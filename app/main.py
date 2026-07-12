from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

from app.models import HealthResponse, InvestigateRequest, InvestigateResponse, ReadmeResponse, RepoRequest, ContextResponse
from app.services.investigation_service import investigate_repo, readme_repo, context_repo

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_frontend():
    return FileResponse("static/index.html")


@app.post("/investigate", response_model=InvestigateResponse)
def investigate(request: InvestigateRequest) -> InvestigateResponse:
    return investigate_repo(request.repo, request.question)

@app.post("/readme", response_model=ReadmeResponse)
def readme(request: RepoRequest) -> ReadmeResponse:
    return readme_repo(request.repo)

@app.post("/context", response_model=ContextResponse)
def context(request: RepoRequest) -> ContextResponse:
    return context_repo(request.repo)

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="OK")
