import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}

@patch("app.services.investigation_service.GitHubRepository.from_url")
@patch("app.services.investigation_service.select_files")
@patch("app.services.answer_service.generate_answer_stream")
def test_investigate_endpoint(mock_generate_answer_stream, mock_select_files, mock_from_url):
    # 1. Mock the GitHubRepository
    mock_repo = MagicMock()
    mock_repo.owner = "test-owner"
    mock_repo.name = "test-repo"
    mock_repo.revision = "mocksha123"
    mock_repo.default_branch = "main"
    mock_repo.metadata = {
        "stargazers_count": 100,
        "description": "A test repo",
        "language": "Python",
        "default_branch": "main"
    }

    mock_repo.get_readme.return_value = "Hello World"
    mock_repo.list_top_level_files.return_value = ["README.md", "main.py"]
    mock_repo.list_files.return_value = ["README.md", "main.py"]
    mock_repo.read_files.return_value = {"main.py": "print('hi')"}

    mock_from_url.return_value = mock_repo

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
