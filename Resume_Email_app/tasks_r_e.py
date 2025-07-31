from celery import Celery
from api_client import APIClient, AIService
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
def generate_resume(self, user_id: str, template: str = "modern", job_description: str = ""):
    """Synchronous Celery task with proper async handling"""
    try:
        # Initialize API client
        api_client = APIClient()
        
        # Create event loop for async operations
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Get profile data
            profile = loop.run_until_complete(api_client.get_user_profile(user_id))
            
            # Process resume content
            raw_text = base64.b64decode(profile['resume']['content']).decode('utf-8')
            
            # Enhance with LLM if available
            if os.getenv("GEMINI_API_KEY"):
                enhanced_text = AIService().enhance_resume_text(raw_text, job_description)
            else:
                enhanced_text = raw_text
            
            # Generate LaTeX content
            latex_content = render_resume(template, {
                **profile,
                "enhanced_text": enhanced_text
            })
            
            # Create temporary directory
            temp_dir = Path("tmp/resumes")
            temp_dir.mkdir(parents=True, exist_ok=True)  # Changed this line
            
            tex_path = temp_dir / f"resume_{self.request.id}.tex"
            tex_path.write_text(latex_content)
            
            # Compile PDF
            subprocess.run([
                "pdflatex",
                "-interaction=nonstopmode",
                f"-output-directory={temp_dir}",
                str(tex_path)
            ], check=True)
            
            # Read and encode PDF
            pdf_bytes = (tex_path.with_suffix(".pdf")).read_bytes()
            return base64.b64encode(pdf_bytes).decode()
            
        finally:
            loop.close()
            
    except Exception as e:
        retry_countdown = min(300, 60 * (2 ** self.request.retries))
        logger.error(f"Retrying in {retry_countdown}s. Error: {str(e)}")
        self.retry(exc=e, countdown=retry_countdown)
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