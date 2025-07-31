import os
import httpx
from fastapi import FastAPI, HTTPException, Form, Query
from fastapi.responses import JSONResponse
from celery.result import AsyncResult
from tasks import celery_app, generation_pipeline_task
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Cover Letter Generator API")

# Configuration
PROFILE_API = "https://sandbox.appleazy.com/api/v1/user"
JOB_API = "https://server.appleazy.com/api/v1/job-listing"
HOST_URL = "https://your-deployed-domain.com"  # Update with your actual domain

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
        # if response.status_code != 200:
        #     raise HTTPException(502, "Failed to fetch profile")
        response.raise_for_status()
        data = response.json()
        if not data.get("resume")or not data["resume"].get("content"):
            raise HTTPException(400, "No resume found in profile")
        return data["resume"]["content"]  # Base64 encoded CV

@app.post("/generate-cover-letter")
async def generate_cover_letter(
    # job_id: str = Form(...),
    job_description: str = Form(...),
    user_id: str = Form(...),
    tone: str = Form("Professional")
):
    try:
        # Fetch required data
        # I have commented out the line that fetches job description from api since there is no job listing for now
        # job_description = await fetch_job_description(job_id)
        # cv_content = await fetch_profile_cv(user_id)
        
        # Start generation task
        # task = generation_pipeline_task.apply_async(
        #     args=[job_description, cv_content, tone]
        # )
         # Immediately return job ID while processing in background
        task = generation_pipeline_task.apply_async(
            args=[job_description, user_id, tone]  # Pass user_id directly
        )
        
        return JSONResponse(
            status_code=202,
            content={
                "status": "processing",
                "cover_letter_url": f"/cover-letters/{task.id}",
                "tracking_url": f"/api/status/{task.id}"
            }
        )
        
        # return {
        #     "status": "processing",
        #     "cover_letter_url": f"{HOST_URL}/cover-letters/{task.id}",
        #     "tracking_url": f"{HOST_URL}/status/{task.id}"
        # }
    
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Profile API error: {str(e)}")
    except Exception as e:
        raise HTTPException(500, f"Generation failed: {str(e)}")
@app.get("/cover-letters/{task_id}")
async def get_cover_letter(task_id: str):
    task_result = AsyncResult(task_id, app=celery_app)
    
    if not task_result.ready():
        raise HTTPException(
            status_code=202,
            detail={
                "status": "processing",
                "message": "Cover letter generation in progress",
                "check_again_at": f"/cover-letters/{task_id}"
            }
        )
    
    if task_result.failed():
        error_msg = str(task_result.result)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "failed",
                "error": error_msg,
                "retry_url": f"/api/generate/cover-letter"  # Suggest retry
            }
        )
    
    return {
        "status": "success",
        "generated_at": task_result.result.get("generated_at"),
        "cover_letter": task_result.result["cover_letter"],
        "job_description_preview": task_result.result.get("job_description", "")[:100] + "...",
        "download_url": f"/cover-letters/{task_id}/download"  # Optional for PDF
    }
# @app.get("/cover-letters/{task_id}")
# async def get_cover_letter(task_id: str):
#     task_result = AsyncResult(task_id, app=celery_app)
    
#     if not task_result.ready():
#         raise HTTPException(202, "Generation in progress")
    
#     if task_result.failed():
#         raise HTTPException(500, "Generation failed")
    
#     return {
#         "job_id": task_result.result.get("job_id"),
#         "user_id": task_result.result.get("user_id"),
#         "cover_letter": task_result.result["cover_letter"]
#     }

@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    """Proper status checking endpoint"""
    task_result = AsyncResult(task_id, app=celery_app)
    
    if not task_result.ready():
        return {
            "status": "processing",
            "task_id": task_id,
            "check_again_in": "10 seconds"
        }
    
    if task_result.failed():
        return {
            "status": "failed",
            "error": str(task_result.result),
            "task_id": task_id
        }
    
    return {
        "status": "success",
        "task_id": task_id,
        "result": task_result.result,
        "completed_at": task_result.date_done.isoformat() if task_result.date_done else None
    }