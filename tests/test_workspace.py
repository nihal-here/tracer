from unittest.mock import MagicMock
from app.services.investigation_workspace import InvestigationWorkspace

def create_mock_snapshot(tmp_path, files):
    snapshot = MagicMock()
    snapshot.root_path = tmp_path
    snapshot.extracted_files = frozenset(files.keys())
    for file, content in files.items():
        p = tmp_path / file
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    return snapshot

def test_workspace_valid_read(tmp_path):
    files = {"valid.py": "content1"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["valid.py"])

    obs1 = workspace.read_file("valid.py")
    assert obs1.result_status == "success"
    assert obs1.new_evidence_added is True
    assert workspace.actions == 1
    assert workspace.consecutive_no_progress == 0
    assert obs1.content is not None
    assert "content1" in obs1.content
    assert "[File: valid.py | Lines 1-1 of 1]" in obs1.content

def test_workspace_duplicate_read_mechanics(tmp_path):
    files = {"valid.py": "content1"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["valid.py"])

    workspace.read_file("valid.py")
    obs2 = workspace.read_file("valid.py")
    assert obs2.result_status == "already_read"
    assert obs2.new_evidence_added is False
    assert obs2.content is None
    assert workspace.consecutive_no_progress == 1

def test_workspace_invalid_paths(tmp_path):
    files = {}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["valid.py"])

    obs = workspace.read_file("bad.py")
    assert obs.result_status == "invalid_path"
    assert obs.new_evidence_added is False
    assert workspace.invalid_actions == 1
    assert workspace.consecutive_no_progress == 1

def test_workspace_max_unique_files_budget(tmp_path):
    files = {f"f{i}.py": "content" for i in range(10)}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=list(files.keys()))

    for i in range(InvestigationWorkspace.MAX_UNIQUE_FILES):
        workspace.read_file(f"f{i}.py")

    obs = workspace.read_file(f"f{InvestigationWorkspace.MAX_UNIQUE_FILES}.py")
    assert obs.result_status == "budget_exhausted"
    assert obs.new_evidence_added is False
    assert workspace.consecutive_no_progress == 1

def test_workspace_max_file_chars_truncation(tmp_path):
    long_content = "A" * (InvestigationWorkspace.MAX_FILE_CHARS + 100)
    files = {"valid.py": long_content}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["valid.py"])

    obs = workspace.read_file("valid.py")
    assert obs.result_status == "success"
    assert obs.content is not None
    assert len(obs.content) < len(long_content)
    assert "File truncated" in obs.content

    workspace.total_evidence_chars = InvestigationWorkspace.MAX_TOTAL_EVIDENCE_CHARS - 20

    # We add a newline so it forms a complete line that can be returned
    files = {"f1.py": "1234567890\n_this_will_be_cut"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["f1.py", "f2.py"])

    workspace.total_evidence_chars = InvestigationWorkspace.MAX_TOTAL_EVIDENCE_CHARS - 15

    obs = workspace.read_file("f1.py")
    assert obs.content is not None
    assert "1234567890" in obs.content

def test_search_code_literal(tmp_path):
    files = {"main.py": "def foo():\n    print('hello')\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    obs = workspace.search_code("foo")
    assert obs.result_status == "success"
    assert obs.content is not None
    assert "main.py\n  1: def foo():" in obs.content
    assert obs.new_evidence_added is True

def test_search_code_limits(tmp_path):
    files = {"main.py": "\n".join([f"foo {i}" for i in range(100)])}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    obs = workspace.search_code("foo")
    assert obs.content is not None
    assert "Scanning halted due to budget exhaustion" in obs.content
    assert obs.content.count("  ") == InvestigationWorkspace.MAX_SEARCH_RESULTS

def test_search_code_oversized_file_skipped(tmp_path):
    """Files exceeding MAX_FILE_SIZE_BYTES are skipped by ripgrep (--max-filesize)."""
    class SmallFileSizeWorkspace(InvestigationWorkspace):
        MAX_FILE_SIZE_BYTES: int = 10  # only 10 bytes allowed per file

    # File is larger than the limit → ripgrep will skip it
    files = {"big.txt": "123456789012345find_me"}
    snapshot = create_mock_snapshot(tmp_path, files)

    workspace = SmallFileSizeWorkspace(snapshot, allowed_paths=["big.txt"])
    obs = workspace.search_code("find_me")

    assert obs.result_status == "success"
    assert obs.content is not None
    # ripgrep skipped the file → no matches
    assert "No matches found." in obs.content
    assert workspace.total_searches == 1

def test_search_code_max_searches_budget(tmp_path):
    files = {"main.py": "def foo(): pass"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    # Exhaust search budget
    for i in range(InvestigationWorkspace.MAX_SEARCHES):
        _ = workspace.search_code(f"foo{i}")

    assert workspace.can_continue() is True

    # Next search fails
    obs = workspace.search_code("bar")
    assert obs.result_status == "budget_exhausted"

    # Read file still succeeds
    obs_read = workspace.read_file("main.py")
    assert obs_read.result_status == "success"
