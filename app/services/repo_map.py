"""
repo_map — deterministic hierarchical repository tree renderer.

Builds a compact, indented representation of a repository's logical
structure from the workspace's ``allowed_paths`` set.  No filesystem
traversal is performed; the map is derived purely from path strings.

Public API
----------
build_repo_map(allowed_paths, max_chars=MAX_REPO_MAP_CHARS) -> str
"""

from __future__ import annotations

from typing import Any

# Default character budget for the rendered map.
# Large enough to hold most real-world repositories (≤500 files at
# ~20 chars/path → ~10 KB), small enough to stay well inside LLM
# context budgets.
MAX_REPO_MAP_CHARS = 8_000

# Indentation unit for each directory level.
_INDENT = "  "


def build_repo_map(
    allowed_paths: frozenset[str],
    max_chars: int = MAX_REPO_MAP_CHARS,
) -> str:
    """
    Build a deterministic hierarchical map of *allowed_paths*.

    Parameters
    ----------
    allowed_paths:
        The workspace's frozenset of repository-relative POSIX file paths.
    max_chars:
        Maximum character budget for the rendered output.  When the full
        map would exceed this, lines are emitted until the budget is
        exhausted and a sentinel line is appended.

    Returns
    -------
    str
        A multi-line string suitable for inclusion in an LLM prompt.

    Guarantees
    ----------
    - Deterministic: identical input always produces identical output.
    - Lexicographical ordering at every level.
    - Only paths from *allowed_paths* appear in the output.
    - No filesystem access.
    - Handles empty repositories (returns an empty string).
    """
    if not allowed_paths:
        return ""

    # -----------------------------------------------------------------------
    # Step 1: Build a nested dict tree (directories as dict, files as None).
    #
    # {"fastapi_users": {"__init__.py": None,
    #                    "authentication": {"authenticator.py": None, ...}}}
    # -----------------------------------------------------------------------
    root: dict[str, Any] = {}

    for path in allowed_paths:
        # Normalise: strip whitespace and collapse repeated slashes
        path = path.strip().strip("/")
        if not path:
            continue
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            if part not in node or not isinstance(node[part], dict):
                node[part] = {}
            node = node[part]
        # Leaf (file): only insert if the key does not already exist as a dir.
        filename = parts[-1]
        if filename not in node:
            node[filename] = None

    # -----------------------------------------------------------------------
    # Step 2: Render the tree into lines, sorted lexicographically.
    #
    # Directories come before files at each level (mimics most tree tools
    # and feels natural when scanning for architectural components).
    # -----------------------------------------------------------------------
    lines: list[str] = []
    _render_node(root, depth=0, lines=lines)

    # -----------------------------------------------------------------------
    # Step 3: Apply character budget.
    # -----------------------------------------------------------------------
    total_paths = len(allowed_paths)
    return _apply_budget(lines, total_paths, max_chars)


def _render_node(node: dict[str, Any], depth: int, lines: list[str]) -> None:
    """Recursively render *node* into *lines* at the given *depth*."""
    prefix = _INDENT * depth

    # Separate directories (dict) from files (None), sort each group.
    dirs = sorted(k for k, v in node.items() if isinstance(v, dict))
    files = sorted(k for k, v in node.items() if v is None)

    for name in dirs:
        lines.append(f"{prefix}{name}/")
        _render_node(node[name], depth + 1, lines)

    for name in files:
        lines.append(f"{prefix}{name}")


def _apply_budget(lines: list[str], total_paths: int, max_chars: int) -> str:
    """
    Assemble *lines* into a string within *max_chars*.

    If the full map fits, returns it unchanged.
    Otherwise emits complete lines until the budget minus the sentinel
    is exhausted, then appends a truncation sentinel.

    The sentinel explicitly tells the model that omitted paths exist and
    that LIST_DIRECTORY can reach them.
    """
    full_text = "\n".join(lines)
    if len(full_text) <= max_chars:
        return full_text

    # We need to truncate.  Reserve space for the sentinel line.
    sentinel_template = "...[map truncated: {n} paths omitted — use LIST_DIRECTORY to explore]"
    # Worst-case sentinel length (6-digit path count).
    sentinel_max = len(sentinel_template.format(n=total_paths))

    budget = max_chars - sentinel_max - 1  # -1 for the separating newline

    kept: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + 1  # +1 for the newline
        if used + cost > budget:
            break
        kept.append(line)
        used += cost

    kept_paths = sum(1 for ln in kept if not ln.rstrip("/").endswith("/"))
    # Count all kept paths (files only — dirs don't correspond 1:1 to paths)
    omitted = total_paths - kept_paths
    sentinel = sentinel_template.format(n=max(omitted, 1))

    return "\n".join(kept) + "\n" + sentinel
