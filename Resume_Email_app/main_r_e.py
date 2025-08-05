from fastapi import FastAPI, HTTPException, status, Form
from fastapi.responses import JSONResponse, FileResponse
from celery.result import AsyncResult
from tasks_r_e import celery_app, generate_resume, generate_job_application,generate_followup_email
import base64
import io
from pydantic import BaseModel
from typing import Literal

app = FastAPI(
    title="Resume & Email Generator API  (JSON)" ,
    description="Integrated with Profile and Job Listing APIs",
    version="1.0.0"
)

class ResumeRequest(BaseModel):
    user_id: str
    template: Literal["modern", "classic"] = "modern"

class JobApplicationRequest(BaseModel):
    user_id: str
    job_id: str

@app.post("/generate-resume", status_code=status.HTTP_202_ACCEPTED)
async def trigger_resume_generation(
    user_id: str = Form(...),
    template: str = Form("modern"),
    job_description: str = Form("")
):
    """Endpoint to start resume generation with job description fallback"""
    if not job_description:
        job_description = """Looking for a skilled developer with experience in:
        - Python programming
        - FastAPI framework
        - Celery task queues
        Competitive salary and benefits package."""
    
    task = generate_resume.delay(
        user_id=user_id,
        template=template,
        job_description=job_description
    )
    return {
        "task_id": task.id,
        "status_check": f"/tasks/status/{task.id}"
        # "download": f"/resumes/{task.id}"
    }
@app.get("/resumes/{task_id}")
async def get_resume(task_id: str):
    """Retrieve generated resume data"""
    task = AsyncResult(task_id, app=celery_app)
    
    if not task.ready():
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail="Processing in progress"
        )
    
    if task.failed():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(task.result)
        )
    
    return task.result
# async def trigger_resume_generation(
#     user_id: str = Form(...),
#     template: str = Form("modern"),
#     job_description: str = Form("")
# ):
#     if template not in ["modern", "classic"]:
#         raise HTTPException(
#             status_code=400,
#             detail="Invalid template. Choose 'modern' or 'classic'"
#         )
#     task = generate_resume.delay(
#         user_id=user_id,
#         template=template,
#         job_description=job_description
#     )
#     return {"task_id": task.id}
# async def trigger_resume_generation(request: ResumeRequest):
#     """Endpoint to start resume generation"""
#     task = generate_resume.delay(request.user_id, request.template)
#     return {"task_id": task.id}
#     # return JSONResponse(
#     #     content={
#     #         "status": "processing",
#     #         "task_id": task.id,
#     #         "check_status": f"/tasks/status/{task.id}",
#     #         "download": f"/resumes/{task.id}"
#         # }
#     # )

@app.post("/generate-application", status_code=status.HTTP_202_ACCEPTED)
async def trigger_application_email(request: JobApplicationRequest):
    """Endpoint to start application email generation"""
    task = generate_job_application.delay(request.user_id, request.job_id)
    return {
        "task_id": task.id,
        "status_check": f"/tasks/status/{task.id}"
    }

@app.get("/tasks/status/{task_id}")
async def get_task_status(task_id: str):
    """Check status of a background task"""
    task = AsyncResult(task_id, app=celery_app)
    
    if task.failed():
        return {
            "status": "failed",
            "error": str(task.result)
        }
    
    return {
        # "status": task.status,
        "ready": task.ready(),
        "successful": task.successful(),
        "result": task.result if task.ready() else None
    }

@app.get("/resumes/{task_id}")
async def download_resume(task_id: str):
    """Download generated resume"""
    task = AsyncResult(task_id, app=celery_app)
    if not task.ready():
        raise HTTPException(
            status_code=status.HTTP_425_TOO_EARLY,
            detail="Resume not ready yet"
        )
    
    pdf_bytes = base64.b64decode(task.result)
    return FileResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        filename=f"resume_{task_id}.pdf"
    )

@app.get("/health")
async def health_check():
    """System health endpoint"""
    return {
        "status": "healthy",
        "services": {
            "celery": celery_app.control.ping() != [],
            "redis": True  # Add actual check if needed
        }
    }
@app.post("/generate-followup")
async def trigger_email_generation(user_id: str, job_id: str):
    try:
        task = generate_followup_email.delay(user_id=user_id, job_id=job_id)
        return {
            "task_id": task.id,
            "status_check": f"/tasks/{task.id}"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
# List all available routes for testing
@app.get("/routes")
async def list_routes():
    return [{"path": route.path, "methods": route.methods} for route in app.routes]