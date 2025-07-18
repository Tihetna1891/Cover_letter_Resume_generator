import os
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from celery.result import AsyncResult
from tasks import celery_app  # make sure celery_app is your Celery instance


from tasks import generation_pipeline_task
from dotenv import load_dotenv
load_dotenv()

# Load your API key from an environment variable for security
if "GEMINI_API_KEY" not in os.environ:
    raise Exception("GEMINI_API_KEY environment variable not set.")

app = FastAPI(title="CV Customizer API")

@app.post("/api/generate/cover-letter")
async def generate_cover_letter(
    job_description: str = Form(...),
    tone: str = Form("Professional"),
    cv_file: UploadFile = File(...)
):
    """
    Accepts a job description and CV to generate a cover letter.
    It enqueues a background task and returns a job_id.
    """
    if cv_file.content_type != 'application/pdf':
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDF is accepted.")

    # Read file content to pass to the background task
    cv_bytes = await cv_file.read()

    # Enqueue the Celery task and get the job ID
    task = generation_pipeline_task.apply_async(args=[job_description, cv_bytes, tone])

    return JSONResponse(status_code=202, content={"job_id": task.id})


@app.get("/api/status/{job_id}")
def get_status(job_id: str):
    """
    Polls for the status and result of the cover letter generation job.
    """
    task_result = AsyncResult(job_id)
    task_result = AsyncResult(job_id, app=celery_app)  # Explicitly pass celery_app
    if not task_result.ready():
        return {"status": task_result.state, "job_id": job_id}
    return {
        "status": task_result.state,
        "job_id": job_id,
        "result": task_result.result if task_result.successful() else str(task_result.result)
    }
    
    # if task_result.state == 'PENDING':
    #     return {"status": "Pending", "job_id": job_id}
    # elif task_result.state == 'STARTED':
    #     return {"status": "In progress", "job_id": job_id}
    # elif task_result.state == 'FAILURE':
    #     return {"status": "Failed", "job_id": job_id, "error": str(task_result.result)}
    # elif task_result.state == 'SUCCESS':
    #     return {
    #         "status": "Success",
    #         "job_id": job_id,
    #         "result": task_result.result
    #     }
    # else:
    #     raise HTTPException(status_code=500, detail="Unknown task state.")

    # if not task_result.ready():
    #     return JSONResponse(status_code=202, content={"status": "PENDING"})

    # if task_result.failed():
    #     return JSONResponse(status_code=500, content={"status": "FAILURE", "error": str(task_result.info)})

    # result = task_result.get()
    # return JSONResponse(status_code=200, content={"status": "SUCCESS", "result": result})