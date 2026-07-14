import os
import logging
import base64
import binascii
import requests
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com/repos"


class GitHubError(Exception):
    """Base exception for GitHub API errors."""
    pass

class GitHubResourceNotFoundError(GitHubError):
    pass

class GitHubRateLimitError(GitHubError):
    pass

class GitHubTimeoutError(GitHubError):
    pass

class InvalidGitHubURLError(GitHubError):
    pass

class GitHubAPIError(GitHubError):
    pass

class RepositoryArchiveTooLargeError(GitHubError):
    pass

class RepositoryArchiveUnsafeError(GitHubError):
    pass

class RepositorySnapshotError(GitHubError):
    pass


def check_github_response(response: requests.Response, endpoint: str = "", ignore_404: bool = False) -> dict[str, Any] | list[Any] | None:
    """Shared HTTP response checker for GitHub API and archive requests."""
    if response.status_code == 404:
        if ignore_404:
            return None
        raise GitHubResourceNotFoundError(f"Not found: {endpoint}")

    if response.status_code == 403:
        if "X-RateLimit-Remaining" in response.headers and response.headers["X-RateLimit-Remaining"] == "0":
            raise GitHubRateLimitError("GitHub API rate limit exceeded! Please add a GITHUB_TOKEN to your .env file.")
        if "rate limit" in response.text.lower():
            raise GitHubRateLimitError("GitHub API rate limit exceeded! Please add a GITHUB_TOKEN to your .env file.")
        raise GitHubAPIError(f"GitHub API request forbidden: {endpoint}")

    if response.status_code != 200:
        raise GitHubAPIError(f"GitHub API request failed for {endpoint} with status {response.status_code}")

    return response.json()

def _github_get(endpoint: str, ignore_404: bool = False) -> dict[str, Any] | list[Any] | None:
    url = f"{GITHUB_API_BASE}{endpoint}"
    headers = {}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.get(url, headers=headers, timeout=15)
    except requests.exceptions.Timeout:
        logger.error(f"GitHub API timed out when fetching {endpoint}")
        raise GitHubTimeoutError("GitHub API timed out. The repository might be too large.")
    except requests.exceptions.RequestException as e:
        logger.error(f"GitHub API request failed when fetching {endpoint}: {e}")
        raise GitHubAPIError(f"GitHub API request failed: {e}")

    return check_github_response(response, endpoint, ignore_404)


@dataclass(frozen=True)
class GitHubRepository:
    """Represents a GitHub repository pinned to a specific commit revision."""
    owner: str
    name: str
    revision: str
    default_branch: str
    metadata: dict[str, Any]

    @classmethod
    def from_url(cls, repo_url: str) -> "GitHubRepository":
        repo_str = str(repo_url)
        if not repo_str.startswith("https://github.com/"):
            raise InvalidGitHubURLError("Only GitHub repository URLs are supported")

        path_part = repo_str.removeprefix("https://github.com/")
        parts = path_part.split("/")

        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise InvalidGitHubURLError("Repository URL must be exactly https://github.com/{owner}/{repo}")

        owner = parts[0]
        name = parts[1].removesuffix(".git")

        # 1. Fetch metadata
        metadata_response = _github_get(f"/{owner}/{name}")
        if not isinstance(metadata_response, dict):
            metadata_response = {}

        default_branch = metadata_response.get("default_branch")
        if not default_branch or not isinstance(default_branch, str):
            raise GitHubAPIError("GitHub metadata is missing a valid 'default_branch'")

        # 2. Resolve default branch to SHA
        branch_response = _github_get(f"/{owner}/{name}/branches/{default_branch}")
        if not isinstance(branch_response, dict):
            raise GitHubAPIError(f"Failed to resolve branch {default_branch} to a commit SHA")

        commit = branch_response.get("commit")
        if not isinstance(commit, dict):
            raise GitHubAPIError(f"Failed to resolve branch {default_branch} to a commit SHA")

        revision = commit.get("sha")
        if not revision or not isinstance(revision, str):
            raise GitHubAPIError(f"Failed to resolve branch {default_branch} to a commit SHA")

        return cls(
            owner=owner,
            name=name,
            revision=revision,
            default_branch=default_branch,
            metadata=metadata_response
        )

    def get_readme(self) -> str | None:
        response = _github_get(f"/{self.owner}/{self.name}/readme?ref={self.revision}", ignore_404=True)
        if not isinstance(response, dict):
            return None
        content = response.get("content")
        if not content:
            return None
        try:
            return base64.b64decode(content).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError) as e:
            raise GitHubAPIError(f"Failed to decode README content: {e}")

    def list_files(self) -> list[str]:
        response = _github_get(f"/{self.owner}/{self.name}/git/trees/{self.revision}?recursive=1")
        if not isinstance(response, dict):
            return []
        tree_payload = response.get("tree", [])
        return [item["path"] for item in tree_payload if isinstance(item, dict) and item.get("type") == "blob"]

    def list_top_level_files(self) -> list[str]:
        response = _github_get(f"/{self.owner}/{self.name}/contents?ref={self.revision}", ignore_404=True)
        if not response:
            return []
        if not isinstance(response, list):
            return []
        return [item["name"] for item in response if "name" in item]
