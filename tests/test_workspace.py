import pytest
from unittest.mock import MagicMock
from app.services.investigation_workspace import InvestigationWorkspace

def test_workspace_duplicate_read_mechanics():
    mock_repo = MagicMock()
    mock_repo.read_files.return_value = {"valid.py": "content1"}

    workspace = InvestigationWorkspace(mock_repo, allowed_paths=["valid.py"])
    
    # First read
    obs1 = workspace.read_file("valid.py")
    assert obs1.result_status == "success"
    assert obs1.new_evidence_added is True
    assert workspace.iterations == 0  # no longer incremented in read_file
    assert workspace.consecutive_no_progress == 0
    mock_repo.read_files.assert_called_once_with(["valid.py"])

    # Second read (duplicate)
    obs2 = workspace.read_file("valid.py")
    assert obs2.result_status == "already_read"
    assert obs2.new_evidence_added is False
    assert obs2.content is None
    assert workspace.consecutive_no_progress == 1
    
    # Ensure GitHub API was NOT called again
    assert mock_repo.read_files.call_count == 1
    
def test_workspace_invalid_path_mechanics():
    mock_repo = MagicMock()
    workspace = InvestigationWorkspace(mock_repo, allowed_paths=["valid.py"])
    
    obs = workspace.read_file("bad.py")
    assert obs.result_status == "invalid_path"
    assert obs.new_evidence_added is False
    assert workspace.invalid_actions == 1
    assert workspace.consecutive_no_progress == 1
    mock_repo.read_files.assert_not_called()

def test_workspace_no_progress_reset_and_termination():
    mock_repo = MagicMock()
    mock_repo.read_files.return_value = {"valid.py": "content"}
    workspace = InvestigationWorkspace(mock_repo, allowed_paths=["valid.py"])
    
    workspace.read_file("bad.py")
    assert workspace.consecutive_no_progress == 1
    
    workspace.read_file("valid.py")
    assert workspace.consecutive_no_progress == 0 # reset!
    
    workspace.read_file("bad2.py")
    assert workspace.consecutive_no_progress == 1
    workspace.read_file("bad3.py")
    assert workspace.consecutive_no_progress == 2
    
    assert workspace.can_continue() is False

def test_workspace_max_iterations_bound():
    mock_repo = MagicMock()
    workspace = InvestigationWorkspace(mock_repo, allowed_paths=["valid.py"])
    
    workspace.iterations = InvestigationWorkspace.MAX_ITERATIONS
    assert workspace.can_continue() is False

def test_workspace_unique_files_bound():
    mock_repo = MagicMock()
    workspace = InvestigationWorkspace(mock_repo, allowed_paths=["f1.py", "f2.py"])
    
    workspace.gathered_evidence = {f"f{i}.py": "content" for i in range(InvestigationWorkspace.MAX_UNIQUE_FILES)}
    assert workspace.can_continue() is False

def test_workspace_max_file_chars_truncation():
    mock_repo = MagicMock()
    long_content = "A" * (InvestigationWorkspace.MAX_FILE_CHARS + 100)
    mock_repo.read_files.return_value = {"valid.py": long_content}
    workspace = InvestigationWorkspace(mock_repo, allowed_paths=["valid.py"])
    
    obs = workspace.read_file("valid.py")
    assert obs.result_status == "success"
    # Content should be truncated to MAX_FILE_CHARS + the truncation message length
    assert len(obs.content) < len(long_content)
    assert "[Truncated]" in obs.content

def test_workspace_max_total_evidence_chars():
    mock_repo = MagicMock()
    workspace = InvestigationWorkspace(mock_repo, allowed_paths=["f1.py", "f2.py"])
    
    # Simulate already having almost all capacity
    workspace.total_evidence_chars = InvestigationWorkspace.MAX_TOTAL_EVIDENCE_CHARS - 10

    mock_repo.read_files.return_value = {"f1.py": "1234567890_this_will_be_cut"}
    obs = workspace.read_file("f1.py")

    assert "1234567890" == obs.content # No room for truncation msg
    assert len(obs.content) == 10
    assert workspace.total_evidence_chars <= InvestigationWorkspace.MAX_TOTAL_EVIDENCE_CHARS
    
    # Try another read when capacity is 0
    workspace.total_evidence_chars = InvestigationWorkspace.MAX_TOTAL_EVIDENCE_CHARS
    obs2 = workspace.read_file("f2.py")
    assert obs2.result_status == "budget_exhausted"
