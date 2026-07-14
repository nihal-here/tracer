from dataclasses import dataclass
from typing import Optional
from app.services.github import GitHubRepository

@dataclass
class AgentObservation:
    action_type: str
    path: Optional[str]
    result_status: str
    content: Optional[str]
    new_evidence_added: bool

class InvestigationWorkspace:
    MAX_ITERATIONS = 8
    MAX_UNIQUE_FILES = 8
    MAX_FILE_CHARS = 20_000
    MAX_TOTAL_EVIDENCE_CHARS = 80_000
    MAX_INVALID_ACTIONS = 3
    MAX_CONSECUTIVE_NO_PROGRESS = 2

    def __init__(self, gh_repo: GitHubRepository, allowed_paths: list[str]):
        self.gh_repo = gh_repo
        self.allowed_paths = frozenset(allowed_paths)
        
        self.iterations = 0
        self.invalid_actions = 0
        self.consecutive_no_progress = 0
        self.total_evidence_chars = 0
        
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

        # Fetch from GitHub
        fetched = self.gh_repo.read_files([path])
        content = fetched.get(path, "")

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

    def _record_no_progress(self, obs: AgentObservation):
        self.consecutive_no_progress += 1
        self.history.append(obs)
