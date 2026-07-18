"""
Tests for InvestigationWorkspace.list_directory().

Coverage:
  - Root listing (None, "", ".", trailing slashes)
  - Nested directory listing
  - Immediate children only (not recursive)
  - Lexicographical ordering within each group
  - Directories distinguished from files
  - Traversal rejection (..)
  - Absolute path rejection
  - Nonexistent directory
  - File passed as directory path (not_a_directory)
  - Only allowed_paths exposed
  - Empty repository root listing
  - LIST_DIRECTORY does not add to gathered_evidence
  - LIST_DIRECTORY does not consume MAX_SEARCHES
  - LIST_DIRECTORY available after MAX_SEARCHES exhausted
  - entries_count attribute on successful observation
  - Orchestration: action dispatch and tracing
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from app.services.investigation_workspace import InvestigationWorkspace



from typing import Any

def create_mock_snapshot(tmp_path: Path, files: dict[str, Any]) -> MagicMock:
    snapshot = MagicMock()
    snapshot.root_path = tmp_path
    snapshot.extracted_files = frozenset(files.keys())
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content or "", encoding="utf-8")
    return snapshot


def make_workspace(tmp_path: Path, paths: list[str]) -> InvestigationWorkspace:
    """Create a workspace with only path metadata (no real file content needed for list_directory)."""
    snapshot = MagicMock()
    snapshot.root_path = tmp_path
    # Create real directories so _validate_directory_arg resolve works
    for p in paths:
        full = tmp_path / p
        full.parent.mkdir(parents=True, exist_ok=True)
        full.touch()
    return InvestigationWorkspace(snapshot, allowed_paths=paths)


# ---------------------------------------------------------------------------
# Root listing
# ---------------------------------------------------------------------------

def test_list_directory_root_via_none(tmp_path: Path):
    workspace = make_workspace(tmp_path, ["auth/bearer.py", "main.py", "util/helper.py"])
    obs = workspace.list_directory(None)
    assert obs.result_status == "success"
    assert obs.content is not None
    assert "auth/" in obs.content
    assert "util/" in obs.content
    assert "main.py" in obs.content


def test_list_directory_root_via_empty_string(tmp_path: Path):
    workspace = make_workspace(tmp_path, ["auth/bearer.py", "main.py"])
    obs = workspace.list_directory("")
    assert obs.result_status == "success"
    assert obs.content is not None
    assert "auth/" in obs.content
    assert "main.py" in obs.content


def test_list_directory_root_via_dot(tmp_path: Path):
    workspace = make_workspace(tmp_path, ["auth/bearer.py", "main.py"])
    obs = workspace.list_directory(".")
    assert obs.result_status == "success"
    assert obs.content is not None
    assert "auth/" in obs.content
    assert "main.py" in obs.content


def test_list_directory_root_trailing_slash(tmp_path: Path):
    workspace = make_workspace(tmp_path, ["auth/bearer.py", "main.py"])
    obs = workspace.list_directory("./")
    # ./ strips to "." which is root
    assert obs.result_status == "success"


def test_list_directory_subdir_trailing_slash(tmp_path: Path):
    workspace = make_workspace(tmp_path, ["auth/bearer.py", "auth/strategy/jwt.py"])
    obs = workspace.list_directory("auth/")
    assert obs.result_status == "success"
    assert obs.content is not None
    assert "bearer.py" in obs.content
    assert "strategy/" in obs.content


# ---------------------------------------------------------------------------
# Nested listing
# ---------------------------------------------------------------------------

def test_list_directory_nested(tmp_path: Path):
    paths = [
        "auth/__init__.py",
        "auth/bearer.py",
        "auth/strategy/jwt.py",
        "auth/strategy/base.py",
        "auth/transport/cookie.py",
        "main.py",
    ]
    workspace = make_workspace(tmp_path, paths)

    obs = workspace.list_directory("auth")
    assert obs.result_status == "success"
    assert obs.content is not None

    # Immediate children: files and dirs in auth/
    assert "__init__.py" in obs.content
    assert "bearer.py" in obs.content
    assert "strategy/" in obs.content
    assert "transport/" in obs.content

    # Should NOT show descendants
    assert "jwt.py" not in obs.content
    assert "cookie.py" not in obs.content

    # main.py is NOT under auth/
    assert "main.py" not in obs.content


def test_list_directory_immediate_children_only(tmp_path: Path):
    """list_directory must not recurse."""
    paths = ["a/b/c/deep.py", "a/top.py"]
    workspace = make_workspace(tmp_path, paths)

    obs = workspace.list_directory("a")
    assert obs.result_status == "success"
    assert obs.content is not None
    assert "b/" in obs.content
    assert "top.py" in obs.content
    assert "c/" not in obs.content
    assert "deep.py" not in obs.content


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def test_list_directory_lexicographical_ordering(tmp_path: Path):
    """Directories before files; each group sorted lexicographically."""
    paths = [
        "pkg/z_dir/file.py",
        "pkg/a_dir/file.py",
        "pkg/m_file.py",
        "pkg/a_file.py",
        "pkg/z_file.py",
    ]
    workspace = make_workspace(tmp_path, paths)
    obs = workspace.list_directory("pkg")
    assert obs.result_status == "success"
    assert obs.content is not None
    content = obs.content
    # All dirs appear before all files
    dirs_section_end = content.find("Files:")
    dirs_section = content[:dirs_section_end]
    assert "a_dir/" in dirs_section
    assert "z_dir/" in dirs_section
    # a_dir before z_dir
    assert dirs_section.index("a_dir/") < dirs_section.index("z_dir/")
    # Files section: a_file before m_file before z_file
    files_section = content[dirs_section_end:]
    assert files_section.index("a_file.py") < files_section.index("m_file.py") < files_section.index("z_file.py")


# ---------------------------------------------------------------------------
# Rejection cases
# ---------------------------------------------------------------------------

def test_list_directory_rejects_traversal(tmp_path: Path):
    workspace = make_workspace(tmp_path, ["main.py"])
    obs = workspace.list_directory("../outside")
    assert obs.result_status == "invalid_path"
    assert workspace.invalid_actions == 1


def test_list_directory_rejects_traversal_nested(tmp_path: Path):
    workspace = make_workspace(tmp_path, ["main.py"])
    obs = workspace.list_directory("sub/../escape")
    assert obs.result_status == "invalid_path"


def test_list_directory_rejects_absolute(tmp_path: Path):
    workspace = make_workspace(tmp_path, ["main.py"])
    obs = workspace.list_directory("/etc")
    assert obs.result_status == "invalid_path"
    assert workspace.invalid_actions == 1


# ---------------------------------------------------------------------------
# Nonexistent directory
# ---------------------------------------------------------------------------

def test_list_directory_nonexistent(tmp_path: Path):
    """A valid but absent directory returns not_found (not a crash)."""
    workspace = make_workspace(tmp_path, ["main.py"])
    obs = workspace.list_directory("does_not_exist")
    assert obs.result_status == "not_found"
    assert obs.new_evidence_added is False
    # Does NOT count as invalid action
    assert workspace.invalid_actions == 0


# ---------------------------------------------------------------------------
# File passed as directory
# ---------------------------------------------------------------------------

def test_list_directory_file_as_directory(tmp_path: Path):
    """Passing a file path to list_directory returns not_a_directory."""
    workspace = make_workspace(tmp_path, ["main.py"])
    obs = workspace.list_directory("main.py")
    assert obs.result_status == "not_a_directory"
    assert workspace.invalid_actions == 1


# ---------------------------------------------------------------------------
# Only allowed paths exposed
# ---------------------------------------------------------------------------

def test_list_directory_only_allowed_paths(tmp_path: Path):
    """Filesystem files not in allowed_paths must not appear."""
    # Create a file on disk that is NOT in allowed_paths
    hidden = tmp_path / "auth" / "secret.py"
    hidden.parent.mkdir(parents=True, exist_ok=True)
    hidden.write_text("secret")

    allowed = ["auth/bearer.py"]
    snapshot = MagicMock()
    snapshot.root_path = tmp_path
    workspace = InvestigationWorkspace(snapshot, allowed_paths=allowed)

    obs = workspace.list_directory("auth")
    assert obs.result_status == "success"
    assert obs.content is not None
    assert "bearer.py" in obs.content
    assert "secret.py" not in obs.content


# ---------------------------------------------------------------------------
# Empty root
# ---------------------------------------------------------------------------

def test_list_directory_empty_repo(tmp_path: Path):
    """An empty allowed_paths set produces an empty root listing."""
    snapshot = MagicMock()
    snapshot.root_path = tmp_path
    workspace = InvestigationWorkspace(snapshot, allowed_paths=[])
    obs = workspace.list_directory(None)
    assert obs.result_status == "success"
    assert obs.content is not None


# ---------------------------------------------------------------------------
# Budget semantics
# ---------------------------------------------------------------------------

def test_list_directory_does_not_consume_search_budget(tmp_path: Path):
    workspace = make_workspace(tmp_path, ["main.py"])
    workspace.list_directory(None)
    assert workspace.total_searches == 0


def test_list_directory_does_not_add_to_evidence(tmp_path: Path):
    workspace = make_workspace(tmp_path, ["auth/bearer.py", "main.py"])
    workspace.list_directory("auth")
    assert workspace.gathered_evidence == {}
    assert workspace.total_evidence_chars == 0


def test_list_directory_available_after_search_budget_exhausted(tmp_path: Path):
    """After MAX_SEARCHES ripgrep calls, LIST_DIRECTORY still works."""
    files = {"main.py": "token\n"}
    snapshot = MagicMock()
    snapshot.root_path = tmp_path
    (tmp_path / "main.py").write_text("token")
    workspace = InvestigationWorkspace(snapshot, allowed_paths=["main.py"])

    # Exhaust ripgrep budget
    for _ in range(InvestigationWorkspace.MAX_SEARCHES):
        workspace.search_code("token")
    assert workspace.total_searches == InvestigationWorkspace.MAX_SEARCHES

    # LIST_DIRECTORY still works
    obs = workspace.list_directory(None)
    assert obs.result_status == "success"


def test_list_directory_counts_as_progress(tmp_path: Path):
    """A successful list_directory resets consecutive_no_progress."""
    workspace = make_workspace(tmp_path, ["auth/bearer.py"])
    workspace.consecutive_no_progress = 1
    workspace.list_directory("auth")
    assert workspace.consecutive_no_progress == 0


# ---------------------------------------------------------------------------
# Entries count metadata
# ---------------------------------------------------------------------------

def test_list_directory_entries_count_attribute(tmp_path: Path):
    """Successful observations carry a _entries_count attribute for tracing."""
    paths = ["auth/bearer.py", "auth/strategy/jwt.py", "main.py"]
    workspace = make_workspace(tmp_path, paths)
    obs = workspace.list_directory("auth")
    assert obs.result_status == "success"
    # auth/ has: bearer.py (file), strategy/ (dir) → 2 entries
    assert getattr(obs, "_entries_count") == 2


# ---------------------------------------------------------------------------
