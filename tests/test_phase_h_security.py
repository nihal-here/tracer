import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services.github import GitHubRepository, InvalidGitHubURLError
from unittest.mock import patch

@patch("app.services.github._github_get")
def test_github_url_validation_success(mock_get):
    mock_get.return_value = {"default_branch": "main", "commit": {"sha": "123"}}
    repo = GitHubRepository.from_url("https://github.com/owner/name")
    assert repo.owner == "owner"
    assert repo.name == "name"
    
    repo = GitHubRepository.from_url("https://github.com/owner/name.git")
    assert repo.owner == "owner"
    assert repo.name == "name"

def test_github_url_validation_failures():
    invalid_urls = [
        "http://github.com/owner/name", # Not HTTPS
        "https://github.com.evil.com/owner/name", # Wrong host
        "https://github.com/owner/name?q=1", # Query string
        "https://github.com/owner/name#frag", # Fragment
        "https://github.com/owner/name/extra", # Extra paths
        "https://github.com/owner/../name", # Traversal
        "https://user:pass@github.com/owner/name", # Credentials
        "https://github.com/owner", # Missing repo name
    ]
    for url in invalid_urls:
        with pytest.raises(InvalidGitHubURLError):
            GitHubRepository.from_url(url)

def test_security_headers():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy" in response.headers

def test_missing_gemini_api_key(monkeypatch):
    from app.services.llm_provider import get_gemini_api_key
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY not set"):
        get_gemini_api_key()

def test_input_length_limits():
    from app.models import InvestigateRequest
    from pydantic import ValidationError
    long_question = "A" * 1001
    with pytest.raises(ValidationError):
        InvestigateRequest.model_validate({"repo": "https://github.com/a/b", "question": long_question})
    
    InvestigateRequest.model_validate({"repo": "https://github.com/a/b", "question": "A" * 1000})

def test_rate_limit(monkeypatch):
    import app.main as main_app
    monkeypatch.setattr(main_app, "RATE_LIMIT_PER_MIN", 2)
    main_app.rate_limit_records.clear()
    
    with TestClient(app) as client:
        r1 = client.post("/investigate", json={"repo": "https://github.com/owner/name", "question": "test"})
        r2 = client.post("/investigate", json={"repo": "https://github.com/owner/name", "question": "test"})
        r3 = client.post("/investigate", json={"repo": "https://github.com/owner/name", "question": "test"})
        
        assert r1.status_code != 429
        assert r2.status_code != 429
        assert r3.status_code == 429

def test_trusted_proxy(monkeypatch):
    import app.main as main_app
    monkeypatch.setattr(main_app, "TRUST_X_FORWARDED_FOR", True)
    monkeypatch.setattr(main_app, "RATE_LIMIT_PER_MIN", 1)
    main_app.rate_limit_records.clear()
    
    with TestClient(app) as client:
        r1 = client.post("/investigate", json={"repo": "https://github.com/owner/name", "question": "test"}, headers={"x-forwarded-for": "10.0.0.1"})
        r2 = client.post("/investigate", json={"repo": "https://github.com/owner/name", "question": "test"}, headers={"x-forwarded-for": "10.0.0.2"})
        assert r1.status_code != 429
        assert r2.status_code != 429

def test_untrusted_proxy(monkeypatch):
    import app.main as main_app
    monkeypatch.setattr(main_app, "TRUST_X_FORWARDED_FOR", False)
    monkeypatch.setattr(main_app, "RATE_LIMIT_PER_MIN", 1)
    main_app.rate_limit_records.clear()
    
    with TestClient(app) as client:
        r1 = client.post("/investigate", json={"repo": "https://github.com/owner/name", "question": "test"}, headers={"x-forwarded-for": "10.0.0.1"})
        r2 = client.post("/investigate", json={"repo": "https://github.com/owner/name", "question": "test"}, headers={"x-forwarded-for": "10.0.0.2"})
        assert r1.status_code != 429
        assert r2.status_code == 429

def test_concurrency_rejection(monkeypatch):
    import app.main as main_app
    monkeypatch.setattr(main_app, "MAX_CONCURRENT", 0)
    monkeypatch.setattr(main_app, "RATE_LIMIT_PER_MIN", 100)
    with TestClient(app) as client:
        r = client.post("/investigate", json={"repo": "https://github.com/owner/name", "question": "test"})
        assert r.status_code == 503
        assert "maximum capacity" in r.json()["detail"]

def test_cache_ttl_cleanup(monkeypatch, tmp_path):
    import time
    import os
    from app.services.repository_snapshot import RepositorySnapshot
    from app.services.github import GitHubRepository
    monkeypatch.setenv("TRACE_SNAPSHOT_CACHE_TTL_SECONDS", "1")
    
    repo = GitHubRepository(owner="owner", name="name", revision="rev", default_branch="main", metadata={})
    
    # Mock materialize so it doesn't do real requests
    monkeypatch.setattr(RepositorySnapshot, "_load_cached_entry", lambda self, entry: None)
    monkeypatch.setattr(RepositorySnapshot, "_do_materialize", lambda self, path, extract: None)
    
    snapshot = RepositorySnapshot(repo, cache_dir=tmp_path)
    
    # Create fake old cache entry
    cache_root = tmp_path / "snapshots"
    cache_root.mkdir()
    old_entry = cache_root / "old_key"
    old_entry.mkdir()
    
    # Set mtime to 10 seconds ago
    old_time = time.time() - 10
    os.utime(old_entry, (old_time, old_time))
    
    assert old_entry.exists()
    snapshot._cleanup_expired_snapshots(cache_root)
    assert not old_entry.exists()
