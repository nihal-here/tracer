from fastapi import FastAPI
from app.models import HealthResponse, InvestigateRequest, InvestigateResponse, ReadmeResponse, RepoRequest, ContextResponse
from app.services.investigation_service import investigate_repo, readme_repo, context_repo

app = FastAPI()


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
