import base64
import requests
from fastapi import HTTPException
from pydantic import HttpUrl


from app.models import ContextResponse, InvestigateResponse, ReadmeResponse
from app.services.answer_service import compose_answer


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
        "default_branch": payload.get("default_branch"),
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
        sources=["github_metadata", "github_readme_presence", "github_repo_contents"],
    )

def readme_repo(repo: HttpUrl) -> ReadmeResponse:
    owner, name = parse_gh_url(repo)
    readme_text = fetch_readme(owner, name)
    if readme_text:
        readme_text = truncate_text(readme_text, 1000)

    return ReadmeResponse(
        repo=repo,
        readme_text=readme_text,
    )


def context_repo(repo: HttpUrl) -> ContextResponse:
    owner, name = parse_gh_url(repo)
    payload = fetch_repo_metadata(owner, name)
    top_level_files = fetch_top_level_files(owner, name)
    readme_text = fetch_readme(owner, name)
    readme_preview = truncate_text(readme_text, 500) if readme_text else None
    detected_stack = detect_stack(top_level_files)

    return ContextResponse(
        repo=repo,
        owner=owner,
        name=name,
        description=payload.get("description"),
        language=payload.get("language"),
        readme_available=readme_text is not None,
        readme_preview=readme_preview,
        top_level_files=top_level_files,
        detected_stack=detected_stack,
        default_branch=payload.get("default_branch"),
    )


def parse_gh_url(repo: HttpUrl) -> tuple[str, str]:
    if not str(repo).startswith("https://github.com/"):
        raise HTTPException(
            status_code=400,
            detail="Only GitHub repository URLs are supported",
        )

    parts = str(repo).removeprefix("https://github.com/").strip("/").split("/")

    if len(parts) < 2:
        raise HTTPException(
            status_code=400,
            detail="Repository URL must look like https://github.com/{owner}/{repo}",
        )

    owner = parts[0]
    name = parts[1].removesuffix(".git")
    return owner, name


def fetch_repo_metadata(owner: str, name: str) -> dict:
    github_response = requests.get(
        f"https://api.github.com/repos/{owner}/{name}",
        timeout=5,
    )

    if github_response.status_code == 404:
        raise HTTPException(status_code=404, detail="Repository not found")

    if github_response.status_code != 200:
        raise HTTPException(status_code=502, detail="GitHub API request failed")

    return github_response.json()


def fetch_readme(owner: str, name: str) -> str | None:
    readme_response = requests.get(
        f"https://api.github.com/repos/{owner}/{name}/readme",
        timeout=5,
    )

    if readme_response.status_code == 404:
        return None

    if readme_response.status_code != 200:
        raise HTTPException(status_code=502, detail="GitHub API request failed")

    readme_content = readme_response.json().get("content")
    if not readme_content:
        return None

    return base64.b64decode(readme_content).decode("utf-8")


def fetch_top_level_files(owner: str, name: str) -> list[str]:
    contents_response = requests.get(
        f"https://api.github.com/repos/{owner}/{name}/contents",
        timeout=5,
    )

    if contents_response.status_code == 404:
        return []

    if contents_response.status_code != 200:
        raise HTTPException(status_code=502, detail="GitHub API request failed")

    payload = contents_response.json()
    if not isinstance(payload, list):
        return []

    return [item["name"] for item in payload if "name" in item]


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
