from dataclasses import dataclass
from typing import Iterable, Optional
from app.services.repository_snapshot import RepositorySnapshot
import json
import logging
import shutil
import subprocess
import app.investigation_trace as trace_models
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ripgrep output-bound constants
# ---------------------------------------------------------------------------
# Maximum number of raw stdout lines we will read from a single ripgrep
# invocation.  This is the *genuine* memory bound: we use subprocess.Popen
# and read line-by-line, stopping early rather than collecting all stdout
# first.  At ~400 bytes/line and 10 000 lines this caps at ~4 MB; in
# practice we stop much earlier once MAX_SEARCH_RESULTS match-lines are
# accumulated.
_RG_MAX_STDOUT_LINES = 10_000


@dataclass
class AgentObservation:
    action_type: str
    path: Optional[str]
    result_status: str
    content: Optional[str]
    new_evidence_added: bool


@dataclass
class SearchMatch:
    path: str
    line_number: int
    line_text: str


def _rg_binary() -> str:
    """Return the path to the ripgrep binary, or raise if unavailable."""
    rg = shutil.which("rg")
    if rg is None:
        raise FileNotFoundError(
            "ripgrep (rg) is not available on PATH. "
            "Install it (e.g. brew install ripgrep) to use search_code."
        )
    return rg


# ---------------------------------------------------------------------------
# Directory-path validation helper (shared by search_code and list_directory)
# ---------------------------------------------------------------------------

def _validate_directory_arg(raw: str | None, root: Path) -> Path | None:
    """
    Validate *raw* as a repository-relative directory path.

    Returns
    -------
    None
        When *raw* is None, ``""`` or ``"."`` — callers should use repo root.
    Path
        Resolved absolute path inside *root*.

    Raises
    ------
    ValueError
        For absolute paths, ``..`` traversal, or paths that escape *root*.
    """
    if raw is None:
        return None

    stripped = raw.strip().rstrip("/")

    if stripped in ("", "."):
        return None

    if stripped.startswith("/"):
        raise ValueError(
            f"Path must be repository-relative, not absolute: {raw!r}"
        )

    try:
        pure = PurePosixPath(stripped)
    except Exception:
        raise ValueError(f"Path is not valid: {raw!r}")

    for part in pure.parts:
        if part == "..":
            raise ValueError(
                f"Path must not contain '..' traversal: {raw!r}"
            )

    candidate = (root / stripped).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError(f"Path escapes the repository root: {raw!r}")

    return candidate


class InvestigationWorkspace:
    MAX_ACTIONS: int = 8
    MAX_UNIQUE_FILES: int = 8
    MAX_FILE_CHARS: int = 20_000
    MAX_TOTAL_EVIDENCE_CHARS: int = 80_000
    MAX_INVALID_ACTIONS: int = 3
    MAX_CONSECUTIVE_NO_PROGRESS: int = 2

    # Search Budgets
    MAX_SEARCHES: int = 4
    MAX_SEARCH_RESULTS: int = 50
    MAX_RESULT_LINE_CHARS: int = 200
    # Per-file size limit passed to ripgrep (--max-filesize).
    MAX_FILE_SIZE_BYTES: int = 10_485_760  # 10 MiB per file

    # LIST_DIRECTORY budget policy
    # ----------------------------
    # LIST_DIRECTORY has NO independent counter.  Every call costs one
    # investigation iteration (enforced by the outer while loop in the
    # service, which checks can_continue() / MAX_ACTIONS=8 before each
    # action).  This is the simplest defensible policy because:
    #   - The iteration cap already bounds total LIST_DIRECTORY calls.
    #   - LIST_DIRECTORY is cheap (pure Python over allowed_paths).
    #   - It should NOT consume the MAX_SEARCHES ripgrep budget.
    #   - A separate counter would add complexity without adding safety.
    # Reaching MAX_SEARCHES still allows READ_FILE and LIST_DIRECTORY as
    # long as MAX_ACTIONS has not been exhausted.

    def __init__(self, snapshot: RepositorySnapshot, allowed_paths: Iterable[str]):
        self.snapshot = snapshot
        self.allowed_paths = frozenset(allowed_paths)

        self.actions = 0
        self.past_searches: set[tuple[str, str | None, bool]] = set()
        self.past_listings: set[str] = set()
        self.invalid_actions = 0
        self.consecutive_no_progress = 0
        self.total_evidence_chars = 0
        self.total_searches = 0

        self.gathered_evidence: dict[str, str] = {}
        self.history: list[AgentObservation] = []
        self.is_finished = False

    def can_continue(self) -> bool:
        if self.is_finished:
            return False
        if self.actions >= self.MAX_ACTIONS:
            return False
        if self.invalid_actions >= self.MAX_INVALID_ACTIONS:
            return False
        if self.consecutive_no_progress >= self.MAX_CONSECUTIVE_NO_PROGRESS:
            return False
        if len(self.gathered_evidence) >= self.MAX_UNIQUE_FILES:
            return False
        if self.total_evidence_chars >= self.MAX_TOTAL_EVIDENCE_CHARS:
            return False
        return True

    def get_termination_reason(self) -> 'trace_models.TerminationReason | None':
        if self.actions >= self.MAX_ACTIONS:
            return trace_models.TerminationReason.MAX_ACTIONS
        if self.invalid_actions >= self.MAX_INVALID_ACTIONS:
            return trace_models.TerminationReason.MAX_INVALID_ACTIONS
        if self.consecutive_no_progress >= self.MAX_CONSECUTIVE_NO_PROGRESS:
            return trace_models.TerminationReason.CONSECUTIVE_NO_PROGRESS
        if len(self.gathered_evidence) >= self.MAX_UNIQUE_FILES:
            return trace_models.TerminationReason.MAX_UNIQUE_FILES
        if self.total_evidence_chars >= self.MAX_TOTAL_EVIDENCE_CHARS:
            return trace_models.TerminationReason.MAX_EVIDENCE_CHARS
        return None

    def record_action(self):
        self.actions += 1

    # -------------------------------------------------------------------------
    # READ_FILE
    # -------------------------------------------------------------------------

    def read_file(self, requested_path: str | None) -> AgentObservation:

        self.actions += 1

        if not requested_path:
            obs = AgentObservation(
                action_type="read_file",
                path=None,
                result_status="invalid_path",
                content=None,
                new_evidence_added=False
            )
            self.invalid_actions += 1
            self._record_no_progress(obs)
            return obs

        path = requested_path.strip()

        if path not in self.allowed_paths:
            obs = AgentObservation(
                action_type="read_file",
                path=path,
                result_status="invalid_path",
                content=None,
                new_evidence_added=False
            )
            self.invalid_actions += 1
            self._record_no_progress(obs)
            return obs

        if path in self.gathered_evidence:
            obs = AgentObservation(
                action_type="read_file",
                path=path,
                result_status="already_read",
                content=None,
                new_evidence_added=False
            )
            self._record_no_progress(obs)
            return obs

        # Budget Check before fetching
        if len(self.gathered_evidence) >= self.MAX_UNIQUE_FILES:
            obs = AgentObservation(
                action_type="read_file",
                path=path,
                result_status="budget_exhausted",
                content=None,
                new_evidence_added=False
            )
            self._record_no_progress(obs)
            return obs

        remaining_capacity = self.MAX_TOTAL_EVIDENCE_CHARS - self.total_evidence_chars
        if remaining_capacity <= 0:
            obs = AgentObservation(
                action_type="read_file",
                path=path,
                result_status="budget_exhausted",
                content=None,
                new_evidence_added=False
            )
            self._record_no_progress(obs)
            return obs

        # Fetch from local disk
        assert self.snapshot.root_path is not None
        target_path = self.snapshot.root_path / path
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            obs = AgentObservation(
                action_type="read_file",
                path=path,
                result_status="binary_file",
                content=None,
                new_evidence_added=False
            )
            self._record_no_progress(obs)
            return obs
        except OSError:
            obs = AgentObservation(
                action_type="read_file",
                path=path,
                result_status="read_error",
                content=None,
                new_evidence_added=False
            )
            self.invalid_actions += 1
            self._record_no_progress(obs)
            return obs

        # Truncate
        retention_limit = min(self.MAX_FILE_CHARS, remaining_capacity)
        if len(content) > retention_limit:
            trunc_msg = "\n...[Truncated]"
            if retention_limit > len(trunc_msg):
                content = content[:retention_limit - len(trunc_msg)] + trunc_msg
            else:
                content = content[:retention_limit]

        self.gathered_evidence[path] = content
        self.total_evidence_chars += len(content)

        obs = AgentObservation(
            action_type="read_file",
            path=path,
            result_status="success",
            content=content,
            new_evidence_added=True
        )
        self.consecutive_no_progress = 0
        self.history.append(obs)
        return obs

    # -------------------------------------------------------------------------
    # LIST_DIRECTORY
    # -------------------------------------------------------------------------

    def list_directory(self, requested_path: str | None) -> AgentObservation:
        self.actions += 1
        """
        Return the immediate children of a repository-relative directory.

        The listing is derived purely from ``allowed_paths`` — no filesystem
        traversal is performed.  Only files and directories visible through
        the workspace's allowed-paths set are returned.

        Budget
        ------
        LIST_DIRECTORY consumes one investigation iteration (enforced by the
        caller's loop) but does NOT consume MAX_SEARCHES or add to
        gathered_evidence / total_evidence_chars.

        Parameters
        ----------
        requested_path:
            Repository-relative directory path.  None, ``""``, and ``"."``
            mean the repository root.  Trailing slashes are stripped.
            Absolute paths and ``..`` traversal are rejected.
        """
        # Validate the path argument
        assert self.snapshot.root_path is not None
        root = self.snapshot.root_path.resolve()

        try:
            scope_abs = _validate_directory_arg(requested_path, root)
        except ValueError as exc:
            obs = AgentObservation(
                action_type="list_directory",
                path=None,
                result_status="invalid_path",
                content=str(exc),
                new_evidence_added=False
            )
            self.invalid_actions += 1
            self._record_no_progress(obs)
            return obs

        # Determine the normalised logical prefix we need to match.
        # scope_abs is None  → root listing: prefix = ""
        # scope_abs is Path  → sub-directory: prefix = "auth/strategy" (no trailing slash)
        if scope_abs is None:
            logical_prefix = ""
        else:
            try:
                logical_prefix = str(scope_abs.relative_to(root))
            except ValueError:
                # Should never happen given _validate_directory_arg passed
                obs = AgentObservation(
                    action_type="list_directory",
                    path=None,
                    result_status="invalid_path",
                    content="Path resolution error.",
                    new_evidence_added=False
                )
                self.invalid_actions += 1
                self._record_no_progress(obs)
                return obs

        # Enumerate immediate children from allowed_paths
        # A child is a path that:
        #   - starts with (prefix + "/")  OR  is a root file (prefix == "")
        #   - the portion after the prefix contains exactly one path component
        #     (immediate child, not a descendant)
        child_dirs: set[str] = set()
        child_files: set[str] = set()

        prefix_with_slash = (logical_prefix + "/") if logical_prefix else ""
        prefix_len = len(prefix_with_slash)

        for ap in self.allowed_paths:
            if logical_prefix:
                # Must start with the prefix + separator
                if not ap.startswith(prefix_with_slash):
                    continue
                remainder = ap[prefix_len:]
            else:
                # Root listing
                remainder = ap

            # remainder should never be empty for a valid path, but guard
            if not remainder:
                continue

            slash_pos = remainder.find("/")
            if slash_pos == -1:
                # Direct file child
                child_files.add(remainder)
            else:
                # Subdirectory child
                child_dirs.add(remainder[:slash_pos] + "/")

        # Check: if the requested path corresponds to a file in allowed_paths,
        # reject it immediately as "not a directory" regardless of children.
        if logical_prefix and logical_prefix in self.allowed_paths:
            obs = AgentObservation(
                action_type="list_directory",
                path=requested_path,
                result_status="not_a_directory",
                content=f"{logical_prefix!r} is a file, not a directory.",
                new_evidence_added=False
            )
            self.invalid_actions += 1
            self._record_no_progress(obs)
            return obs

        # If no children were found for a non-root scope, it means the directory
        # either doesn't exist or has no allowed files under it.
        if logical_prefix and not child_dirs and not child_files:
            obs = AgentObservation(
                action_type="list_directory",
                path=requested_path,
                result_status="not_found",
                content=f"No entries found for directory: {logical_prefix!r}",
                new_evidence_added=False
            )
            # not_found is not an invalid action (valid operation, no such dir)
            self._record_no_progress(obs)
            return obs

        # Format output
        sorted_dirs = sorted(child_dirs)
        sorted_files = sorted(child_files)

        output_lines: list[str] = []
        if sorted_dirs:
            output_lines.append("Directories:")
            output_lines.extend(f"  {d}" for d in sorted_dirs)
        if sorted_files:
            output_lines.append("Files:")
            output_lines.extend(f"  {f}" for f in sorted_files)

        if not output_lines:
            # Root listing with zero allowed paths — empty repository
            content = "(empty)"
        else:
            content = "\n".join(output_lines)

        entries_count = len(child_dirs) + len(child_files)

        obs = AgentObservation(
            action_type="list_directory",
            path=requested_path,
            result_status="success",
            content=content,
            new_evidence_added=True,  # Counts as forward progress
        )
        # Store entry count as extra metadata in the observation for tracing.
        # We tag it on as an attribute so the trace layer can access it without
        # parsing the content string.
        object.__setattr__(obs, "_entries_count", entries_count) # pyright: ignore
        self.consecutive_no_progress = 0
        self.history.append(obs)
        return obs

    # -------------------------------------------------------------------------
    # SEARCH_CODE (ripgrep-backed)
    # -------------------------------------------------------------------------

    def search_code(
        self,
        query: str | None,
        case_sensitive: bool = False,
        target_directory: str | None = None,
    ) -> AgentObservation:
        self.actions += 1
        """
        Search for literal occurrences of *query* in the repository using
        ripgrep.  Results are filtered to the workspace's ``allowed_paths``
        set so ripgrep cannot surface files outside the workspace boundary.

        The subprocess is run via ``subprocess.Popen`` with line-by-line
        reading so that unbounded stdout is never fully buffered in memory.
        Reading stops as soon as ``MAX_SEARCH_RESULTS`` match records have
        been collected or ``_RG_MAX_STDOUT_LINES`` raw lines have been read
        (whichever comes first), then the process is killed.

        Parameters
        ----------
        query:
            Literal string to search for.  Must not be None or empty.
        case_sensitive:
            When True, the search is case-sensitive.  Default is False.
        target_directory:
            Optional repository-relative directory path.  None / ``""`` /
            ``"."`` → search the entire repository root.
        """
        # --- 1. Empty / invalid query ---
        if not query:
            obs = AgentObservation(
                action_type="search_code",
                path=target_directory,
                result_status="invalid_query",
                content=None,
                new_evidence_added=False
            )
            self.invalid_actions += 1
            self._record_no_progress(obs)
            return obs

        search_key = (query, target_directory, case_sensitive)
        if search_key in self.past_searches:
            obs = AgentObservation(
                action_type="search_code",
                path=target_directory,
                result_status="already_searched",
                content=None,
                new_evidence_added=False
            )
            self._record_no_progress(obs)
            return obs
        self.past_searches.add(search_key)

        # --- 2. Budget gate ---
        if self.total_searches >= self.MAX_SEARCHES:
            obs = AgentObservation(
                action_type="search_code",
                path=None,
                result_status="budget_exhausted",
                content=None,
                new_evidence_added=False
            )
            self._record_no_progress(obs)
            return obs

        # --- 3. Validate target_directory BEFORE consuming the budget slot ---
        assert self.snapshot.root_path is not None
        repo_root = self.snapshot.root_path.resolve()

        try:
            scope_path = _validate_directory_arg(target_directory, repo_root)
        except ValueError as exc:
            obs = AgentObservation(
                action_type="search_code",
                path=None,
                result_status="invalid_scope",
                content=str(exc),
                new_evidence_added=False
            )
            self.invalid_actions += 1
            self._record_no_progress(obs)
            return obs

        # --- 4. Check ripgrep availability ---
        try:
            rg = _rg_binary()
        except FileNotFoundError as exc:
            obs = AgentObservation(
                action_type="search_code",
                path=None,
                result_status="search_engine_unavailable",
                content=str(exc),
                new_evidence_added=False
            )
            self.invalid_actions += 1
            self._record_no_progress(obs)
            return obs

        # --- 5. Budget consumed ---
        self.total_searches += 1

        # --- 6. Determine search root ---
        # If a valid scope was given but does not exist, return zero results
        # without running ripgrep (avoids confusing error output).
        if scope_path is not None and not scope_path.exists():
            obs = AgentObservation(
                action_type="search_code",
                path=None,
                result_status="success",
                content="No matches found.",
                new_evidence_added=True,
            )
            self.consecutive_no_progress = 0
            self.history.append(obs)
            return obs

        search_target = str(scope_path) if scope_path is not None else str(repo_root)

        # --- 7. Build ripgrep argument list (no shell=True) ---
        cmd: list[str] = [
            rg,
            "--json",                          # machine-readable output
            "--fixed-strings",                 # literal match (no regex injection)
            "--max-count", str(self.MAX_SEARCH_RESULTS),   # per-file match cap
            "--max-filesize", str(self.MAX_FILE_SIZE_BYTES),
            "--no-ignore",                     # respect workspace, not .gitignore
            "--hidden",                        # don't skip dotfiles
        ]
        if not case_sensitive:
            cmd.append("--ignore-case")
        cmd += ["--", query, search_target]

        # --- 8. Run ripgrep with *genuine* bounded output ---
        # We use Popen + line-by-line reading so we never allocate more than
        # O(lines_read) bytes regardless of total ripgrep output size.
        matches: list[SearchMatch] = []
        budget_hit = False
        stderr_bytes = b""

        proc: subprocess.Popen[bytes] | None = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert proc.stdout is not None

            lines_read = 0
            for raw_line in proc.stdout:
                lines_read += 1
                if lines_read > _RG_MAX_STDOUT_LINES:
                    budget_hit = True
                    break

                raw_line = raw_line.rstrip(b"\n")
                if not raw_line:
                    continue

                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                if record.get("type") != "match":
                    continue

                data = record.get("data", {})

                # Absolute path from ripgrep → make relative to repo_root
                path_field = data.get("path", {})
                abs_path_str: str | None = path_field.get("text")
                if abs_path_str is None:
                    continue

                try:
                    rel_path = str(Path(abs_path_str).resolve().relative_to(repo_root))
                except ValueError:
                    continue

                # Workspace containment: only emit paths the workspace allows
                if rel_path not in self.allowed_paths:
                    continue

                line_number: int = data.get("line_number", 0)

                # lines field is either {"text": "..."} or {"bytes": "<base64>"}
                lines_field = data.get("lines", {})
                line_text_raw: str
                if "text" in lines_field:
                    line_text_raw = lines_field["text"]
                else:
                    # Non-UTF-8 line: fall back to a safe placeholder
                    line_text_raw = "<binary line>"

                line_text = line_text_raw.rstrip("\n").rstrip("\r")
                if len(line_text) > self.MAX_RESULT_LINE_CHARS:
                    line_text = line_text[:self.MAX_RESULT_LINE_CHARS] + "..."

                matches.append(SearchMatch(path=rel_path, line_number=line_number, line_text=line_text))

                if len(matches) >= self.MAX_SEARCH_RESULTS:
                    budget_hit = True
                    break

            # Kill the process if we stopped reading early (budget_hit),
            # then collect stderr for error detection.
            if hasattr(proc.stdout, "close"):
                proc.stdout.close()
            try:
                proc.kill()
            except ProcessLookupError:
                pass  # already exited
            _, stderr_bytes = proc.communicate(timeout=5)
            returncode = proc.returncode

        except OSError as exc:
            if proc is not None:
                try:
                    proc.kill()
                    proc.communicate(timeout=5)
                except Exception:
                    pass
            logger.error("ripgrep execution failed: %s", exc)
            obs = AgentObservation(
                action_type="search_code",
                path=None,
                result_status="search_engine_error",
                content=f"ripgrep execution error: {exc}",
                new_evidence_added=False
            )
            self.invalid_actions += 1
            self._record_no_progress(obs)
            return obs

        # rg exit codes: 0 = matches found, 1 = no matches, 2 = error
        # (We kill the process on budget_hit so rc may be non-zero; only treat
        # rc=2 as an error when we did NOT stop early ourselves.)
        if returncode == 2 and not budget_hit and not matches:
            stderr_snippet = stderr_bytes[:200].decode("utf-8", errors="replace")
            logger.error("ripgrep error (rc=2): %s", stderr_snippet)
            obs = AgentObservation(
                action_type="search_code",
                path=None,
                result_status="search_engine_error",
                content=f"ripgrep error: {stderr_snippet}",
                new_evidence_added=False
            )
            self.invalid_actions += 1
            self._record_no_progress(obs)
            return obs

        # --- 9. Format result (same surface as before) ---
        if not matches:
            content = "No matches found."
            if budget_hit:
                content += " (Scanning halted due to budget exhaustion)"
        else:
            lines_out = [f"{m.path}:{m.line_number}: {m.line_text}" for m in matches]
            content = "\n".join(lines_out)
            if budget_hit:
                content += "\n...[Scanning halted due to budget exhaustion]"

        obs = AgentObservation(
            action_type="search_code",
            path=None,
            result_status="success",
            content=content,
            new_evidence_added=True  # Treat searches as progress
        )
        self.consecutive_no_progress = 0
        self.history.append(obs)
        return obs

    def _record_no_progress(self, obs: AgentObservation):
        self.consecutive_no_progress += 1
        self.history.append(obs)
