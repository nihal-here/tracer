import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}

@patch("app.services.investigation_service._github_get")
@patch("app.services.investigation_service.select_files")
@patch("app.services.answer_service.generate_answer")
@patch("app.services.investigation_service.get_semantic_cache")
@patch("app.services.investigation_service.set_semantic_cache")
def test_investigate_endpoint(mock_set_cache, mock_get_cache, mock_generate_answer, mock_select_files, mock_github_get):
    # Mock cache miss by default
    mock_get_cache.return_value = None
    # 1. Mock the GitHub API helper responses
    def mock_get(endpoint, ignore_404=False):
        if "readme" in endpoint:
            # Mock README base64 string "Hello World"
            return {"content": "SGVsbG8gV29ybGQ="}
        if "contents/" in endpoint:
            # Mock file content base64 string "print('hi')"
            return {"content": "cHJpbnQoJ2hpJyk="}
        if "contents" in endpoint:
            # Mock top level files
            return [{"name": "README.md"}, {"name": "main.py"}]
        if "trees" in endpoint:
            # Mock repo tree
            return {"tree": [{"path": "main.py", "type": "blob"}, {"path": "README.md", "type": "blob"}]}
        
        # Mock repo metadata
        return {
            "stargazers_count": 100,
            "description": "A test repo",
            "language": "Python",
            "default_branch": "main"
        }
    
    mock_github_get.side_effect = mock_get

    # 2. Mock the Gemini API File Selection
    mock_select_files.return_value = ["main.py"]

    # 3. Mock the Gemini API Answer Generation
    mock_generate_answer.return_value = "This is a mocked answer based on the repo."

    # 4. Perform the test request
    payload = {
        "repo": "https://github.com/test-owner/test-repo",
        "question": "What is this repo?"
    }
    response = client.post("/investigate", json=payload)
    
    # 5. Assert the response is what we expect
    assert response.status_code == 200
    data = response.json()
    assert data["owner"] == "test-owner"
    assert data["name"] == "test-repo"
    assert data["stars"] == 100
    assert data["readme_available"] is True
    assert data["answer"] == "This is a mocked answer based on the repo."
    assert "main.py" in data["sources"]
