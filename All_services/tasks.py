import os
import json
from io import StringIO, BytesIO
from celery import Celery
from pdfminer.high_level import extract_text_to_fp
from pdfminer.layout import LAParams
import base64
from datetime import datetime
from llm_service import LLMService
from minio import Minio
from minio.error import MinioException
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from api_client import APIClient, AIService
from resume_parser import ResumeParser
from typing import Dict, Optional
import asyncio
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)

load_dotenv()

celery_app = Celery(
    'tasks',
    broker=os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0'),
    backend=os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0'),
    result_extended=True,
    result_backend_transport_options={'visibility_timeout': 3600}
)
celery_app.conf.broker_connection_retry_on_startup = True

llm_service = LLMService(os.environ["OPENAI_API_KEY"])
api_client = APIClient()

# MinIO Configuration (optional, falls back if not configured)
minio_client = None
if os.getenv("MINIO_ENDPOINT") and os.getenv("MINIO_ACCESS_KEY") and os.getenv("MINIO_SECRET_KEY"):
    minio_client = Minio(
        os.getenv("MINIO_ENDPOINT"),
        access_key=os.getenv("MINIO_ACCESS_KEY"),
        secret_key=os.getenv("MINIO_SECRET_KEY"),
        secure=False  # Set to True if using HTTPS
    )

def parse_cv_content(cv_content: str) -> str:
    try:
        cv_bytes = base64.b64decode(cv_content)
        if cv_bytes.startswith(b'%PDF-'):
            output = StringIO()
            extract_text_to_fp(BytesIO(cv_bytes), output, laparams=LAParams())
            return output.getvalue()
        try:
            return cv_bytes.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError("Invalid content - neither PDF nor text")
    except Exception as e:
        raise ValueError(f"CV processing failed: {str(e)}")

def rewrite_cv_for_clarity(cv_text: str, jd_text: str, skills: str = "", experience: str = "") -> dict:
    prompt = f"""
    Analyze the following resume and extract key information into a structured JSON format.
    Focus on details relevant to this job description: {jd_text[:500]}...
    Additional skills: {skills}, Additional experience: {experience}

    Resume Text:
    ---
    {cv_text}
    ---

    Output only a JSON object with keys: "name", "contact", "summary", "experience", and "skills".
    """
    try:
        response = llm_service.generate_text(prompt, tone="professional")
        return json.loads(response)
    except (json.JSONDecodeError, ValueError):
        return {"error": "Failed to parse CV into JSON", "raw_cv": cv_text}

def generate_letter_text(cv_json: dict, jd_text: str, tone: str, skills: str = "", experience: str = "", doc_type: str = "cover_letter") -> str:
    if doc_type == "cover_letter":
        prompt = f"""
        You are a professional career coach writing a compelling cover letter.

        Tone: {tone}.
        Use the candidate's JSON resume and the full job description below.
        Highlight 2-3 key qualifications that directly match the job description, including additional skills: {skills} and experience: {experience}.
        Express enthusiasm for the role and end with a clear call to action.
        Use placeholders [Your Name], [Company Name] for user info to be replaced later.

        Job Description:
        ---
        {jd_text}
        ---

        Candidate's Resume Data (JSON):
        ---
        {json.dumps(cv_json, indent=2)}
        ---

        Cover Letter:
        """
    else:  # follow-up email
        prompt = f"""
        You are a professional writing a concise follow-up email.

        Tone: {tone}.
        Use the candidate's JSON resume and job details below.
        Mention the application date as today's date and align skills with the job, including additional skills: {skills} and experience: {experience}.
        End with a polite call to action.

        Job Details:
        ---
        {jd_text}
        ---

        Candidate's Resume Data (JSON):
        ---
        {json.dumps(cv_json, indent=2)}
        ---

        Email Body:
        """
    response = llm_service.generate_text(prompt, tone=tone)
    if doc_type == "cover_letter":
        letter = response.replace("[Your Name]", cv_json.get("name", "Candidate Name"))
        letter = letter.replace("[Company Name]", jd_text.split("Company:")[1].split("\n")[0].strip() if "Company:" in jd_text else "Company Name")
    else:
        letter = response.replace("[Your Name]", cv_json.get("name", "Candidate Name"))
    return letter

def convert_to_pdf(text: str) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    flowables = [Paragraph(text, styles['Normal'])]
    doc.build(flowables)
    return buffer.getvalue()

def store_in_object_storage(content: bytes, filename: str, content_type: str) -> str:
    if not minio_client:
        return ""
    bucket_name = os.getenv("MINIO_BUCKET_NAME", "job-docs")
    try:
        minio_client.make_bucket(bucket_name) if not minio_client.bucket_exists(bucket_name) else None
        minio_client.put_object(bucket_name, filename, BytesIO(content), length=len(content), content_type=content_type)
        return f"http://{os.getenv('MINIO_ENDPOINT')}/{bucket_name}/{filename}"
    except MinioException as e:
        logger.error(f"MinIO storage failed: {str(e)}")
        return ""

@celery_app.task(bind=True, max_retries=3, time_limit=300, acks_late=True)
def generation_pipeline_task(self, job_description: str, user_id: str, tone: str, skills: str = "", experience: str = "", doc_type: str = "cover_letter"):
    try:
        debug_dir = "cv_debug"
        os.makedirs(debug_dir, exist_ok=True)
        task_id = self.request.id
        debug_path = os.path.join(debug_dir, f"cv_{task_id}.bin")
        
        self.update_state(state='PROGRESS', meta={'stage': 'validating_input'})
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        profile = loop.run_until_complete(api_client.get_user_profile(user_id))
        cv_content = profile.get("resume", {}).get("content", "base64_encoded_cv_placeholder")
        cv_bytes = base64.b64decode(cv_content)
        with open(debug_path, "wb") as f:
            f.write(cv_bytes)
        if len(cv_bytes) == 0:
            raise ValueError("Empty CV content received")

        self.update_state(state='PROGRESS', meta={'stage': 'extracting_text'})
        cv_text = parse_cv_content(cv_content)
        
        self.update_state(state='PROGRESS', meta={'stage': 'analyzing_cv'})
        cv_json = rewrite_cv_for_clarity(cv_text, job_description, skills, experience)
        
        self.update_state(state='PROGRESS', meta={'stage': 'generating_document'})
        content = generate_letter_text(cv_json, job_description, tone, skills, experience, doc_type)
        
        pdf_content = convert_to_pdf(content)
        text_content = content.encode('utf-8')
        
        pdf_url = store_in_object_storage(pdf_content, f"{doc_type}_{task_id}.pdf", "application/pdf")
        text_url = store_in_object_storage(text_content, f"{doc_type}_{task_id}.txt", "text/plain")
        
        return {
            "status": "success",
            "content": content,
            "pdf_url": pdf_url,
            "text_url": text_url,
            "pdf_content": base64.b64encode(pdf_content).decode() if pdf_content else "",
            "debug_path": debug_path,
            "generated_at": datetime.utcnow().isoformat(),
            "job_description": job_description
        }
    except Exception as e:
        error_msg = f"Task {task_id} failed: {str(e)}"
        with open(os.path.join(debug_dir, f"error_{task_id}.log"), "w") as f:
            f.write(f"Error: {error_msg}\n")
        if self.request.retries == self.max_retries:
            return {"status": "failed", "error": error_msg, "debug_path": debug_path}
        raise self.retry(exc=e, countdown=min(300, 60 * (2 ** self.request.retries)))
    finally:
        loop.close()

@celery_app.task(bind=True, max_retries=3)
def generate_resume(self, user_id: str, template: str = "modern", job_description: str = ""):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            profile = loop.run_until_complete(api_client.get_user_profile(user_id))
            if not profile:
                raise ValueError("Profile not found")

            resume_text = None
            if profile.get('resume', {}).get('content'):
                raw_content = base64.b64decode(profile['resume']['content'])
                if raw_content.startswith(b'%PDF-'):
                    resume_text = extract_text(BytesIO(raw_content))
                else:
                    try:
                        resume_text = raw_content.decode('utf-8')
                    except UnicodeDecodeError:
                        resume_text = "[Unsupported binary content]"

            if resume_text and resume_text != "[Unsupported binary content]":
                parser = ResumeParser()
                contact = parser.parse_contact(resume_text)
                profile.update({
                    "name": profile.get("name") or parser.parse_name(resume_text),
                    "email": contact["email"] or profile.get("email", ""),
                    "phone": contact["phone"] or profile.get("phone"),
                    "location": profile.get("location") or parser.parse_location(resume_text),
                    "education": profile.get("education") or parser.parse_education(resume_text),
                    "experience": profile.get("experience") or parser.parse_experience(resume_text)
                })

            enhanced_content = resume_text
            if os.getenv("OPENAI_API_KEY") and job_description and resume_text and resume_text != "[Unsupported binary content]":
                try:
                    enhanced_content = AIService().enhance_resume_text(resume_text, job_description)
                except Exception as ai_error:
                    logger.warning(f"AI enhancement failed: {ai_error}")
                    enhanced_content = resume_text

            result = {
                "metadata": {
                    "user_id": user_id,
                    "template": template,
                    "generated_at": datetime.utcnow().isoformat(),
                    "source": "PDF" if raw_content.startswith(b'%PDF-') else "Text"
                },
                "profile": {
                    "name": profile.get("name"),
                    "contact": {
                        "email": profile.get("email"),
                        "phone": profile.get("phone"),
                        "location": profile.get("location")
                    },
                    "education": profile.get("education", []),
                    "experience": profile.get("experience", []),
                    "skills": {
                        "technical": profile.get("technical_skills", []),
                        "professional": profile.get("professional_skills", []),
                        "languages": profile.get("languages", [])
                    }
                },
                "content": {
                    "original": resume_text,
                    "enhanced": enhanced_content,
                    "job_description": job_description
                }
            }
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"Resume generation failed: {str(e)}", exc_info=True)
        self.retry(exc=e, countdown=min(60 * (2 ** self.request.retries), 300))
@celery_app.task(bind=True, max_retries=3, time_limit=45, soft_time_limit=40)
def generate_followup_email(self, user_id: str, job_id: str) -> Dict:
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            profile = loop.run_until_complete(api_client.get_user_profile(user_id))
            job_description = loop.run_until_complete(fetch_job_description(job_id))
            if not profile or not job_description:
                raise ValueError("Profile or job data not found")

            cv_content = profile.get("resume", {}).get("content", "base64_encoded_cv_placeholder")
            cv_text = parse_cv_content(cv_content)
            cv_json = rewrite_cv_for_clarity(cv_text, job_description)

            content = generate_letter_text(cv_json, job_description, "Professional", doc_type="follow_up_email")
            pdf_content = convert_to_pdf(content)
            text_content = content.encode('utf-8')
            
            pdf_url = store_in_object_storage(pdf_content, f"followup_{self.request.id}.pdf", "application/pdf")
            text_url = store_in_object_storage(text_content, f"followup_{self.request.id}.txt", "text/plain")

            return {
                'metadata': {
                    'user_id': user_id,
                    'job_id': job_id,
                    'timestamp': datetime.utcnow().isoformat(),
                    'data_source': "api"
                },
                'email': {
                    'to': job_description.split("Contact:")[1].split("\n")[0].strip() if "Contact:" in job_description else "hiring@company.com",
                    'subject': f"Follow-up: Application for {cv_json.get('name', 'the position')}",
                    'text': content,
                    'html': f"<html><body><p>{content.replace('\n', '<br>')}</p></body></html>",
                    'pdf_url': pdf_url,
                    'text_url': text_url
                }
            }
        except asyncio.TimeoutError:
            raise self.retry(exc=TimeoutError("Operation timed out"), countdown=60)
        except Exception as e:
            logger.error(f"Processing error: {str(e)}")
            raise self.retry(exc=e, countdown=min(120 * (2 ** self.request.retries), 600))
        finally:
            loop.close()
    except Exception as e:
        raise