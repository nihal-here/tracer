from dataclasses import dataclass
from typing import Optional
from app.services.repository_snapshot import RepositorySnapshot
import re
import app.investigation_trace as trace_models

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

class InvestigationWorkspace:
    MAX_ITERATIONS = 8
    MAX_UNIQUE_FILES = 8
    MAX_FILE_CHARS = 20_000
    MAX_TOTAL_EVIDENCE_CHARS = 80_000
    MAX_INVALID_ACTIONS = 3
    MAX_CONSECUTIVE_NO_PROGRESS = 2

    # Phase 6 Search Budgets
    MAX_SEARCHES = 4
    MAX_SEARCH_RESULTS = 50
    MAX_RESULT_LINE_CHARS = 200
    MAX_FILES_SCANNED_PER_SEARCH = 2_000
    MAX_BYTES_SCANNED_PER_SEARCH = 50_000_000

    def __init__(self, snapshot: RepositorySnapshot, allowed_paths: list[str]):
        self.snapshot = snapshot
        self.allowed_paths = frozenset(allowed_paths)

        self.iterations = 0
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
        if self.iterations >= self.MAX_ITERATIONS:
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
        if self.iterations >= self.MAX_ITERATIONS:
            return trace_models.TerminationReason.MAX_ITERATIONS
        if self.invalid_actions >= self.MAX_INVALID_ACTIONS:
            return trace_models.TerminationReason.MAX_INVALID_ACTIONS
        if self.consecutive_no_progress >= self.MAX_CONSECUTIVE_NO_PROGRESS:
            return trace_models.TerminationReason.CONSECUTIVE_NO_PROGRESS
        if len(self.gathered_evidence) >= self.MAX_UNIQUE_FILES:
            return trace_models.TerminationReason.MAX_UNIQUE_FILES
        if self.total_evidence_chars >= self.MAX_TOTAL_EVIDENCE_CHARS:
            return trace_models.TerminationReason.MAX_EVIDENCE_CHARS
        return None

    def record_iteration(self):
        self.iterations += 1

    def read_file(self, requested_path: str | None) -> AgentObservation:

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

    def search_code(self, query: str | None, case_sensitive: bool = False) -> AgentObservation:
        if not query:
            obs = AgentObservation(
                action_type="search_code",
                path=None,
                result_status="invalid_query",
                content=None,
                new_evidence_added=False
            )
            self.invalid_actions += 1
            self._record_no_progress(obs)
            return obs

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

        self.total_searches += 1

        matches = []
        files_scanned = 0
        bytes_scanned = 0
        budget_exhausted = False

        flags = 0 if case_sensitive else re.IGNORECASE
        escaped_query = re.escape(query)
        pattern = re.compile(escaped_query, flags)

        for path in self.allowed_paths:
            if files_scanned >= self.MAX_FILES_SCANNED_PER_SEARCH:
                budget_exhausted = True
                break
            if bytes_scanned >= self.MAX_BYTES_SCANNED_PER_SEARCH:
                budget_exhausted = True
                break
            if len(matches) >= self.MAX_SEARCH_RESULTS:
                budget_exhausted = True
                break

            files_scanned += 1
            assert self.snapshot.root_path is not None
            target_path = self.snapshot.root_path / path

            try:
                with open(target_path, "rb") as f:
                    line_number = 1
                    while True:
                        remaining_budget = self.MAX_BYTES_SCANNED_PER_SEARCH - bytes_scanned
                        if remaining_budget <= 0:
                            budget_exhausted = True
                            break

                        line_bytes = f.readline(remaining_budget)
                        if not line_bytes:
                            break

                        is_partial = False
                        if len(line_bytes) == remaining_budget and not line_bytes.endswith(b'\n'):
                            # Peek to see if we actually ran out of file, or just ran out of budget
                            next_byte = f.read(1)
                            if next_byte:
                                is_partial = True
                                budget_exhausted = True

                        bytes_scanned += len(line_bytes)

                        if is_partial:
                            break

                        try:
                            line_str = line_bytes.decode("utf-8")
                            if pattern.search(line_str):
                                line_text = line_str.strip()
                                if len(line_text) > self.MAX_RESULT_LINE_CHARS:
                                    line_text = line_text[:self.MAX_RESULT_LINE_CHARS] + "..."

                                matches.append(SearchMatch(path=path, line_number=line_number, line_text=line_text))

                                if len(matches) >= self.MAX_SEARCH_RESULTS:
                                    budget_exhausted = True
                                    break
                        except UnicodeDecodeError:
                            # Abort searching this file if it contains invalid utf-8 (binary)
                            break

                        line_number += 1

            except OSError:
                pass # skip unreadable files silently

        # Format the result content
        if not matches:
            content = "No matches found."
            if budget_exhausted:
                content += " (Scanning halted due to budget exhaustion)"
        else:
            lines = [f"{m.path}:{m.line_number}: {m.line_text}" for m in matches]
            content = "\n".join(lines)
            if budget_exhausted:
                content += "\n...[Scanning halted due to budget exhaustion]"

        obs = AgentObservation(
            action_type="search_code",
            path=None,
            result_status="success",
            content=content,
            new_evidence_added=True # Treat searches as progress
        )
        self.consecutive_no_progress = 0
        self.history.append(obs)
        return obs

    def _record_no_progress(self, obs: AgentObservation):
        self.consecutive_no_progress += 1
        self.history.append(obs)
