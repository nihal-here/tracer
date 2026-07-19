from app.models import HealthResponse, InvestigateRequest, ReadmeResponse, RepoRequest, ContextResponse
from app.services.investigation_service import run_investigation, readme_repo, context_repo, github_error_boundary
from app.services.github import GitHubRepository
from app.services.repository_snapshot import RepositorySnapshot
from app.investigation_events import (
    InvestigationEvent,
    InvestigationMetadata,
    CitationMetadata,
    InvestigationTraceMetadata,
    InvestigationAnswerChunk,
    InvestigationCompleted,
)
from app.investigation_trace import InvestigationTrace, FailureStage, emit_trace
import time
from datetime import datetime, timezone


from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
import logging
import json
from dataclasses import asdict
from typing import Iterator, AsyncIterator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_frontend():
    return FileResponse("static/index.html")

async def _sse_adapter(events: AsyncIterator[InvestigationEvent]):
    async for event in events:
        if isinstance(event, InvestigationMetadata):
            yield f"data: {json.dumps({'metadata': asdict(event)})}\n\n"
        elif isinstance(event, CitationMetadata):
            yield f"data: {json.dumps({'citations': event.citations})}\n\n"
        elif isinstance(event, InvestigationTraceMetadata):
            yield f"data: {json.dumps({'investigation_trace': event.steps})}\n\n"
        elif isinstance(event, InvestigationAnswerChunk):
            yield f"data: {json.dumps({'chunk': event.chunk})}\n\n"
        elif isinstance(event, InvestigationCompleted):
            yield "data: {\"completed\": true}\n\n"

@app.post("/investigate")
async def investigate(request: InvestigateRequest):
    trace = InvestigationTrace(
        started_at=datetime.now(timezone.utc).isoformat(),
        question_chars=len(request.question),
        _start_time=time.perf_counter()
    )

    try:
        with github_error_boundary():
            t_res = time.perf_counter()
            gh_repo = GitHubRepository.from_url(str(request.repo))
            trace.repository_resolution_duration_sec = time.perf_counter() - t_res

            snapshot = RepositorySnapshot(gh_repo)
            t_mat = time.perf_counter()
            snapshot.materialize()
            trace.materialization_duration_sec = time.perf_counter() - t_mat
            trace.repository_snapshot_cache_hit = snapshot.cache_hit
            trace.repository_cache_lookup_duration_sec = snapshot.cache_lookup_duration_sec
    except Exception as e:
        if not trace.failure_stage:
            # If resolution duration wasn't fully computed, assume it failed during resolution
            if trace.repository_resolution_duration_sec == 0.0:
                trace.failure_stage = FailureStage.REPOSITORY_RESOLUTION
            else:
                trace.failure_stage = FailureStage.MATERIALIZATION
        trace.error_type = type(e).__name__
        emit_trace(trace)
        raise

    async def event_generator():
        try:
            async for event in run_investigation(snapshot, request.question, trace):
                yield event
        finally:
            emit_trace(trace)
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
