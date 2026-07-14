import logging
from typing import Iterator

from app.models import ContextResponse, ReadmeResponse
from app.investigation_events import (
    InvestigationEvent,
    InvestigationMetadata,
    InvestigationFileRead,
    InvestigationAnswerChunk,
    InvestigationCompleted
)
from app.services.answer_service import compose_answer_stream
from app.services.investigation_workspace import InvestigationWorkspace
from app.services.repository_snapshot import RepositorySnapshot
from app.services.investigation_agent import choose_next_action, ActionType
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


def run_investigation(snapshot: RepositorySnapshot, question: str) -> Iterator[InvestigationEvent]:
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

    while workspace.can_continue():
        action = choose_next_action(question, workspace.allowed_paths, workspace.history)
        workspace.record_iteration()

        if action.action_type == ActionType.FINISH:
            break

        elif action.action_type == ActionType.READ_FILE:
            observation = workspace.read_file(action.file_path)

            if observation.new_evidence_added:
                assert observation.path is not None
                yield InvestigationFileRead(path=observation.path, chars_read=len(observation.content or ""), cached=False)

        elif action.action_type == ActionType.SEARCH_CODE:
            observation = workspace.search_code(action.search_query, action.case_sensitive)

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

    logger.info("Generating streamed answer using Gemini...")
    for chunk in compose_answer_stream(question, context):
        yield InvestigationAnswerChunk(chunk=chunk)

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
