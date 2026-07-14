import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app
from app.services.investigation_workspace import InvestigationWorkspace

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}

@patch("app.main.GitHubRepository.from_url")
@patch("app.main.RepositorySnapshot")
@patch("app.services.investigation_service.choose_next_action")
@patch("app.services.investigation_service.prepare_answer_stream")
def test_investigate_endpoint(mock_compose, mock_choose, mock_snapshot_cls, mock_from_url):
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
    mock_from_url.return_value = mock_repo

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "main.py").write_text("x=1")
        (root / "README.md").write_text("Hello")

        mock_snapshot = MagicMock()
        mock_snapshot.gh_repo = mock_repo
        mock_snapshot.root_path = root
        mock_snapshot.extracted_files = frozenset(["README.md", "main.py"])
        mock_snapshot_cls.return_value = mock_snapshot

        from app.services.investigation_agent import InvestigationAction, ActionType, AgentDecisionResult
        mock_choose.side_effect = [
            AgentDecisionResult(action=InvestigationAction(action_type=ActionType.READ_FILE, file_path="main.py"), prompt_chars=100, history_chars=10, allowed_paths_chars=5),
            AgentDecisionResult(action=InvestigationAction(action_type=ActionType.FINISH), prompt_chars=100, history_chars=10, allowed_paths_chars=5)
        ]

        from app.services.answer_service import AnswerGeneratorResult
        mock_compose.return_value = AnswerGeneratorResult(
            prompt_chars=500,
            chunk_generator=iter(["This is a ", "mocked answer ", "based on the repo."])
        )

        payload = {
            "repo": "https://github.com/test-owner/test-repo",
            "question": "What is this repo?"
        }

        captured_ws = []
        original_init = InvestigationWorkspace.__init__
        def fake_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            captured_ws.append(self)

        with patch.object(InvestigationWorkspace, "__init__", fake_init):
            response = client.post("/investigate", json=payload)

            ws_instance = captured_ws[0]
            assert ws_instance.iterations == 2  # 1 for READ_FILE, 1 for FINISH
            assert "main.py" in ws_instance.gathered_evidence
            assert ws_instance.gathered_evidence["main.py"] == "x=1"

        assert response.status_code == 200
        text_data = response.text
        assert "test-owner" in text_data
        assert "test-repo" in text_data
        assert "mocked answer" in text_data
        assert "main.py" in text_data

        # Prove the final answer composer receives the locally read evidence
        context = mock_compose.call_args[0][1]
        assert "main.py" in context["file_contents"]
        assert context["file_contents"]["main.py"] == "x=1"


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


@patch("app.main.GitHubRepository.from_url")
@patch("app.main.RepositorySnapshot")
@patch("app.services.investigation_service.choose_next_action")
@patch("app.services.investigation_service.prepare_answer_stream")
def test_investigate_endpoint_mid_stream_failure(mock_compose, mock_choose, mock_snapshot_cls, mock_from_url):
    mock_repo = MagicMock()
    mock_repo.owner = "test-owner"
    mock_repo.name = "test-repo"
    mock_repo.metadata = {}
    mock_repo.get_readme.return_value = None
    mock_repo.list_top_level_files.return_value = []
    mock_from_url.return_value = mock_repo

    mock_snapshot = MagicMock()
    mock_snapshot.gh_repo = mock_repo
    mock_snapshot.extracted_files = frozenset()
    mock_snapshot_cls.return_value = mock_snapshot

    from app.services.investigation_agent import InvestigationAction, ActionType, AgentDecisionResult
    mock_choose.return_value = AgentDecisionResult(action=InvestigationAction(action_type=ActionType.FINISH), prompt_chars=100, history_chars=10, allowed_paths_chars=5)

    def failing_stream(*args, **kwargs):
        yield "Starting answer..."
        raise RuntimeError("Mid-stream LLM failure")

    from app.services.answer_service import AnswerGeneratorResult
    mock_compose.return_value = AnswerGeneratorResult(
        prompt_chars=500,
        chunk_generator=failing_stream()
    )

    payload = {
        "repo": "https://github.com/test/repo",
        "question": "Q"
    }

    with pytest.raises(RuntimeError, match="Mid-stream LLM failure"):
        client.post("/investigate", json=payload)


def test_sse_adapter_mapping():
    from app.main import _sse_adapter
    from app.investigation_events import InvestigationMetadata, InvestigationFileRead, InvestigationAnswerChunk, InvestigationCompleted

    events = [
        InvestigationFileRead(path="main.py", chars_read=100, cached=False),
        InvestigationMetadata(repo="repo", provider="github", owner="owner", name="name", question="q", description="d", stars=0, language="l", summary="s", readme_available=False, sources=["src"]),
        InvestigationAnswerChunk(chunk="hello"),
        InvestigationCompleted()
    ]

    result = list(_sse_adapter(iter(events)))

    assert len(result) == 2
    assert "metadata" in result[0]
    assert "src" in result[0]
    assert "chunk" in result[1]
    assert "hello" in result[1]
