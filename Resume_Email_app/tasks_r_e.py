from celery import Celery
from services.api_client import APIClient, AIService
from services.template_render import render_resume, render_email
import subprocess
from pathlib import Path
import base64
import os
from dotenv import load_dotenv


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
async def generate_resume(self, user_id: str, template: str = "modern", job_description: str = ""):
    """Synchronous task with smart error handling"""
    try:
        api_client = APIClient()
        
        # Get profile data (sync wrapper around async client)
        profile = await api_client.get_user_profile(user_id)

        # Get raw resume text
        raw_text = base64.b64decode(profile['resume']['content']).decode('utf-8')
        
        # Enhance with LLM (only if Gemini API key exists)
        if os.getenv("GEMINI_API_KEY"):
            enhanced_text = AIService().enhance_resume_text(raw_text, job_description)
        else:
            enhanced_text = raw_text
        
        # PDF generation logic
        # Generate LaTeX with enhanced content
        latex_content = render_resume(template, {
            **profile,
            "enhanced_text": enhanced_text
        })
        # latex_content = f"""
        # \\documentclass{{article}}
        # \\begin{{document}}
        # \\section*{{{profile['name']}}}
        # \\subsection*{{Experience}}
        # {''.join(f"\\textbf{{{exp['title']}}} at {exp['company']}\\\\" for exp in profile['experience'])}
        # \\end{{document}}
        # """
        
        temp_dir = Path("/tmp/resumes")
        temp_dir.mkdir(exist_ok=True)
        tex_path = temp_dir / f"resume_{self.request.id}.tex"
        tex_path.write_text(latex_content)
        
        subprocess.run([
            "pdflatex",
            "-interaction=nonstopmode",
            f"-output-directory={temp_dir}",
            str(tex_path)
        ], check=True)
        
        return base64.b64encode((tex_path.with_suffix(".pdf")).read_bytes()).decode()
        
    except Exception as e:
        self.retry(exc=e, countdown=min(300, 60 * (2 ** self.request.retries)))
# async def generate_resume(self, user_id: str, template: str = "modern"):
#     """Generate resume from user profile"""
#     try:
#         # 1. Fetch user data
#         profile = await api_client.get_user_profile(user_id)
        
#         # 2. Prepare template context
#         context = {
#             "name": profile.get("name", ""),
#             "email": profile.get("email", ""),
#             "phone": profile.get("phone", ""),
#             "experience": profile.get("experience", []),
#             "education": profile.get("education", []),
#             "skills": profile.get("skills", [])
#         }
        
#         # 3. Render LaTeX
#         latex_content = render_resume(template, context)
        
#         # 4. Compile to PDF
#         temp_dir = Path("/tmp/resumes")
#         temp_dir.mkdir(exist_ok=True)
#         tex_path = temp_dir / f"resume_{self.request.id}.tex"
#         tex_path.write_text(latex_content)
        
#         subprocess.run([
#             "pdflatex",
#             "-interaction=nonstopmode",
#             f"-output-directory={temp_dir}",
#             str(tex_path)
#         ], check=True)
        
#         pdf_path = tex_path.with_suffix(".pdf")
#         return base64.b64encode(pdf_path.read_bytes()).decode()
        
#     except Exception as e:
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