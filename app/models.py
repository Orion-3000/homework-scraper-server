from pydantic import BaseModel

class StartJobRequest(BaseModel):
    email: str
    password: str
    sheetLink: str

class StartJobResponse(BaseModel):
    jobId: str

class JobStatusResponse(BaseModel):
    status: str
    progress: float | None = None
    message: str | None = None