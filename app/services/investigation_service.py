import logging
from typing import Iterator, AsyncIterator

from app.models import ContextResponse, ReadmeResponse
from app.investigation_events import (
    InvestigationEvent,
    InvestigationMetadata,
    InvestigationFileRead,
    InvestigationAnswerChunk,
    InvestigationCompleted
)
from app.services.answer_service import prepare_answer_stream
from app.services.investigation_workspace import InvestigationWorkspace
from app.services.repo_map import build_repo_map
from app.investigation_trace import (
    InvestigationTrace,
    AgentStepTrace,
    FailureStage,
    TerminationReason,
    bound_trace_string
)
import time
from app.services.repository_snapshot import RepositorySnapshot
from app.services.investigation_agent import investigation_agent, AgentDeps, DomainTerminationException
from pydantic_ai.exceptions import UnexpectedModelBehavior
from app.services.github import (
    GitHubRepository,
    GitHubError,
    GitHubResourceNotFoundError,
    GitHubRateLimitError,
    GitHubTimeoutError,
    InvalidGitHubURLError,
    RepositoryArchiveTooLargeError,
    RepositoryArchiveUnsafeError,
    RepositorySnapshotError
)
from pydantic import HttpUrl
from fastapi import HTTPException
from contextlib import contextmanager

logger = logging.getLogger(__name__)

NOISE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".lock", ".pyc")
NOISE_FOLDERS = (".git/", ".venv/", "venv/", "node_modules/", "__pycache__/")


@contextmanager
def github_error_boundary():
    """Maps GitHub domain exceptions to public FastAPI HTTPExceptions."""
    try:
        yield
    except InvalidGitHubURLError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RepositoryArchiveUnsafeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RepositoryArchiveTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except RepositorySnapshotError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except GitHubResourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except GitHubRateLimitError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except GitHubTimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except GitHubError as e:
        raise HTTPException(status_code=502, detail=str(e))


async def run_investigation(snapshot: RepositorySnapshot, question: str, trace: InvestigationTrace) -> AsyncIterator[InvestigationEvent]:
    gh_repo = snapshot.gh_repo
    url_str = f"https://github.com/{gh_repo.owner}/{gh_repo.name}"
    logger.info("--- Starting Investigation ---")
    logger.info("Repo: %s", url_str)
    logger.info("Question: %s", question)

    top_level_files = gh_repo.list_top_level_files()
    readme_text = gh_repo.get_readme()
    raw_tree = list(snapshot.extracted_files)

    owner = gh_repo.owner
    name = gh_repo.name
    payload = gh_repo.metadata

    stars = payload.get("stargazers_count", 0)
    description_text = payload.get("description", "") or ""
    language_text = payload.get("language", "") or "Unknown"
    summary = f"{owner}/{name} is a GitHub repository with {stars} stars, written in {language_text}. {description_text}"
    readme_available = readme_text is not None
    detected_stack = detect_stack(top_level_files)

    clean_tree = filter_noise(raw_tree)
    workspace = InvestigationWorkspace(snapshot, clean_tree)

    # Build the hierarchical repository map once before the investigation loop.
    repo_map = build_repo_map(workspace.allowed_paths)

    yield InvestigationMetadata(
        repo=url_str,
        provider="github",
        owner=owner,
        name=name,
        question=question,
        description=description_text,
        stars=stars,
        language=language_text,
        summary=summary,
        readme_available=readme_available,
        sources=["github_metadata", "github_readme_presence", "github_repo_contents"] + list(workspace.allowed_paths)
    )

    try:
        deps = AgentDeps(workspace=workspace, trace=trace)
        prompt = f"Repository map:\n{repo_map}\n\nQuestion: {question}"

        try:
            from pydantic_ai.usage import UsageLimits
            MAX_MODEL_REQUESTS = 20
            result = await investigation_agent.run(
                prompt,
                deps=deps,
                usage_limits=UsageLimits(request_limit=MAX_MODEL_REQUESTS)
            )
            trace.termination_reason = TerminationReason.MODEL_FINISHED
            usage = result.usage
            trace.model_requests = usage.requests
            trace.input_tokens = usage.input_tokens
            trace.output_tokens = usage.output_tokens
            
        except DomainTerminationException as e:
            trace.termination_reason = TerminationReason(e.reason)
        except UnexpectedModelBehavior:
            trace.termination_reason = TerminationReason.MAX_ACTIONS

        if trace.termination_reason is None:
            trace.termination_reason = workspace.get_termination_reason()

    except Exception as e:
        if not trace.failure_stage:
            trace.failure_stage = FailureStage.AGENT_DECISION
        trace.error_type = type(e).__name__
        raise

    context = {
        "owner": owner,
        "name": name,
        "language": language_text,
        "description": description_text,
        "stars": stars,
        "readme_available": readme_available,
        "readme_preview": truncate_text(readme_text, 500) if readme_text else "",
        "top_level_files": ", ".join(top_level_files),
        "detected_stack": detected_stack,
        "default_branch": gh_repo.default_branch,
        "file_contents": workspace.gathered_evidence,
    }

    trace.final_evidence_files_count = len(workspace.gathered_evidence)
    trace.final_evidence_chars = workspace.total_evidence_chars

    logger.info("Generating streamed answer using Gemini...")
    ans_res = prepare_answer_stream(question, context)
    trace.final_prompt_chars = ans_res.prompt_chars

    start_ans_time = time.perf_counter()
    try:
        for chunk in ans_res.chunk_generator:
            trace.answer_chunks_emitted += 1
            yield InvestigationAnswerChunk(chunk=chunk)
    except Exception as e:
        trace.failure_stage = FailureStage.ANSWER_GENERATION
        trace.error_type = type(e).__name__
        raise
    finally:
        trace.answer_generation_duration_sec = time.perf_counter() - start_ans_time

    logger.info("Answer stream completed successfully.")
    yield InvestigationCompleted()

    logger.info("--- Investigation Complete ---")


def readme_repo(repo: HttpUrl) -> ReadmeResponse:
    with github_error_boundary():
        gh_repo = GitHubRepository.from_url(str(repo))
        readme_text = gh_repo.get_readme()

    if readme_text:
        readme_text = truncate_text(readme_text, 1000)

    return ReadmeResponse(repo=repo, readme_text=readme_text)


def context_repo(repo: HttpUrl) -> ContextResponse:
    with github_error_boundary():
        gh_repo = GitHubRepository.from_url(str(repo))
        top_level_files = gh_repo.list_top_level_files()
        readme_text = gh_repo.get_readme()

    return ContextResponse(
        repo=repo,
        owner=gh_repo.owner,
        name=gh_repo.name,
        description=gh_repo.metadata.get("description"),
        language=gh_repo.metadata.get("language"),
        readme_available=readme_text is not None,
        readme_preview=truncate_text(readme_text, 500) if readme_text else None,
        top_level_files=top_level_files,
        detected_stack=detect_stack(top_level_files),
        default_branch=gh_repo.default_branch,
    )


def filter_noise(file_paths: list[str]) -> list[str]:
    return [
        path for path in file_paths
        if not path.endswith(NOISE_EXTENSIONS)
        and not any(folder in path for folder in NOISE_FOLDERS)
    ]


def truncate_text(text: str, limit: int) -> str:
    return text[:limit] + "..." if len(text) > limit else text


def detect_stack(top_level_files: list[str]) -> str:
    files = set(top_level_files)
    if "pom.xml" in files or "build.gradle" in files:
        return "java"
    if "pyproject.toml" in files or "requirements.txt" in files:
        return "python"
    if "package.json" in files:
        return "node"
    if "Cargo.toml" in files:
        return "rust"
    return "unknown"
