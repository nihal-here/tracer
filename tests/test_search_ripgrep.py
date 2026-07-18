"""
Tests for InvestigationWorkspace.search_code() with the ripgrep backend.

Coverage:
  - Existing unscoped search behaviour (literal match, line format, case sensitivity)
  - MAX_SEARCH_RESULTS cap and budget-exhaustion messages
  - MAX_SEARCHES budget (shared with other searches)
  - No-match behaviour
  - Scoped search (target_directory)
  - Path-component boundary (foo/bar must not match foo/barista)
  - Root scope ("." and "" and trailing slashes collapse to full repo)
  - Traversal rejection (".." in target_directory)
  - Absolute-path rejection
  - Nonexistent-but-valid directory
  - Case-sensitive vs case-insensitive
  - Binary / non-UTF-8 line handling
  - ripgrep unavailable behaviour (mocked)
  - ripgrep execution error (mocked)
  - Pydantic InvestigationAction validation for target_directory
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from app.services.investigation_workspace import InvestigationWorkspace



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from typing import Any

def create_mock_snapshot(tmp_path: Path, files: dict[str, Any]) -> MagicMock:
    """Write *files* into *tmp_path* and return a snapshot mock."""
    snapshot = MagicMock()
    snapshot.root_path = tmp_path
    snapshot.extracted_files = frozenset(files.keys())
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    return snapshot


# ---------------------------------------------------------------------------
# Unscoped search – existing behaviour
# ---------------------------------------------------------------------------

def test_search_code_literal_match(tmp_path: Path):
    """A literal substring is found and formatted as 'path:line: text'."""
    files = {"main.py": "def foo():\n    print('hello')\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    obs = workspace.search_code("foo")

    assert obs.result_status == "success"
    assert obs.new_evidence_added is True
    assert obs.content is not None
    assert "main.py:1: def foo():" in obs.content


def test_search_code_no_match(tmp_path: Path):
    """A query with no hits produces 'No matches found.' without crashing."""
    files = {"main.py": "def bar(): pass\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    obs = workspace.search_code("zzznomatch_unique_9987")

    assert obs.result_status == "success"
    assert obs.new_evidence_added is True
    assert obs.content == "No matches found."


def test_search_code_case_insensitive_default(tmp_path: Path):
    """By default the search is case-insensitive."""
    files = {"code.py": "class FooBar: pass\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["code.py"])

    obs = workspace.search_code("foobar")  # lowercase query, mixed-case file

    assert obs.result_status == "success"
    assert obs.content is not None
    assert "code.py" in obs.content


def test_search_code_case_sensitive_no_match(tmp_path: Path):
    """Case-sensitive mode does not match when casing differs."""
    files = {"code.py": "class FooBar: pass\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["code.py"])

    obs = workspace.search_code("foobar", case_sensitive=True)

    assert obs.result_status == "success"
    assert obs.content == "No matches found."


def test_search_code_case_sensitive_match(tmp_path: Path):
    """Case-sensitive mode matches when casing is exact."""
    files = {"code.py": "class FooBar: pass\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["code.py"])

    obs = workspace.search_code("FooBar", case_sensitive=True)

    assert obs.result_status == "success"
    assert obs.content is not None
    assert "code.py" in obs.content


def test_search_code_max_results_cap(tmp_path: Path):
    """Results are capped at MAX_SEARCH_RESULTS; budget message is appended."""
    # 100 lines each containing "foo" → only MAX_SEARCH_RESULTS are returned
    content = "\n".join([f"foo {i}" for i in range(100)])
    files = {"many.py": content}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["many.py"])

    obs = workspace.search_code("foo")

    assert obs.result_status == "success"
    assert obs.content is not None
    assert "Scanning halted due to budget exhaustion" in obs.content
    assert obs.content.count("many.py:") == InvestigationWorkspace.MAX_SEARCH_RESULTS


def test_search_code_max_searches_budget(tmp_path: Path):
    """After MAX_SEARCHES are consumed, the next call returns budget_exhausted."""
    files = {"main.py": "def foo(): pass"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    for _ in range(InvestigationWorkspace.MAX_SEARCHES):
        obs = workspace.search_code("foo")
        assert obs.result_status == "success"

    assert workspace.total_searches == InvestigationWorkspace.MAX_SEARCHES

    # One more call → budget_exhausted (does NOT consume another search slot)
    obs_over = workspace.search_code("foo")
    assert obs_over.result_status == "budget_exhausted"
    assert obs_over.new_evidence_added is False
    assert workspace.total_searches == InvestigationWorkspace.MAX_SEARCHES

    # read_file is unaffected
    obs_read = workspace.read_file("main.py")
    assert obs_read.result_status == "success"


def test_search_code_empty_query(tmp_path: Path):
    """An empty query is rejected as invalid and does not consume a budget slot."""
    files = {"main.py": "foo"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    obs = workspace.search_code("")

    assert obs.result_status == "invalid_query"
    assert workspace.total_searches == 0
    assert workspace.invalid_actions == 1


def test_search_code_none_query(tmp_path: Path):
    """None query is also rejected as invalid."""
    files = {"main.py": "foo"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    obs = workspace.search_code(None)

    assert obs.result_status == "invalid_query"
    assert workspace.total_searches == 0


def test_search_only_returns_allowed_paths(tmp_path: Path):
    """Results from files outside allowed_paths are silently filtered out."""
    files = {
        "allowed.py": "token here\n",
        "restricted.py": "token here\n",
    }
    snapshot = create_mock_snapshot(tmp_path, files)
    # Only allowed.py is in the workspace
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["allowed.py"])

    obs = workspace.search_code("token")

    assert obs.result_status == "success"
    assert obs.content is not None
    assert "allowed.py" in obs.content
    assert "restricted.py" not in obs.content


# ---------------------------------------------------------------------------
# Directory scoping
# ---------------------------------------------------------------------------

def test_search_scoped_to_subdirectory(tmp_path: Path):
    """target_directory restricts results to the given subdirectory."""
    files = {
        "auth/bearer.py": "def get_token(): pass\n",
        "main.py": "# no token here\n",
        "util/helper.py": "def tokenize(): pass\n",
    }
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(
        snapshot,
        allowed_paths=["auth/bearer.py", "main.py", "util/helper.py"]
    )

    obs = workspace.search_code("token", target_directory="auth")

    assert obs.result_status == "success"
    assert obs.content is not None
    assert "auth/bearer.py" in obs.content
    assert "util/helper.py" not in obs.content
    assert "main.py" not in obs.content


def test_search_scope_path_component_boundary(tmp_path: Path):
    """'auth' scope must not match 'authentication' directory."""
    files = {
        "auth/bearer.py": "token\n",
        "authentication/login.py": "token\n",
    }
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(
        snapshot,
        allowed_paths=["auth/bearer.py", "authentication/login.py"]
    )

    obs = workspace.search_code("token", target_directory="auth")

    assert obs.result_status == "success"
    assert obs.content is not None
    assert "auth/bearer.py" in obs.content
    assert "authentication/login.py" not in obs.content


def test_search_scope_trailing_slash_normalised(tmp_path: Path):
    """A trailing slash in target_directory is silently normalised."""
    files = {"auth/bearer.py": "token\n", "main.py": "token\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(
        snapshot, allowed_paths=["auth/bearer.py", "main.py"]
    )

    obs = workspace.search_code("token", target_directory="auth/")

    assert obs.result_status == "success"
    assert obs.content is not None
    assert "auth/bearer.py" in obs.content
    assert "main.py" not in obs.content


def test_search_scope_dot_means_root(tmp_path: Path):
    """target_directory='.' searches the full repository root."""
    files = {"auth/bearer.py": "token\n", "main.py": "token\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(
        snapshot, allowed_paths=["auth/bearer.py", "main.py"]
    )

    obs = workspace.search_code("token", target_directory=".")

    assert obs.result_status == "success"
    assert obs.content is not None
    assert "auth/bearer.py" in obs.content
    assert "main.py" in obs.content


def test_search_scope_empty_string_means_root(tmp_path: Path):
    """target_directory='' is treated as the repository root."""
    files = {"auth/bearer.py": "token\n", "main.py": "token\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(
        snapshot, allowed_paths=["auth/bearer.py", "main.py"]
    )

    obs = workspace.search_code("token", target_directory="")

    assert obs.result_status == "success"
    assert obs.content is not None
    assert "auth/bearer.py" in obs.content
    assert "main.py" in obs.content


def test_search_scope_none_means_root(tmp_path: Path):
    """target_directory=None searches the full repository (default behaviour)."""
    files = {"auth/bearer.py": "token\n", "main.py": "token\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(
        snapshot, allowed_paths=["auth/bearer.py", "main.py"]
    )

    obs = workspace.search_code("token", target_directory=None)

    assert obs.result_status == "success"
    assert obs.content is not None
    assert "auth/bearer.py" in obs.content
    assert "main.py" in obs.content


def test_search_scope_nonexistent_directory(tmp_path: Path):
    """A valid but non-existent directory returns zero results and succeeds."""
    files = {"main.py": "token\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    obs = workspace.search_code("token", target_directory="does_not_exist")

    assert obs.result_status == "success"
    assert obs.content == "No matches found."
    # Nonexistent dir still consumes a search budget slot (valid operation)
    assert workspace.total_searches == 1


# ---------------------------------------------------------------------------
# Directory scoping – rejection cases
# ---------------------------------------------------------------------------

def test_search_scope_rejects_traversal(tmp_path: Path):
    """'..' components in target_directory are rejected as invalid_scope."""
    files = {"main.py": "token\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    obs = workspace.search_code("token", target_directory="../outside")

    assert obs.result_status == "invalid_scope"
    assert obs.new_evidence_added is False
    assert workspace.invalid_actions == 1
    # Does NOT consume a search budget slot
    assert workspace.total_searches == 0


def test_search_scope_rejects_double_dot_inside_path(tmp_path: Path):
    """'sub/../outside' is also rejected."""
    files = {"main.py": "token\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    obs = workspace.search_code("token", target_directory="sub/../outside")

    assert obs.result_status == "invalid_scope"
    assert workspace.total_searches == 0


def test_search_scope_rejects_absolute_path(tmp_path: Path):
    """Absolute target_directory paths are rejected."""
    files = {"main.py": "token\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    obs = workspace.search_code("token", target_directory="/etc")

    assert obs.result_status == "invalid_scope"
    assert obs.new_evidence_added is False
    assert workspace.invalid_actions == 1
    assert workspace.total_searches == 0


# ---------------------------------------------------------------------------
# ripgrep unavailability
# ---------------------------------------------------------------------------

def test_search_code_rg_unavailable(tmp_path: Path):
    """If ripgrep is not on PATH, result_status is 'search_engine_unavailable'."""
    files = {"main.py": "token\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    with patch(
        "app.services.investigation_workspace.shutil.which", return_value=None
    ):
        obs = workspace.search_code("token")

    assert obs.result_status == "search_engine_unavailable"
    assert obs.new_evidence_added is False
    # Does NOT consume a search budget slot
    assert workspace.total_searches == 0
    assert workspace.invalid_actions == 1


# ---------------------------------------------------------------------------
# ripgrep execution error (rc=2)
# ---------------------------------------------------------------------------

def test_search_code_rg_execution_error(tmp_path: Path):
    """A ripgrep rc=2 error is surfaced as 'search_engine_error'."""
    files = {"main.py": "token\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    # Mock Popen to simulate rc=2
    fake_proc = MagicMock()
    fake_proc.stdout = iter([])  # empty stdout iterator
    fake_proc.communicate.return_value = (b"", b"rg: some fatal error\n")
    fake_proc.returncode = 2

    with patch("app.services.investigation_workspace.subprocess.Popen", return_value=fake_proc):
        obs = workspace.search_code("token")

    assert obs.result_status == "search_engine_error"
    assert obs.new_evidence_added is False
    assert workspace.invalid_actions == 1
    # Budget IS consumed because it passed the budget gate
    assert workspace.total_searches == 1


# ---------------------------------------------------------------------------
# Binary / non-UTF-8 line handling
# ---------------------------------------------------------------------------

def test_search_code_binary_line_fallback(tmp_path: Path):
    """A match whose 'lines' field is 'bytes' (non-UTF-8) is emitted safely."""
    files = {"main.py": "token\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    abs_path = str(tmp_path / "main.py")
    # Simulate ripgrep JSON output with a bytes-encoded line
    fake_stdout_lines = [
        json.dumps({
            "type": "match",
            "data": {
                "path": {"text": abs_path},
                "lines": {"bytes": "aGVsbG8="},   # bytes field, no "text"
                "line_number": 1,
                "submatches": [],
            }
        }).encode("utf-8") + b"\n",
        json.dumps({"type": "summary", "data": {}}).encode("utf-8") + b"\n",
    ]

    # Mock Popen with a line iterator
    fake_proc = MagicMock()
    fake_proc.stdout = iter(fake_stdout_lines)
    fake_proc.communicate.return_value = (b"", b"")
    fake_proc.returncode = 0

    with patch("app.services.investigation_workspace.subprocess.Popen", return_value=fake_proc):
        obs = workspace.search_code("token")

    assert obs.result_status == "success"
    assert obs.content is not None
    assert "<binary line>" in obs.content


# ---------------------------------------------------------------------------
# Popen output bounding
# ---------------------------------------------------------------------------

def test_search_code_popen_stops_after_max_results(tmp_path: Path):
    """
    The workspace reads rg output line-by-line and stops after MAX_SEARCH_RESULTS
    match records, then kills the process.  This genuinely bounds memory use.
    """
    files = {"main.py": "token\n"}
    snapshot = create_mock_snapshot(tmp_path, files)
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    abs_path = str(tmp_path / "main.py")

    # Generate more match lines than MAX_SEARCH_RESULTS
    over_limit = InvestigationWorkspace.MAX_SEARCH_RESULTS + 20
    fake_lines = []
    for i in range(over_limit):
        record = json.dumps({
            "type": "match",
            "data": {
                "path": {"text": abs_path},
                "lines": {"text": f"token line {i}\n"},
                "line_number": i + 1,
                "submatches": [{"match": {"text": "token"}, "start": 0, "end": 5}],
            }
        }).encode("utf-8") + b"\n"
        fake_lines.append(record)

    lines_consumed = [0]
    original_iter = iter(fake_lines)

    def counting_iter():
        for line in original_iter:
            lines_consumed[0] += 1
            yield line

    fake_proc = MagicMock()
    fake_proc.stdout = counting_iter()
    fake_proc.communicate.return_value = (b"", b"")
    fake_proc.returncode = 0

    with patch("app.services.investigation_workspace.subprocess.Popen", return_value=fake_proc):
        obs = workspace.search_code("token")

    assert obs.result_status == "success"
    assert obs.content is not None
    # Must have stopped at MAX_SEARCH_RESULTS, not read all over_limit lines
    assert lines_consumed[0] <= InvestigationWorkspace.MAX_SEARCH_RESULTS + 1  # +1 for the line that trips the limit
    assert obs.content.count("main.py:") == InvestigationWorkspace.MAX_SEARCH_RESULTS
    assert "budget exhaustion" in obs.content
