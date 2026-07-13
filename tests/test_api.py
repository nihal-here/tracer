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
@patch("app.services.answer_service.generate_answer_stream")
def test_investigate_endpoint(mock_generate_answer_stream, mock_select_files, mock_github_get):
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

    # 3. Mock the Gemini API Answer Generation Stream
    mock_generate_answer_stream.return_value = iter(["This is a ", "mocked answer ", "based on the repo."])

    # 4. Perform the test request
    payload = {
        "repo": "https://github.com/test-owner/test-repo",
        "question": "What is this repo?"
    }
    response = client.post("/investigate", json=payload)
    
    # 5. Assert the response is what we expect
    assert response.status_code == 200
    text_data = response.text
    assert "test-owner" in text_data
    assert "test-repo" in text_data
    assert "mocked answer" in text_data
    assert "main.py" in text_data
