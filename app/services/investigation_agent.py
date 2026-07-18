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


class EvidenceExcerpt(BaseModel):
    path: str = Field(description="The exact repository-relative path of the file.")
    start_line: int = Field(description="The starting line number (inclusive) of the relevant evidence.")
    end_line: int = Field(description="The ending line number (inclusive) of the relevant evidence.")
    justification: str = Field(description="Why this specific code block is relevant to the final answer.")


class InvestigationResult(BaseModel):
    summary_of_evidence: str = Field(description="Summary of the evidence gathered.")
    delegated_interfaces_discovered: list[str] = Field(
        description="List of any delegated interfaces, abstractions, or functions discovered that defer their concrete implementation (e.g., 'strategy.read_token', 'Repository.save')."
    )
    relevant_excerpts: list[EvidenceExcerpt] = Field(
        description="Crucial: Select specific, exact line ranges from the files you actually read via 'read_file' that support your final answer. These excerpts will be injected into the final answer generation step."
    )
    concrete_implementations_read: list[EvidenceExcerpt] = Field(
        description="List of exact excerpts you read that contain the concrete implementations of the delegated interfaces discovered."
    )


INVESTIGATION_MODEL = "google:gemini-3.1-flash-lite"


SYSTEM_PROMPT = """You are an expert software investigator analyzing a code repository.

Investigation strategy:
- Use 'list_directory' to expand a directory when you need to navigate.
- Use 'search_code' for symbol or text discovery. Scope it with 'target_directory' when you expect noisy global results.
- Use 'read_file' around that line to follow delegated implementations. Avoid reading large files from line 1 when search results already identify the relevant location.
- Use narrow line windows around symbols and expand ranges only when needed.

CRITICAL EVIDENCE-COMPLETENESS POLICY:
- If evidence delegates an important mechanism to another symbol, interface, strategy, or abstraction, YOU MUST FOLLOW THAT DEPENDENCY into its concrete implementation before finishing.
- Do NOT finish merely because behavior can be inferred from interface names or abstraction signatures.
- You must actively navigate/search for at least one concrete implementation of the delegated behavior and read its code.
- Your final `InvestigationResult` requires you to explicitly cite the concrete implementation excerpts you read.
- Avoid exhaustive traversal unrelated to the user's question, but core requested mechanisms must be traced down to their concrete logic.

FINAL EVIDENCE GROUNDING:
- You must explicitly select `relevant_excerpts` in your final result.
- Every excerpt you claim MUST have been actually observed via a successful `read_file` tool call during this investigation.
- If you cite unread lines, your result will be rejected.
"""

investigation_agent = Agent(
    INVESTIGATION_MODEL,
    deps_type=AgentDeps,
    output_type=InvestigationResult,
    system_prompt=SYSTEM_PROMPT,
    retries=2
)


@investigation_agent.output_validator
def validate_evidence_completeness(ctx: RunContext[AgentDeps], result: InvestigationResult) -> InvestigationResult:
    """
    Ensures that if the model claims to have discovered delegated interfaces,
    it actually successfully read concrete implementations for them.
    Also validates that ALL claimed excerpts were actually observed in an EvidenceSpan.
    """
    workspace = ctx.deps.workspace

    if not result.relevant_excerpts:
        raise ModelRetry(
            "You did not provide any relevant_excerpts. You must select the exact observed line ranges that support your answer."
        )

    if result.delegated_interfaces_discovered:
        if not result.concrete_implementations_read:
            raise ModelRetry(
                "You discovered delegated interfaces but did not read their concrete implementations. "
                "You must use list_directory or search_code to locate and read the implementation files before finishing."
            )

    # Validate all excerpts against observed EvidenceSpans
    all_excerpts_to_check = result.relevant_excerpts + result.concrete_implementations_read

    for excerpt in all_excerpts_to_check:
        spans_for_path = [s for s in workspace.evidence_spans if s.path == excerpt.path]
        if not spans_for_path:
            raise ModelRetry(
                f"You claimed evidence for {excerpt.path} but never successfully read this file. "
                "You must use `read_file` to observe it before citing it."
            )

        # Check if [excerpt.start_line, excerpt.end_line] is fully covered by the union of spans
        # Since ranges are inclusive, we can check integer coverage
        requested_lines = set(range(excerpt.start_line, excerpt.end_line + 1))
        observed_lines = set()
        for span in spans_for_path:
            observed_lines.update(range(span.start_line, span.end_line + 1))

        unobserved = requested_lines - observed_lines
        if unobserved:
            min_unobs = min(unobserved)
            max_unobs = max(unobserved)
            raise ModelRetry(
                f"You claimed evidence for {excerpt.path}:{excerpt.start_line}-{excerpt.end_line} but lines {min_unobs}-{max_unobs} were never observed. "
                f"You must invoke `read_file` to observe these lines before citing them."
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
def read_file(ctx: RunContext[AgentDeps], file_path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """Fetch a file or line range. Requires 'file_path'. Optional: 'start_line', 'end_line' (1-indexed inclusive)."""
    _check_domain_termination(ctx.deps)

    workspace = ctx.deps.workspace
    t_start = time.perf_counter()
    obs = workspace.read_file(file_path, start_line, end_line)
    duration = time.perf_counter() - t_start

    step_trace = AgentStepTrace(
        action_number=workspace.actions,
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
        action_number=workspace.actions,
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
        action_number=workspace.actions,
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
