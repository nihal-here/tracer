from app.models import HealthResponse, InvestigateRequest, ReadmeResponse, RepoRequest, ContextResponse
from app.services.investigation_service import run_investigation, readme_repo, context_repo, github_error_boundary
from app.services.github import GitHubRepository
from app.services.repository_snapshot import RepositorySnapshot
from app.investigation_events import InvestigationEvent, InvestigationMetadata, InvestigationAnswerChunk


from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
import logging
import json
from dataclasses import asdict
from typing import Iterator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_frontend():
    return FileResponse("static/index.html")

def _sse_adapter(events: Iterator[InvestigationEvent]):
    for event in events:
        if isinstance(event, InvestigationMetadata):
            yield f"data: {json.dumps({'metadata': asdict(event)})}\n\n"
        elif isinstance(event, InvestigationAnswerChunk):
            yield f"data: {json.dumps({'chunk': event.chunk})}\n\n"

@app.post("/investigate")
def investigate(request: InvestigateRequest):
    with github_error_boundary():
        gh_repo = GitHubRepository.from_url(str(request.repo))
        snapshot = RepositorySnapshot(gh_repo)
        snapshot.materialize()

    def event_generator():
        try:
            yield from run_investigation(snapshot, request.question)
        finally:
            snapshot.cleanup()

    return StreamingResponse(_sse_adapter(event_generator()), media_type="text/event-stream")

@app.post("/readme", response_model=ReadmeResponse)
def readme(request: RepoRequest) -> ReadmeResponse:
    return readme_repo(request.repo)

@app.post("/context", response_model=ContextResponse)
def context(request: RepoRequest) -> ContextResponse:
    return context_repo(request.repo)

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="OK")
