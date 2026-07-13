import logging
import json
from contextlib import contextmanager

from fastapi import HTTPException
from pydantic import HttpUrl

from app.models import ContextResponse, InvestigateResponse, ReadmeResponse
from app.services.answer_service import compose_answer, compose_answer_stream
from app.services.llm_provider import select_files
from app.services.github import (
    GitHubRepository,
    GitHubError,
    GitHubResourceNotFoundError,
    GitHubRateLimitError,
    GitHubTimeoutError,
    InvalidGitHubURLError
)

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
    except GitHubResourceNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except GitHubRateLimitError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except GitHubTimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except GitHubError as e:
        raise HTTPException(status_code=502, detail=str(e))


def investigate_repo(repo: HttpUrl, question: str) -> InvestigateResponse:
    url_str = str(repo)
    logger.info("--- Starting Investigation ---")
    logger.info("Repo: %s", url_str)
    logger.info("Question: %s", question)

    with github_error_boundary():
        gh_repo = GitHubRepository.from_url(repo)
        top_level_files = gh_repo.list_top_level_files()
        readme_text = gh_repo.get_readme()
        repo_tree = gh_repo.list_files()

    owner = gh_repo.owner
    name = gh_repo.name
    payload = gh_repo.metadata

    stars = payload.get("stargazers_count", 0)
    description_text = payload.get("description") or "No description available"
    language_text = payload.get("language") or "an unknown language"
    summary = f"{owner}/{name} is a GitHub repository with {stars} stars, written in {language_text}. {description_text}"
    readme_available = readme_text is not None

    clean_tree = filter_noise(repo_tree)

    file_list = select_files(question, clean_tree)

    with github_error_boundary():
        file_contents = gh_repo.read_files(file_list)

    context = {
        "owner": owner,
        "name": name,
        "language": language_text,
        "description": description_text,
        "stars": stars,
        "readme_available": readme_available,
        "readme_preview": truncate_text(readme_text, 500) if readme_text else None,
        "top_level_files": top_level_files,
        "detected_stack": detect_stack(top_level_files),
        "default_branch": gh_repo.default_branch,
        "file_contents": file_contents,
    }

    answer = compose_answer(question, context)

    final_response = InvestigateResponse(
        repo=repo,
        provider="github",
        owner=owner,
        name=name,
        question=question,
        description=description_text,
        stars=stars,
        language=language_text,
        summary=summary,
        readme_available=readme_available,
        answer=answer,
        sources=["github_metadata", "github_readme_presence", "github_repo_contents"] + file_list,
    )

    logger.info("--- Investigation Complete ---")
    return final_response


def investigate_repo_stream(repo_url: HttpUrl, question: str):
    url_str = str(repo_url)
    logger.info("--- Starting Streaming Investigation ---")
    logger.info("Repo: %s", url_str)
    logger.info("Question: %s", question)

    with github_error_boundary():
        gh_repo = GitHubRepository.from_url(repo_url)
        readme_content = gh_repo.get_readme()
        top_level_files = gh_repo.list_top_level_files()
        repo_tree = gh_repo.list_files()

    owner = gh_repo.owner
    name = gh_repo.name
    payload = gh_repo.metadata

    file_list = select_files(question, repo_tree)

    with github_error_boundary():
        file_contents = gh_repo.read_files(file_list)

    context = {
        "owner": owner,
        "name": name,
        "language": payload.get("language", ""),
        "description": payload.get("description", ""),
        "stars": payload.get("stargazers_count", 0),
        "readme_available": readme_content is not None,
        "readme_preview": readme_content[:500] if readme_content else "",
        "top_level_files": ", ".join(top_level_files),
        "detected_stack": "",
        "default_branch": gh_repo.default_branch,
        "file_contents": file_contents,
    }

    summary = f"{owner}/{name} is a GitHub repository with {payload.get('stargazers_count', 0)} stars, written in {payload.get('language', 'Unknown')}. {payload.get('description', '')}"

    meta = {
        "repo": url_str,
        "provider": "github",
        "owner": owner,
        "name": name,
        "question": question,
        "description": payload.get("description", ""),
        "stars": payload.get("stargazers_count", 0),
        "language": payload.get("language", ""),
        "summary": summary,
        "readme_available": readme_content is not None,
        "sources": ["github_metadata", "github_readme_presence", "github_repo_contents"] + file_list,
    }

    yield f"data: {json.dumps({'metadata': meta})}\n\n"

    logger.info("Generating streamed answer using Gemini...")
    full_answer = ""
    for chunk in compose_answer_stream(question, context):
        full_answer += chunk
        yield f"data: {json.dumps({'chunk': chunk})}\n\n"
    logger.info("Answer stream completed successfully.")

    logger.info("--- Streaming Investigation Complete ---")


def readme_repo(repo: HttpUrl) -> ReadmeResponse:
    with github_error_boundary():
        gh_repo = GitHubRepository.from_url(repo)
        readme_text = gh_repo.get_readme()

    if readme_text:
        readme_text = truncate_text(readme_text, 1000)

    return ReadmeResponse(repo=repo, readme_text=readme_text)


def context_repo(repo: HttpUrl) -> ContextResponse:
    with github_error_boundary():
        gh_repo = GitHubRepository.from_url(repo)
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
