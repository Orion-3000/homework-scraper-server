from fastapi import FastAPI, HTTPException, BackgroundTasks
from app.models import StartJobRequest, StartJobResponse, JobStatusResponse
from app.jobs import create_job, get_job
from app.scraper_runner import run_scraper_job

app = FastAPI()

@app.get("/")
def root():
    return {"ok": True, "service": "homework-scraper-server"}

@app.post("/start-job", response_model=StartJobResponse)
def start_job(payload: StartJobRequest, background_tasks: BackgroundTasks):
    job_id = create_job()

    background_tasks.add_task(
        run_scraper_job,
        job_id,
        payload.email,
        payload.password,
        payload.sheetLink
    )

    return StartJobResponse(jobId=job_id)

@app.get("/job-status/{job_id}", response_model=JobStatusResponse)
def job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        status=job["status"],
        progress=job.get("progress"),
        message=job.get("message")
    )