import os
import json
from io import StringIO
from celery import Celery
from pdfminer.high_level import extract_text_to_fp
from io import BytesIO, StringIO
from pdfminer.layout import LAParams
import google.generativeai as genai
import base64 
from datetime import datetime  # For timestamps



from dotenv import load_dotenv
load_dotenv()

# --- Celery Configuration ---
# Assumes Redis is running locally on the default port.
# celery_app = Celery('tasks', 
#                     broker='redis://localhost:6379/0', 
#                     backend='redis://localhost:6379/0')
celery_app = Celery(
    'tasks',
    broker='redis://192.168.48.1:6379/0',
    backend='redis://192.168.48.1:6379/0',  # Must match broker URL exactly
    # include=['tasks']
    result_extended=True,
    result_backend_transport_options={
        'visibility_timeout': 3600  # Keep results for 1 hour
    }
)
celery_app.conf.result_extended = True
celery_app.conf.broker_connection_retry_on_startup = True
# celery_app.conf.update(
    
#     result_extended=True,
#     result_backend_transport_options={
#         'retry_policy': {
#             'timeout': 5.0
#         }
#     },
#     task_track_started=True,
#     broker_connection_retry_on_startup=True
# )
# --- LLM Configuration ---
# The API key is read from the environment variable set before running the app.
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

# --- Agent Implementations ---

# def parse_cv_pdf(cv_bytes: bytes) -> str:
#     """
#     Parses the text from a PDF file in memory.
#     Corresponds to the 'CV Parser Agent'[cite: 9].
#     """
#     output_string = StringIO()
#     extract_text_to_fp(BytesIO(cv_bytes), output_string, laparams=LAParams(),
#                    output_type='text', codec="")
#     # extract_text_to_fp(StringIO(cv_bytes.decode('latin-1')), output_string, laparams=LAParams(),
#     #                    output_type='text', codec="")
#     return output_string.getvalue()
def parse_cv_content(cv_content: str) -> str:
    """Handles both PDF and text CVs with validation"""
    try:
        cv_bytes = base64.b64decode(cv_content)
        
        # Check if it's a PDF
        if cv_bytes.startswith(b'%PDF-'):
            output = StringIO()
            extract_text_to_fp(BytesIO(cv_bytes), output, laparams=LAParams())
            return output.getvalue()
            
        # Fallback: Try text decoding
        try:
            return cv_bytes.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError("Invalid content - neither PDF nor text")
            
    except Exception as e:
        raise ValueError(f"CV processing failed: {str(e)}")

def rewrite_cv_for_clarity(cv_text: str, jd_text: str) -> dict:
    """
    Uses an LLM to extract structured data from the CV text.
    Corresponds to the 'CV Rewriter Agent'[cite: 9].
    """
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    Analyze the following resume and extract key information into a structured JSON format.
    Focus on details relevant to this job description: {jd_text[:500]}...

    Resume Text:
    ---
    {cv_text}
    ---

    Output only a JSON object with keys: "name", "contact", "summary", "experience", and "skills".
    """
    try:
        response = model.generate_content(prompt)
        # A simple way to clean and parse the JSON from the LLM response
        json_str = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
         # Fallback for when the LLM response isn't valid JSON
        return {"error": "Failed to parse CV into JSON", "raw_cv": cv_text}


def generate_cover_letter_text(cv_json: dict, jd_text: str, tone: str) -> str:
    """
    Generates the cover letter text using the structured CV data.
    Corresponds to the 'Cover Letter Writer'[cite: 9].
    """
    model = genai.GenerativeModel('gemini-1.5-flash') # As specified in the tech stack [cite: 27]
    prompt = f"""
    You are a professional career coach writing a compelling cover letter.

    **Instructions:**
    - The tone must be **{tone}**.
    - Use the candidate's JSON resume and the full job description below.
    - Highlight 2-3 key qualifications that directly match the job description.
    - Express enthusiasm for the role and end with a clear call to action.
    - Do not invent any information not present in the resume data.
    - Output ONLY the cover letter text.

    **Job Description:**
    ---
    {jd_text}
    ---

    **Candidate's Resume Data (JSON):**
    ---
    {json.dumps(cv_json, indent=2)}
    ---

    **Cover Letter:**
    """
    response = model.generate_content(prompt)
    return response.text

# --- Main Celery Task ---
@celery_app.task(bind=True, max_retries=3, time_limit=300, acks_late=True)
def generation_pipeline_task(self, job_description: str, cv_content: str, tone: str):
    """
    Ultimate cover letter generation pipeline with:
    - Multi-format CV support (PDF, text, docx)
    - Comprehensive error handling
    - Detailed debugging
    - Automatic fallbacks
    """
    try:
        # Debug setup
        debug_dir = "cv_debug"
        os.makedirs(debug_dir, exist_ok=True)
        task_id = self.request.id
        debug_path = os.path.join(debug_dir, f"cv_{task_id}.bin")
        
        # 1. Decode and validate input
        self.update_state(state='PROGRESS', meta={'stage': 'validating_input'})
        try:
            cv_bytes = base64.b64decode(cv_content)
            with open(debug_path, "wb") as f:
                f.write(cv_bytes)
                
            if len(cv_bytes) == 0:
                raise ValueError("Empty CV content received")
        except Exception as e:
            raise ValueError(f"CV content decoding failed: {str(e)}")

        # 2. Determine content type and extract text
        self.update_state(state='PROGRESS', meta={'stage': 'extracting_text'})
        cv_text = extract_text_from_cv(cv_bytes, debug_dir, task_id)
        
        # 3. Analyze CV content
        self.update_state(state='PROGRESS', meta={'stage': 'analyzing_cv'})
        cv_json = analyze_cv_content(cv_text, job_description)
        
        # 4. Generate cover letter
        self.update_state(state='PROGRESS', meta={'stage': 'generating_letter'})
        cover_letter = generate_cover_letter_text(cv_json, job_description, tone)
        
        return {
            "status": "success",
            "cover_letter": cover_letter,
            "debug_path": debug_path,
            "generated_at": datetime.utcnow().isoformat()
        }

    except Exception as e:
        error_msg = f"Task {task_id} failed: {str(e)}"
        with open(os.path.join(debug_dir, f"error_{task_id}.log"), "w") as f:
            f.write(f"Error: {error_msg}\n")
            f.write(f"Job Description: {job_description[:200]}\n")
            f.write(f"CV Content Type: {type(cv_content)}\n")
            f.write(f"CV Content Sample: {cv_content[:200]}\n")
        
        if self.request.retries == self.max_retries:
            return {
                "status": "failed",
                "error": error_msg,
                "debug_path": debug_path
            }
        raise self.retry(exc=e, countdown=min(300, 60 * (2 ** self.request.retries)))

def extract_text_from_cv(cv_bytes: bytes, debug_dir: str, task_id: str) -> str:
    """Universal CV text extractor with multiple fallbacks"""
    try:
        # Try PDF first
        if cv_bytes.startswith(b'%PDF-'):
            output = StringIO()
            extract_text_to_fp(BytesIO(cv_bytes), output, laparams=LAParams())
            return output.getvalue()
        
        # Try text decoding
        try:
            return cv_bytes.decode('utf-8')
        except UnicodeDecodeError:
            pass
            
        # Try other encodings
        for encoding in ['latin-1', 'utf-16', 'ascii']:
            try:
                return cv_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
                
        # Final fallback - save raw bytes
        with open(os.path.join(debug_dir, f"raw_{task_id}.bin"), "wb") as f:
            f.write(cv_bytes)
        raise ValueError("Unsupported CV format - saved raw file for inspection")
        
    except Exception as e:
        raise ValueError(f"Text extraction failed: {str(e)}")

def analyze_cv_content(cv_text: str, job_description: str) -> dict:
    """CV analysis with improved error handling"""
    try:
        cv_json = rewrite_cv_for_clarity(cv_text, job_description)
        if "error" in cv_json:
            raise ValueError(cv_json["error"])
        return cv_json
    except Exception as e:
        raise ValueError(f"CV analysis failed: {str(e)}")
# def generation_pipeline_task(self, job_description: str, cv_content: str, tone: str):
#     """Enhanced task with progress tracking"""
#     self.update_state(state='PROGRESS', meta={'stage': 'parsing_cv'})
    
#     # Step 1: Decode base64 CV content
#     cv_bytes = base64.b64decode(cv_content)
    
#     # Step 2: Parse CV
#     cv_text = parse_cv_pdf(cv_bytes)
#     self.update_state(state='PROGRESS', meta={'stage': 'extracting_details'})
    
#     # Step 3: Structure CV data
#     cv_json = rewrite_cv_for_clarity(cv_text, job_description)
#     if "error" in cv_json:
#         raise Exception(cv_json["error"])
    
#     # Step 4: Generate cover letter
#     self.update_state(state='PROGRESS', meta={'stage': 'generating_letter'})
#     cover_letter = generate_cover_letter_text(cv_json, job_description, tone)
    
#     return {
#         "job_id": self.request.id,
#         "cover_letter": cover_letter,
#         "generated_at": datetime.utcnow().isoformat()
#     }

# @celery_app.task(name="generation_pipeline_task")
# def generation_pipeline_task(job_description: str, cv_bytes: bytes, tone: str) -> dict:
#     """
#     The full pipeline for generating a cover letter[cite: 14].
#     """
#     # Step 1: Parse the uploaded CV PDF [cite: 16]
#     cv_text = parse_cv_pdf(cv_bytes)

#     # Step 2: Rewrite CV text into a structured format [cite: 17]
#     rewritten_cv_json = rewrite_cv_for_clarity(cv_text, job_description)
#     if "error" in rewritten_cv_json:
#         # If parsing fails, we can't proceed.
#         raise Exception(rewritten_cv_json["error"])

#     # Step 3: Generate the cover letter [cite: 18]
#     cover_letter = generate_cover_letter_text(rewritten_cv_json, job_description, tone)

#     # In the full implementation, you would also generate the resume PDF and upload to S3 here.
#     return {
#         "cover_letter": cover_letter
#     }