from app.jobs import update_job
from app.real_scraper import run_scraper

def run_scraper_job(job_id: str, email: str, password: str, sheet_link: str):
    try:
        update_job(job_id, status="running", progress=0.01, message="Job started")

        def progress_callback(progress: float, message: str):
            update_job(job_id, status="running", progress=progress, message=message)

        run_scraper(
            email=email,
            password=password,
            sheet_link=sheet_link,
            progress_callback=progress_callback
        )

        update_job(job_id, status="done", progress=1.0, message="Finished")

    except Exception as e:
        update_job(job_id, status="failed", progress=1.0, message=str(e))