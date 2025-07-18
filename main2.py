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
            params={"field": "userId"}
        )
        if response.status_code != 200:
            raise HTTPException(502, "Failed to fetch profile")
        data = response.json()
        if not data.get("cv"):
            raise HTTPException(400, "No CV found in profile")
        return data["cv"]["content"]  # Base64 encoded CV

@app.post("/generate-cover-letter")
async def generate_cover_letter(
    job_id: str = Form(...),
    user_id: str = Form(...),
    tone: str = Form("Professional")
):
    try:
        # Fetch required data
        job_description = await fetch_job_description(job_id)
        cv_content = await fetch_profile_cv(user_id)
        
        # Start generation task
        task = generation_pipeline_task.apply_async(
            args=[job_description, cv_content, tone]
        )
        
        return {
            "status": "processing",
            "cover_letter_url": f"{HOST_URL}/cover-letters/{task.id}",
            "tracking_url": f"{HOST_URL}/status/{task.id}"
        }
    
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(500, f"Generation failed: {str(e)}")

@app.get("/cover-letters/{task_id}")
async def get_cover_letter(task_id: str):
    task_result = AsyncResult(task_id, app=celery_app)
    
    if not task_result.ready():
        raise HTTPException(202, "Generation in progress")
    
    if task_result.failed():
        raise HTTPException(500, "Generation failed")
    
    return {
        "job_id": task_result.result.get("job_id"),
        "user_id": task_result.result.get("user_id"),
        "cover_letter": task_result.result["cover_letter"]
    }

@app.get("/status/{task_id}")
async def get_status(task_id: str):
    task_result = AsyncResult(task_id, app=celery_app)
    return {
        "status": task_result.state,
        "ready": task_result.ready(),
        "result_url": f"{HOST_URL}/cover-letters/{task_id}" if task_result.ready() else None
    }