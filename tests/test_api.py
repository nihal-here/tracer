import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}

@patch("app.main.GitHubRepository.from_url")
def test_investigate_endpoint_eager_error(mock_from_url):
    from app.services.github import InvalidGitHubURLError
    mock_from_url.side_effect = InvalidGitHubURLError("Bad URL")

    payload = {
        "repo": "https://github.com/bad/url/issues/1",
        "question": "What is this repo?"
    }
    response = client.post("/investigate", json=payload)

    assert response.status_code == 400
    assert response.json() == {"detail": "Bad URL"}

def test_sse_adapter_mapping():
    from app.main import _sse_adapter
    from app.investigation_events import InvestigationMetadata, InvestigationFileRead, InvestigationAnswerChunk, InvestigationCompleted

    events = [
        InvestigationFileRead(path="main.py", chars_read=100, cached=False),
        InvestigationMetadata(repo="repo", provider="github", owner="owner", name="name", question="q", description="d", stars=0, language="l", summary="s", readme_available=False, sources=["src"]),
        InvestigationAnswerChunk(chunk="hello"),
        InvestigationCompleted()
    ]

    async def generate_events():
        for e in events:
            yield e

    async def run_adapter():
        return [e async for e in _sse_adapter(generate_events())]

    import asyncio
    result = asyncio.run(run_adapter())

    assert len(result) == 2
    assert "metadata" in result[0]
    assert "src" in result[0]
    assert "chunk" in result[1]
    assert "hello" in result[1]
