import base64
import logging
import requests

from fastapi import HTTPException
from pydantic import HttpUrl

from app.models import ContextResponse, InvestigateResponse, ReadmeResponse
from app.services.answer_service import compose_answer
from app.services.llm_provider import select_files
from app.services.cache_service import get_cached_response,set_cached_response

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com/repos"
NOISE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".lock", ".pyc")
NOISE_FOLDERS = (".git/", ".venv/", "venv/", "node_modules/", "__pycache__/")


def _github_get(endpoint: str, ignore_404: bool = False) -> dict| list | None:
    """Helper to deduplicate GitHub API requests and error handling."""

    # check cache first
    cached=get_cached_response(endpoint)
    if cached is not None:
        logger.info("CACHE HIT: %s",endpoint)
        return cached

    # no cache make the http request
    url = f"{GITHUB_API_BASE}{endpoint}"

    import os
    headers = {}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.get(url, headers=headers, timeout=5)

    if response.status_code == 404:
        if ignore_404:
            return None
        raise HTTPException(status_code=404, detail=f"Not found: {endpoint}")

    if response.status_code == 403:
        raise HTTPException(status_code=502, detail="GitHub API rate limit exceeded! Please add a GITHUB_TOKEN to your .env file.")

    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"GitHub API request failed for {endpoint}")

    logger.info("fetched %s successfully --> statuscode:%s", endpoint, response.status_code)


    data =response.json()
    set_cached_response(endpoint,data)
    return data


def investigate_repo(repo: HttpUrl, question: str) -> InvestigateResponse:
    owner, name = parse_gh_url(repo)

    payload = fetch_repo_metadata(owner, name)
    top_level_files = fetch_top_level_files(owner, name)
    readme_text = fetch_readme(owner, name)

    stars = payload.get("stargazers_count", 0)
    description_text = payload.get("description") or "No description available"
    language_text = payload.get("language") or "an unknown language"
    summary = f"{owner}/{name} is a GitHub repository with {stars} stars, written in {language_text}. {description_text}"
    readme_available = readme_text is not None

    def_branch = payload.get("default_branch") or "main"
    repo_tree = fetch_repo_tree(owner, name, def_branch)
    clean_tree = filter_noise(repo_tree)

    file_list = select_files(question, clean_tree)
    file_contents = fetch_file_contents(owner, name, file_list)

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
        "default_branch": def_branch,
        "file_contents": file_contents,
    }

    answer = compose_answer(question, context)

    return InvestigateResponse(
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


def readme_repo(repo: HttpUrl) -> ReadmeResponse:
    owner, name = parse_gh_url(repo)
    readme_text = fetch_readme(owner, name)
    if readme_text:
        readme_text = truncate_text(readme_text, 1000)

    return ReadmeResponse(repo=repo, readme_text=readme_text)


def context_repo(repo: HttpUrl) -> ContextResponse:
    owner, name = parse_gh_url(repo)
    payload = fetch_repo_metadata(owner, name)
    top_level_files = fetch_top_level_files(owner, name)
    readme_text = fetch_readme(owner, name)

    return ContextResponse(
        repo=repo,
        owner=owner,
        name=name,
        description=payload.get("description"),
        language=payload.get("language"),
        readme_available=readme_text is not None,
        readme_preview=truncate_text(readme_text, 500) if readme_text else None,
        top_level_files=top_level_files,
        detected_stack=detect_stack(top_level_files),
        default_branch=payload.get("default_branch"),
    )


def parse_gh_url(repo: HttpUrl) -> tuple[str, str]:
    repo_str = str(repo)
    if not repo_str.startswith("https://github.com/"):
        raise HTTPException(status_code=400, detail="Only GitHub repository URLs are supported")

    parts = repo_str.removeprefix("https://github.com/").strip("/").split("/")
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="Repository URL must look like https://github.com/{owner}/{repo}")

    return parts[0], parts[1].removesuffix(".git")


def fetch_repo_metadata(owner: str, name: str) -> dict:
    response = _github_get(f"/{owner}/{name}")
    if isinstance(response, dict):
        return response
    return {}


def fetch_readme(owner: str, name: str) -> str | None:
    response = _github_get(f"/{owner}/{name}/readme", ignore_404=True)
    if not isinstance(response, dict):
        return None

    content = response.get("content")
    return base64.b64decode(content).decode("utf-8") if content else None


def fetch_top_level_files(owner: str, name: str) -> list[str]:
    response = _github_get(f"/{owner}/{name}/contents", ignore_404=True)
    if not response:
        return []

    if not isinstance(response, list):
        return []

    return [item["name"] for item in response if "name" in item]


def fetch_repo_tree(owner: str, name: str, default_branch: str) -> list[str]:
    response = _github_get(f"/{owner}/{name}/git/trees/{default_branch}?recursive=1")
    if not isinstance(response, dict):
        return []

    tree_payload = response.get("tree", [])
    return [item["path"] for item in tree_payload if isinstance(item, dict) and item.get("type") == "blob"]


def fetch_file_contents(owner: str, name: str, file_paths: list[str]) -> dict[str, str]:
    file_contents = {}
    for path in file_paths:
        response = _github_get(f"/{owner}/{name}/contents/{path}", ignore_404=True)
        if not isinstance(response, dict):
            continue

        content = response.get("content")
        if isinstance(content, str):
            file_contents[path] = base64.b64decode(content).decode("utf-8")
        else:
            file_contents[path] = ""

    return file_contents


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
