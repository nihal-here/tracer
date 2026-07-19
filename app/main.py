import os
import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import json
from dataclasses import asdict
from typing import AsyncIterator

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_INVESTIGATIONS", 2))
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", 5))
TRUST_X_FORWARDED_FOR = os.environ.get("TRUST_X_FORWARDED_FOR", "false").lower() == "true"

concurrency_semaphore: asyncio.Semaphore | None = None
rate_limit_records: dict[str, list[float]] = {}

def check_rate_limit(request: Request):
    ip = request.client.host if request.client else "unknown"
    if TRUST_X_FORWARDED_FOR and "x-forwarded-for" in request.headers:
        ip = request.headers["x-forwarded-for"].split(",")[0].strip()
    
    now = time.time()
    history = rate_limit_records.get(ip, [])
    history = [t for t in history if now - t < 60]
    
    if len(history) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(status_code=429, detail="Too many investigation requests. Please try again later.")
    
    history.append(now)
    rate_limit_records[ip] = history


@asynccontextmanager
async def lifespan(app: FastAPI):
    global concurrency_semaphore
    from app.services.llm_provider import get_gemini_api_key
    get_gemini_api_key() # Validates key presence on startup
    concurrency_semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    yield

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self';"
    )
    return response

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
async def investigate(request: Request, body: InvestigateRequest):
    check_rate_limit(request)
    
    assert concurrency_semaphore is not None
    try:
        await asyncio.wait_for(concurrency_semaphore.acquire(), timeout=0.5)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Server is currently at maximum capacity for investigations. Please try again later.")
        
    trace = InvestigationTrace(
        started_at=datetime.now(timezone.utc).isoformat(),
        question_chars=len(body.question),
        _start_time=time.perf_counter()
    )

    try:
        with github_error_boundary():
            t_res = time.perf_counter()
            gh_repo = await asyncio.to_thread(GitHubRepository.from_url, str(body.repo))
            trace.repository_resolution_duration_sec = time.perf_counter() - t_res

            snapshot = RepositorySnapshot(gh_repo)
            t_mat = time.perf_counter()
            await asyncio.to_thread(snapshot.materialize)
            trace.materialization_duration_sec = time.perf_counter() - t_mat
            trace.repository_snapshot_cache_hit = snapshot.cache_hit
            trace.repository_cache_lookup_duration_sec = snapshot.cache_lookup_duration_sec
    except Exception as e:
        concurrency_semaphore.release()
        if not trace.failure_stage:
            if trace.repository_resolution_duration_sec == 0.0:
                trace.failure_stage = FailureStage.REPOSITORY_RESOLUTION
            else:
                trace.failure_stage = FailureStage.MATERIALIZATION
        trace.error_type = type(e).__name__
        emit_trace(trace)
        raise

    async def event_generator():
        try:
            async for event in run_investigation(snapshot, body.question, trace):
                yield event
        except asyncio.CancelledError:
            logging.getLogger(__name__).info("Client disconnected, cancelling investigation.")
            raise
        finally:
            emit_trace(trace)
            snapshot.cleanup()
            if concurrency_semaphore:
                concurrency_semaphore.release()

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
