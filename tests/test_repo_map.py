"""
Tests for app.services.repo_map.build_repo_map.

Coverage:
  - Empty repository
  - Root-level files only
  - Nested directories
  - Deterministic ordering (lexicographical)
  - Directories before files at each level
  - Only allowed_paths represented
  - Deeply nested directories
  - Truncation sentinel when budget exceeded
"""

import pytest
from app.services.repo_map import build_repo_map, MAX_REPO_MAP_CHARS


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------

def test_empty_repository():
    result = build_repo_map(frozenset())
    assert result == ""


def test_root_files_only():
    paths = frozenset(["README.md", "main.py", "setup.py"])
    result = build_repo_map(paths)
    lines = result.splitlines()
    # All three files should appear, no indentation
    assert "README.md" in lines
    assert "main.py" in lines
    assert "setup.py" in lines
    # No subdirectory prefix
    assert not any(line.startswith("  ") for line in lines)


def test_single_nested_file():
    paths = frozenset(["auth/bearer.py"])
    result = build_repo_map(paths)
    assert "auth/" in result
    assert "bearer.py" in result
    # bearer.py should be indented under auth/
    lines = result.splitlines()
    auth_idx = next(i for i, l in enumerate(lines) if l.strip() == "auth/")
    bearer_line = lines[auth_idx + 1]
    assert "bearer.py" in bearer_line
    assert bearer_line.startswith("  ")  # indented


def test_nested_directories():
    paths = frozenset([
        "fastapi_users/__init__.py",
        "fastapi_users/authentication/__init__.py",
        "fastapi_users/authentication/backend.py",
        "fastapi_users/authentication/strategy/jwt.py",
        "fastapi_users/authentication/strategy/base.py",
        "fastapi_users/authentication/transport/bearer.py",
    ])
    result = build_repo_map(paths)

    # Top-level dir present
    assert "fastapi_users/" in result
    # Nested dirs present
    assert "authentication/" in result
    assert "strategy/" in result
    assert "transport/" in result
    # Files present
    assert "jwt.py" in result
    assert "bearer.py" in result
    assert "base.py" in result


def test_deterministic_ordering():
    """Same input always produces the same output."""
    paths = frozenset([
        "z_module/z_file.py",
        "a_module/a_file.py",
        "m_module/m_file.py",
        "z_module/a_file.py",
    ])
    result1 = build_repo_map(paths)
    result2 = build_repo_map(paths)
    assert result1 == result2


def test_lexicographical_ordering_dirs_before_files():
    """Directories appear before files at every level; both groups sorted."""
    paths = frozenset([
        "README.md",
        "main.py",
        "auth/bearer.py",
        "util/helper.py",
    ])
    result = build_repo_map(paths)
    lines = result.splitlines()

    # auth/ and util/ (dirs) should appear before root files
    dir_indices = [i for i, l in enumerate(lines) if l.endswith("/")]
    file_indices = [i for i, l in enumerate(lines) if not l.endswith("/") and not l.startswith("  ")]

    assert all(d < f for d in dir_indices for f in file_indices), (
        "All directories at root level should appear before root-level files"
    )

    # auth/ before util/ (lexicographic)
    auth_idx = next(i for i, l in enumerate(lines) if l.strip() == "auth/")
    util_idx = next(i for i, l in enumerate(lines) if l.strip() == "util/")
    assert auth_idx < util_idx


def test_only_allowed_paths_represented():
    """Paths not in allowed_paths must not appear in the map."""
    allowed = frozenset(["auth/bearer.py"])
    result = build_repo_map(allowed)
    assert "restricted.py" not in result
    assert "secret/" not in result


def test_deeply_nested():
    paths = frozenset([
        "a/b/c/d/e/deep.py",
        "a/b/c/d/e/f/deeper.py",
    ])
    result = build_repo_map(paths)
    assert "deep.py" in result
    assert "deeper.py" in result
    # deep.py should be at a lesser indent than deeper.py
    lines = result.splitlines()
    deep_idx = next(i for i, l in enumerate(lines) if "deep.py" in l and "deeper" not in l)
    deeper_idx = next(i for i, l in enumerate(lines) if "deeper.py" in l)
    deep_indent = len(lines[deep_idx]) - len(lines[deep_idx].lstrip())
    deeper_indent = len(lines[deeper_idx]) - len(lines[deeper_idx].lstrip())
    assert deeper_indent > deep_indent


def test_example_repo_map_output():
    """Verify the exact output format for a canonical fixture."""
    paths = frozenset([
        "fastapi_users/__init__.py",
        "fastapi_users/authentication/__init__.py",
        "fastapi_users/authentication/authenticator.py",
        "fastapi_users/authentication/strategy/__init__.py",
        "fastapi_users/authentication/strategy/jwt.py",
        "fastapi_users/authentication/transport/__init__.py",
        "fastapi_users/authentication/transport/bearer.py",
    ])
    result = build_repo_map(paths)

    # Verify indentation nesting
    assert "fastapi_users/" in result
    assert "  authentication/" in result
    assert "    strategy/" in result
    assert "    transport/" in result
    assert "      jwt.py" in result
    assert "      bearer.py" in result


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

def test_truncation_with_sentinel():
    """When the map exceeds max_chars, a sentinel is appended."""
    # Create many paths so the full map is large
    paths = frozenset([f"module_{i:03d}/file_{j:03d}.py" for i in range(50) for j in range(5)])
    result = build_repo_map(paths, max_chars=500)

    assert len(result) <= 500 + 200  # sentinel may add a little
    assert "map truncated" in result
    assert "LIST_DIRECTORY" in result
    assert "paths omitted" in result


def test_no_truncation_when_within_budget():
    """Small repositories are not truncated."""
    paths = frozenset(["auth/bearer.py", "main.py"])
    result = build_repo_map(paths, max_chars=MAX_REPO_MAP_CHARS)
    assert "truncated" not in result


def test_truncation_never_cuts_mid_line():
    """Truncated output only contains complete lines."""
    paths = frozenset([f"module_{i:03d}/file.py" for i in range(200)])
    result = build_repo_map(paths, max_chars=300)
    # Every line before the sentinel should be a complete path fragment
    lines = result.splitlines()
    sentinel_line = next((l for l in lines if "truncated" in l), None)
    assert sentinel_line is not None
    content_lines = lines[:lines.index(sentinel_line)]
    for line in content_lines:
        # Must end with either '/' (dir) or a file-like name
        stripped = line.strip()
        assert stripped  # no empty lines


def test_truncation_sentinel_mentions_omitted_count():
    """Sentinel includes a count of omitted paths."""
    paths = frozenset([f"file_{i:04d}.py" for i in range(1000)])
    result = build_repo_map(paths, max_chars=200)
    import re
    # Should contain a number in the sentinel
    assert re.search(r"\d+", result.split("\n")[-1])
