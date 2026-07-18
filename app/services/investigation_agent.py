import os
from dataclasses import dataclass
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelRetry
from app.services.investigation_workspace import InvestigationWorkspace
from app.investigation_trace import InvestigationTrace, AgentStepTrace
import time

class DomainTerminationException(Exception):
    """Raised when the domain dictates the investigation must stop (e.g. no progress)."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)

@dataclass
class AgentDeps:
    workspace: InvestigationWorkspace
    trace: InvestigationTrace


class InvestigationResult(BaseModel):
    summary_of_evidence: str = Field(description="Summary of the evidence gathered.")
    delegated_interfaces_discovered: list[str] = Field(
        description="List of any delegated interfaces, abstractions, or functions discovered that defer their concrete implementation (e.g., 'strategy.read_token', 'Repository.save')."
    )
    concrete_implementations_read: list[str] = Field(
        description="List of exact file paths read that contain the concrete implementations of the delegated interfaces discovered."
    )


# System prompt with strict evidence-completeness policy
SYSTEM_PROMPT = """You are an expert software investigator analyzing a code repository.

Investigation strategy:
- Use 'list_directory' to expand a directory when you need to navigate.
- Use 'search_code' for symbol or text discovery. Scope it with 'target_directory' when you expect noisy global results.
- Use 'read_file' for direct evidence once you know the exact file path.

CRITICAL EVIDENCE-COMPLETENESS POLICY:
- If evidence delegates an important mechanism to another symbol, interface, strategy, or abstraction (e.g., calling `strategy.read_token(...)` or delegating to a backend), YOU MUST FOLLOW THAT DEPENDENCY into its concrete implementation before finishing.
- Do NOT finish merely because behavior can be inferred from interface names or abstraction signatures.
- You must actively navigate/search for at least one concrete implementation of the delegated behavior and read its code.
- Your final `InvestigationResult` requires you to list the concrete files you read for these delegations.
- Avoid exhaustive traversal unrelated to the user's question, but core requested mechanisms must be traced down to their concrete logic.
"""

investigation_agent = Agent(
    "google:gemini-3.1-flash-lite",
    deps_type=AgentDeps,
    output_type=InvestigationResult,
    system_prompt=SYSTEM_PROMPT,
    retries=2
)


@investigation_agent.output_validator
def validate_evidence_completeness(ctx: RunContext[AgentDeps], result: InvestigationResult) -> InvestigationResult:
    """
    Ensures that if the model claims to have discovered delegated interfaces,
    it actually successfully read concrete implementations for them, and those
    files are genuinely in the gathered evidence.
    """
    if result.delegated_interfaces_discovered:
        if not result.concrete_implementations_read:
            raise ModelRetry(
                "You discovered delegated interfaces but did not read their concrete implementations. "
                "You must use list_directory or search_code to locate and read the implementation files before finishing."
            )

        # Verify the model didn't just hallucinate reading a file
        gathered_files = set(ctx.deps.workspace.gathered_evidence.keys())
        missing = [f for f in result.concrete_implementations_read if f not in gathered_files]
        if missing:
            raise ModelRetry(
                f"You claimed to have read concrete implementations in files {missing}, but they were not successfully read. "
                "You must actually invoke `read_file` on these paths."
            )
    return result


def _check_domain_termination(deps: AgentDeps):
    """Raise DomainTerminationException if the workspace says we can't continue."""
    if not deps.workspace.can_continue():
        reason = deps.workspace.get_termination_reason()
        if reason:
            raise DomainTerminationException(reason.value)
        raise DomainTerminationException("budget_exhausted")


@investigation_agent.tool
def read_file(ctx: RunContext[AgentDeps], file_path: str) -> str:
    """Fetch a complete file. Requires 'file_path' (exact repository-relative path)."""
    _check_domain_termination(ctx.deps)

    workspace = ctx.deps.workspace
    t_start = time.perf_counter()
    obs = workspace.read_file(file_path)
    duration = time.perf_counter() - t_start

    step_trace = AgentStepTrace(
        iteration=workspace.iterations,
        action_chosen="read_file",
        action_arguments={"file_path": str(len(file_path)) + " chars"}, # Privacy mask
        prompt_chars=0, # Deprecated metric under PydanticAI
        history_chars=0, # Deprecated metric
        repo_map_chars=0, # Deprecated metric
        decision_duration_sec=None,
        execution_duration_sec=duration
    )
    # The read_file file_path is already in allowed_paths, so we can log it safely if we want,
    # but privacy mask here just to be safe. We'll use the bound_trace_string equivalent.
    step_trace.action_arguments["file_path"] = (
        file_path if file_path in workspace.allowed_paths else f"unauthorized_path_{len(file_path)}"
    )
    ctx.deps.trace.steps.append(step_trace)

    _check_domain_termination(ctx.deps)
    return obs.content if obs.content is not None else obs.result_status


@investigation_agent.tool
def search_code(ctx: RunContext[AgentDeps], search_query: str, case_sensitive: bool = False, target_directory: str | None = None) -> str:
    """Search for a literal substring. Optionally: 'case_sensitive' (default false), 'target_directory' (restrict scope)."""
    _check_domain_termination(ctx.deps)

    workspace = ctx.deps.workspace
    t_start = time.perf_counter()
    obs = workspace.search_code(search_query, case_sensitive, target_directory)
    duration = time.perf_counter() - t_start

    step_trace = AgentStepTrace(
        iteration=workspace.iterations,
        action_chosen="search_code",
        action_arguments={
            "search_query_chars": str(len(search_query)),
            "case_sensitive": str(case_sensitive)
        },
        prompt_chars=0,
        history_chars=0,
        repo_map_chars=0,
        decision_duration_sec=None,
        execution_duration_sec=duration
    )
    if target_directory:
        step_trace.action_arguments["target_directory_chars"] = str(len(target_directory))
    ctx.deps.trace.steps.append(step_trace)

    _check_domain_termination(ctx.deps)
    return obs.content if obs.content is not None else obs.result_status


@investigation_agent.tool
def list_directory(ctx: RunContext[AgentDeps], directory_path: str | None = None) -> str:
    """List immediate children of a directory. Provide 'directory_path' (or omit/use '.' for root)."""
    _check_domain_termination(ctx.deps)

    workspace = ctx.deps.workspace
    t_start = time.perf_counter()
    obs = workspace.list_directory(directory_path)
    duration = time.perf_counter() - t_start

    step_trace = AgentStepTrace(
        iteration=workspace.iterations,
        action_chosen="list_directory",
        action_arguments={
            "directory_path_chars": str(len(directory_path)) if directory_path else "0"
        },
        prompt_chars=0,
        history_chars=0,
        repo_map_chars=0,
        decision_duration_sec=None,
        execution_duration_sec=duration
    )
    entries = getattr(obs, "_entries_count", None)
    if entries is not None:
        step_trace.action_arguments["entries_returned"] = str(entries)
    ctx.deps.trace.steps.append(step_trace)

    _check_domain_termination(ctx.deps)
    return obs.content if obs.content is not None else obs.result_status
