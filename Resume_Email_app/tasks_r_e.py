from celery import Celery
from api_client import APIClient, AIService
from services.template_render import render_resume, render_email
import subprocess
from pathlib import Path
import base64
import os
from typing import Dict, Optional
from pathlib import Path
from io import BytesIO
import re
import asyncio
from pdfminer.high_level import extract_text  # Only if keeping PDF text extraction

from jinja2 import Environment, FileSystemLoader, select_autoescape
from resume_parser import ResumeParser  # Add this import

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
import sentry_sdk
from datetime import datetime
import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # You can set it to INFO, WARNING, etc.
# Configure logging to output to console
handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)

load_dotenv()

celery_app = Celery(
    'tasks_r_e',
    broker=os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0'),
    backend=os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
    # task_serializer='json',
    # accept_content=['json'],
    # result_serializer='json',
    # timezone='UTC',
    # enable_utc=True
)
@celery_app.task(bind=True, max_retries=3)
def generate_resume(self, user_id: str, template: str = "modern", job_description: str = ""):
    """Generate resume with AI enhancement and PDF parsing"""
    try:
        api_client = APIClient()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # 1. Fetch profile data
            profile = loop.run_until_complete(api_client.get_user_profile(user_id))
            if not profile:
                raise ValueError("Profile not found")

            # 2. Process resume content
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

            # 3. Enhance profile with parsed resume data
            if resume_text and resume_text != "[Unsupported binary content]":
                parser = ResumeParser()
                contact = parser.parse_contact(resume_text)
                # Update profile with parsed data
                profile.update({
                    "name": profile.get("name") or parser.parse_name(resume_text),
                    "email": contact["email"] or profile.get("email", ""),
                    "phone": contact["phone"] or profile.get("phone"),
                    "location": profile.get("location") or parser.parse_location(resume_text),
                    "education": profile.get("education") or parser.parse_education(resume_text),
                    "experience": profile.get("experience") or parser.parse_experience(resume_text)
                })
                # # Fill missing fields
                # if not profile.get("name"):
                #     profile["name"] = parser.parse_name(resume_text)
                
                # contact_info = parser.parse_contact(resume_text)
                # profile.setdefault("email", contact_info["email"])
                # profile.setdefault("phone", contact_info["phone"])
                
                # if not profile.get("education"):
                #     profile["education"] = parser.parse_education(resume_text)
                
                # if not profile.get("experience"):
                #     profile["experience"] = parser.parse_experience(resume_text)

            # 4. AI Enhancement (if enabled)
            enhanced_content = resume_text
            if (os.getenv("GEMINI_API_KEY") 
                and job_description 
                and resume_text 
                and resume_text != "[Unsupported binary content]"
            ):
                try:
                    enhanced_content = AIService().enhance_resume_text(
                        resume_text, 
                        job_description
                    )
                except Exception as ai_error:
                    logger.warning(f"AI enhancement failed: {ai_error}")
                    enhanced_content = resume_text

            # 5. Prepare final output
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
# def _parse_education(profile: dict) -> list:
#     """Convert education data to template format"""
#     education = []
#     if profile.get("education"):
#         for edu in profile["education"]:
#             education.append({
#                 "institution": edu.get("institution", ""),
#                 "degree": edu.get("degree", ""),
#                 "location": edu.get("location", ""),
#                 "graduation_date": edu.get("graduation_date", ""),
#                 "gpa": edu.get("gpa", "")
#             })
#     return education

# def _parse_experience(profile: dict) -> list:
#     """Convert experience data to template format"""
#     experience = []
#     if profile.get("experience"):
#         for exp in profile["experience"]:
#             experience.append({
#                 "company": exp.get("company", ""),
#                 "position": exp.get("position", ""),
#                 "duration": exp.get("duration", ""),
#                 "location": exp.get("location", ""),
#                 "achievements": exp.get("achievements", []),
#                 "description": exp.get("description", "")
#             })
#     return experience

# def _parse_skills(profile: dict) -> dict:
#     """Organize skills data for template"""
#     return {
#         "technical": profile.get("technical_skills", []),
#         "professional": profile.get("professional_skills", []),
#         "languages": profile.get("languages", [])
#     }
# @celery_app.task(bind=True, max_retries=3)
# def generate_resume(self, user_id: str, template: str = "modern", job_description: str = ""):
#     """Generate resume from user profile using HTML templates"""
#     try:
#         # Initialize API client and event loop
#         api_client = APIClient()
#         loop = asyncio.new_event_loop()
#         asyncio.set_event_loop(loop)
        
#         try:
#             # 1. Get profile data
#             profile = loop.run_until_complete(api_client.get_user_profile(user_id))
#             if not profile.get("name"):
#                 logger.warning(f"Incomplete profile data for user {user_id}")
#             # 2. Process resume content (if PDF parsing is still needed)
#             resume_content = None
#             if profile.get('resume', {}).get('content'):
#                 raw_content = base64.b64decode(profile['resume']['content'])
        
#                 try:
#                     resume_content = raw_content.decode('utf-8')
#                 except UnicodeDecodeError:
#                     if raw_content.startswith(b'%PDF-'):
#                         resume_content = extract_text(BytesIO(raw_content))
#                     else:
#                         resume_content = "[Binary resume content]"
              
            
#             # 3. Enhance with LLM if available
#             enhanced_content = resume_content
#             if os.getenv("GEMINI_API_KEY") and job_description and resume_content and resume_content != "[Binary resume content]":
#                 enhanced_content = AIService().enhance_resume_text(resume_content, job_description)
#             # 4. Prepare structured output
#             result = {
#                 "metadata": {
#                     "user_id": user_id,
#                     "template": template,
#                     "generated_at": datetime.utcnow().isoformat()
#                 },
#                 "profile": {
#                     "name": profile.get("name"),
#                     "contact": {
#                         "email": profile.get("email"),
#                         "phone": profile.get("phone"),
#                         "location": profile.get("location")
#                     },
#                     "education": _parse_education(profile),
#                     "experience": _parse_experience(profile),
#                     "skills": _parse_skills(profile)
#                 },
#                 "content": {
#                     "original": resume_content,
#                     "enhanced": enhanced_content,
#                     "job_description": job_description
#                 }
#             }
            
#             return result
            
            
#         finally:
#             loop.close()
            
#     except Exception as e:
#         logger.error(f"Resume generation failed: {str(e)}", exc_info=True)
#         self.retry(exc=e, countdown=60)

@celery_app.task(bind=True, max_retries=3)
async def generate_job_application(self, user_id: str, job_id: str):
    """Generate job application email"""
    try:
        # 1. Fetch data from both APIs
        profile = await api_client.get_user_profile(user_id)
        job = await api_client.get_job_listing(job_id)
        
        # 2. Prepare email context
        context = {
            "applicant_name": profile.get("name", ""),
            "company_name": job.get("company", ""),
            "job_title": job.get("title", ""),
            "skills": ", ".join(profile.get("skills", [])[:3]),
            "job_description": job.get("description", "")[:200] + "..."
        }
        
        # 3. Render and return email
        return render_email("application", context)
        
    except Exception as e:
        self.retry(exc=e, countdown=30)
# Enhanced mock data
MOCK_JOBS = {
    "python_dev_123": {
        "company": "TechCorp Inc.",
        "title": "Python Developer",
        "contact_email": "jobs@techcorp.com",
        "key_technology": "Django",
        "source": "mock"
    },
    "fallback": {
        "company": "Acme Corp",
        "title": "Software Engineer",
        "contact_email": "hiring@acme.com",
        "key_technology": "Python",
        "source": "mock"
    }
}

class EmailGenerationError(Exception):
    """Custom exception for email generation failures"""
    pass
# Add this helper function
def extract_email_from_text(text: str) -> Optional[str]:
    """Extract first valid email from text"""
    if not text:
        return None
    emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    return emails[0] if emails else None

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def fetch_profile_with_retry(user_id: str) -> Dict:
    """Enhanced profile fetcher with email fallback"""
    try:
        profile = await APIClient().get_user_profile(user_id)
        
        # If email missing but resume exists, try extracting from resume
        if 'email' not in profile and 'resume' in profile:
            try:
                resume_content = base64.b64decode(profile['resume']['content'])
                if resume_content.startswith(b'%PDF-'):
                    text = extract_text(BytesIO(resume_content))
                else:
                    text = resume_content.decode('utf-8', errors='ignore')
                
                if extracted_email := extract_email_from_text(text):
                    profile['email'] = extracted_email
                    logger.info(f"Extracted email from resume: {extracted_email}")
            except Exception as e:
                logger.warning(f"Couldn't extract email from resume: {str(e)}")

        # Final validation
        if not profile.get('name'):
            raise EmailGenerationError("Profile missing required field: name")
        if not profile.get('email'):
            raise EmailGenerationError("No email found in profile or resume")
            
        return profile
    except Exception as e:
        logger.error(f"Profile fetch attempt failed: {str(e)}")
        raise

async def get_job_data(job_id: str) -> Dict:
    """Get job data with circuit breaker pattern"""
    try:
        job = await APIClient().get_job_listing(job_id)
        if not all(k in job for k in ['company', 'title', 'contact_email']):
            raise EmailGenerationError("Job data incomplete")
        return {**job, "source": "api"}
    except Exception as e:
        logger.warning(f"Using mock job data: {str(e)}")
        return {**MOCK_JOBS.get(job_id, MOCK_JOBS["fallback"]), "source": "mock"}

# Update the validate_and_build_context function
def validate_and_build_context(profile: Dict, job: Dict) -> Dict:
    """Safer context builder with detailed validation"""
    try:
        return {
            'applicant': {
                'name': profile['name'],
                'email': profile['email'],  # Now guaranteed to exist
                'phone': profile.get('phone', 'Not provided')
            },
            'job': {
                'company': job['company'],
                'title': job['title'],
                'contact_email': job['contact_email'],
                'technology': job.get('key_technology', 'your technology stack')
            },
            'date': datetime.now().strftime("%B %d, %Y"),
            'source': job.get('source', 'unknown')
        }
    except KeyError as e:
        raise EmailGenerationError(f"Missing required field: {str(e)}")
@celery_app.task(bind=True, max_retries=3, time_limit=45, soft_time_limit=40)
def generate_followup_email(self, user_id: str, job_id: str) -> Dict:
    """Production-grade email generator"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # 1. Parallel data fetching
            profile, job = loop.run_until_complete(
                asyncio.gather(
                    fetch_profile_with_retry(user_id),
                    get_job_data(job_id),
                    return_exceptions=True
                )
            )
            
            # 2. Handle fetch errors
            if isinstance(profile, Exception):
                raise profile
            if isinstance(job, Exception):
                raise job
            
            # 3. Validate and build context
            context = validate_and_build_context(profile, job)
            
            # 4. Generate email
            email_content = f"""
Subject: Follow-up: {context['job']['title']} Application

Dear Hiring Manager,

I'm following up regarding my application for {context['job']['title']} 
at {context['job']['company']} (submitted on {context['date']}).

My skills in {context['job']['technology']} align well with your requirements.

Best regards,
{context['applicant']['name']}
{context['applicant']['email']}
Phone: {context['applicant']['phone']}
""".strip()

            return {
                'metadata': {
                    'user_id': user_id,
                    'job_id': job_id,
                    'timestamp': datetime.utcnow().isoformat(),
                    'data_source': job.get('source', 'unknown')
                },
                'email': {
                    'to': context['job']['contact_email'],
                    'subject': f"Follow-up: {context['job']['title']} Application",
                    'text': email_content,
                    'html': f"""
                    <html>
                        <body>
                            <p>Dear Hiring Manager,</p>
                            <p>I'm following up regarding my application...</p>
                        </body>
                    </html>
                    """
                }
            }
            
        except asyncio.TimeoutError:
            raise self.retry(exc=TimeoutError("Operation timed out"), countdown=60)
        except EmailGenerationError as e:
            logger.error(f"Validation failed: {str(e)}")
            raise  # Don't retry for data issues
        except Exception as e:
            logger.error(f"Processing error: {str(e)}")
            raise self.retry(exc=e, countdown=min(120 * (2 ** self.request.retries), 600))
        finally:
            loop.close()
            
    except Exception as e:
        if os.getenv('SENTRY_DSN'):
            sentry_sdk.capture_exception(e)
        raise