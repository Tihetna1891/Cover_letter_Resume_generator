import os
import httpx
from fastapi import FastAPI, HTTPException, Form, Query
from fastapi.responses import JSONResponse, FileResponse
from celery.result import AsyncResult
from tasks import celery_app, generation_pipeline_task, generate_resume, generate_followup_email
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import Literal

load_dotenv()

app = FastAPI(
    title="Job Application Generator API",
    description="Integrated API for Cover Letter, Resume, and Follow-up Email Generation",
    version="1.0.0"
)

class ResumeRequest(BaseModel):
    user_id: str
    template: Literal["modern", "classic"] = "modern"

class JobApplicationRequest(BaseModel):
    user_id: str
    job_id: str

# Configuration
PROFILE_API = "https://sandbox.appleazy.com/api/v1/user"
JOB_API = "https://server.appleazy.com/api/v1/job-listing"
HOST_URL = "https://your-deployed-domain.com"

async def fetch_job_description(job_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{JOB_API}/{job_id}")
        if response.status_code != 200:
            raise HTTPException(502, "Failed to fetch job details")
        return response.json().get("description", "")

async def fetch_profile_cv(user_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{PROFILE_API}/get-profile/{user_id}",
            params={"field": "userId"},
            timeout=30.0
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("resume") or not data["resume"].get("content"):
            raise HTTPException(400, "No resume found in profile")
        return data["resume"]["content"]

@app.post("/generate-cover-letter")
async def generate_cover_letter(
    job_description: str = Form(...),
    user_id: str = Form(...),
    tone: str = Form("Professional"),
    skills: str = Form(""),
    experience: str = Form("")
):
    try:
        task = generation_pipeline_task.apply_async(
            args=[job_description, user_id, tone, skills, experience, "cover_letter"]
        )
        return JSONResponse(
            status_code=202,
            content={
                "status": "processing",
                "cover_letter_url": f"/documents/{task.id}",
                "tracking_url": f"/api/status/{task.id}"
            }
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Profile API error: {str(e)}")
    except Exception as e:
        raise HTTPException(500, f"Generation failed: {str(e)}")

@app.post("/generate-resume", status_code=status.HTTP_202_ACCEPTED)
async def trigger_resume_generation(
    user_id: str = Form(...),
    template: str = Form("modern"),
    job_description: str = Form("")
):
    if not job_description:
        job_description = """Looking for a skilled developer with experience in:
        - Python programming
        - FastAPI framework
        - Celery task queues
        Competitive salary and benefits package."""
    
    task = generate_resume.apply_async(
        args=[user_id, template, job_description]
    )
    return {
        "task_id": task.id,
        "status_check": f"/tasks/status/{task.id}"
    }

@app.post("/generate-followup", status_code=status.HTTP_202_ACCEPTED)
async def trigger_followup_email(request: JobApplicationRequest):
    task = generate_followup_email.apply_async(
        args=[request.user_id, request.job_id]
    )
    return {
        "task_id": task.id,
        "status_check": f"/tasks/status/{task.id}"
    }

@app.get("/documents/{task_id}")
async def get_document(task_id: str):
    task_result = AsyncResult(task_id, app=celery_app)
    
    if not task_result.ready():
        raise HTTPException(
            status_code=202,
            detail={
                "status": "processing",
                "message": "Document generation in progress",
                "check_again_at": f"/documents/{task_id}"
            }
        )
    
    if task_result.failed():
        error_msg = str(task_result.result)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "failed",
                "error": error_msg,
                "retry_url": f"/api/generate/cover-letter"
            }
        )
    
    return {
        "status": "success",
        "generated_at": task_result.result.get("generated_at"),
        "content": task_result.result.get("content", ""),
        "pdf_url": task_result.result.get("pdf_url", ""),
        "text_url": task_result.result.get("text_url", ""),
        "job_description_preview": task_result.result.get("job_description", "")[:100] + "..."
    }

@app.get("/tasks/status/{task_id}")
async def get_task_status(task_id: str):
    task = AsyncResult(task_id, app=celery_app)
    
    if task.failed():
        return {
            "status": "failed",
            "error": str(task.result)
        }
    
    return {
        "ready": task.ready(),
        "successful": task.successful(),
        "result": task.result if task.ready() else None
    }

@app.get("/documents/{task_id}/download")
async def download_document(task_id: str):
    task = AsyncResult(task_id, app=celery_app)
    if not task.ready():
        raise HTTPException(
            status_code=status.HTTP_425_TOO_EARLY,
            detail="Document not ready yet"
        )
    
    pdf_bytes = base64.b64decode(task_result.get("pdf_content", "")) if task.result.get("pdf_content") else None
    if pdf_bytes:
        return FileResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            filename=f"document_{task_id}.pdf"
        )
    raise HTTPException(status_code=404, detail="No PDF available")

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "services": {
            "celery": celery_app.control.ping() != [],
            "redis": True
        }
    }

@app.get("/openapi.json")
async def openapi_spec():
    from fastapi.openapi.utils import get_openapi
    return get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes
    )