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

@patch("app.main.GitHubRepository.from_url")
def test_investigate_endpoint_eager_error(mock_from_url):
    from app.services.github import InvalidGitHubURLError
    mock_from_url.side_effect = InvalidGitHubURLError("Bad URL")

    payload = {
        "repo": "https://github.com/bad/url/issues/1",
        "question": "What is this repo?"
    }
    response = client.post("/investigate", json=payload)

    # 400 because InvalidGitHubURLError is raised eagerly before StreamingResponse is returned
    assert response.status_code == 400
    assert response.json() == {"detail": "Bad URL"}

@patch("app.main.GitHubRepository.from_url")
@patch("app.services.investigation_service.select_files")
@patch("app.services.answer_service.generate_answer_stream")
def test_investigate_endpoint_mid_stream_failure(mock_generate_answer_stream, mock_select_files, mock_from_url):
    mock_repo = MagicMock()
    mock_repo.owner = "test-owner"
    mock_repo.name = "test-repo"
    mock_repo.metadata = {}
    mock_repo.get_readme.return_value = None
    mock_repo.list_top_level_files.return_value = []
    mock_repo.list_files.return_value = []
    mock_repo.read_files.return_value = {}
    mock_from_url.return_value = mock_repo
    mock_select_files.return_value = []

    def failing_stream(*args, **kwargs):
        yield "Starting answer..."
        raise RuntimeError("Mid-stream LLM failure")

    mock_generate_answer_stream.side_effect = failing_stream

    payload = {
        "repo": "https://github.com/test/repo",
        "question": "Q"
    }

    with pytest.raises(RuntimeError, match="Mid-stream LLM failure"):
        client.post("/investigate", json=payload)

def test_sse_adapter_mapping():
    from app.main import _sse_adapter
    from app.investigation_events import InvestigationMetadata, InvestigationFilesSelected, InvestigationAnswerChunk, InvestigationCompleted

    events = [
        InvestigationFilesSelected(files=["main.py"]),
        InvestigationMetadata(repo="repo", provider="github", owner="owner", name="name", question="q", description="d", stars=0, language="l", summary="s", readme_available=False, sources=["src"]),
        InvestigationAnswerChunk(chunk="hello"),
        InvestigationCompleted()
    ]

    result = list(_sse_adapter(iter(events)))

    # FilesSelected and Completed are ignored.
    assert len(result) == 2
    assert "metadata" in result[0]
    assert "src" in result[0]
    assert "chunk" in result[1]
    assert "hello" in result[1]

@patch("app.services.investigation_service.select_files")
@patch("app.services.investigation_service.compose_answer_stream")
def test_canonical_workflow_filtering_and_event_order(mock_compose, mock_select):
    from app.services.investigation_service import run_investigation
    from app.investigation_events import InvestigationMetadata, InvestigationFilesSelected, InvestigationAnswerChunk, InvestigationCompleted

    mock_repo = MagicMock()
    mock_repo.owner = "owner"
    mock_repo.name = "name"
    mock_repo.metadata = {}
    mock_repo.list_top_level_files.return_value = []
    mock_repo.get_readme.return_value = None
    # Provide a raw tree with noise
    mock_repo.list_files.return_value = ["valid.py", "noise.png", "node_modules/bad.js"]
    mock_repo.read_files.return_value = {"valid.py": "code"}

    mock_select.return_value = ["valid.py"]
    mock_compose.return_value = iter(["answer chunk"])

    events = list(run_investigation(mock_repo, "q"))

    # Assert filter_noise was applied before select_files
    mock_select.assert_called_once()
    filtered_tree = mock_select.call_args[0][1]
    assert "valid.py" in filtered_tree
    assert "noise.png" not in filtered_tree
    assert "node_modules/bad.js" not in filtered_tree

    # Assert event order
    assert len(events) == 4
    assert isinstance(events[0], InvestigationFilesSelected)
    assert events[0].files == ["valid.py"]

    assert isinstance(events[1], InvestigationMetadata)
    assert "valid.py" in events[1].sources

    assert isinstance(events[2], InvestigationAnswerChunk)
    assert events[2].chunk == "answer chunk"

    assert isinstance(events[3], InvestigationCompleted)
