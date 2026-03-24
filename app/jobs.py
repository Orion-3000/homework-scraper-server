import uuid
from typing import Dict, Any

jobs: Dict[str, Dict[str, Any]] = {}

def create_job() -> str:
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "queued",
        "progress": 0.0,
        "message": "Queued"
    }
    return job_id

def update_job(job_id: str, *, status: str | None = None, progress: float | None = None, message: str | None = None):
    if job_id not in jobs:
        return
    if status is not None:
        jobs[job_id]["status"] = status
    if progress is not None:
        jobs[job_id]["progress"] = progress
    if message is not None:
        jobs[job_id]["message"] = message

def get_job(job_id: str):
    return jobs.get(job_id)