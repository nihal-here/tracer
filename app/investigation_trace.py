import time
import json
import logging
from dataclasses import dataclass, field, is_dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger("investigation.trace")


class FailureStage(str, Enum):
    REPOSITORY_RESOLUTION = "repository_resolution"
    MATERIALIZATION = "materialization"
    AGENT_DECISION = "agent_decision"
    ACTION_EXECUTION = "action_execution"
    ANSWER_GENERATION = "answer_generation"


class TerminationReason(str, Enum):
    MODEL_FINISHED = "model_finished"
    MAX_ACTIONS = "max_actions"
    MAX_UNIQUE_FILES = "max_unique_files"
    MAX_EVIDENCE_CHARS = "max_evidence_chars"
    MAX_INVALID_ACTIONS = "max_invalid_actions"
    CONSECUTIVE_NO_PROGRESS = "consecutive_no_progress"
    ERROR = "error"
    CLIENT_DISCONNECTED = "client_disconnected"


@dataclass
class ReadFileTraceMetadata:
    requested_path: str
    requested_start_line: int | None
    requested_end_line: int | None
    actual_start_line: int | None
    actual_end_line: int | None
    total_file_lines: int | None
    truncated: bool | None
    returned_chars: int | None

@dataclass
class SearchCodeTraceMetadata:
    query: str
    scope: str | None
    case_sensitive: bool
    matches_returned: int | None
    returned_chars: int | None

@dataclass
class ListDirectoryTraceMetadata:
    directory_path: str
    entries_returned: int | None
    returned_chars: int | None

@dataclass
class AgentStepTrace:
    action_number: int
    action_chosen: str
    action_arguments: dict[str, str]
    prompt_chars: int
    history_chars: int
    repo_map_chars: int
    search_results_count: int | None = None
    decision_duration_sec: float | None = None
    execution_duration_sec: float | None = None
    read_file_metadata: ReadFileTraceMetadata | None = None
    search_code_metadata: SearchCodeTraceMetadata | None = None
    list_directory_metadata: ListDirectoryTraceMetadata | None = None


@dataclass
class ModelRequestUsage:
    """Usage exposed by one completed PydanticAI model response."""

    request_number: int
    input_tokens: int
    output_tokens: int
    cumulative_input_tokens: int
    cumulative_output_tokens: int
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    preceding_tool_results: list[str] = field(default_factory=list)


@dataclass
class InvestigationTrace:
    started_at: str
    question_chars: int

    repository_resolution_duration_sec: float = 0.0
    materialization_duration_sec: float = 0.0
    total_duration_sec: float = 0.0
    answer_generation_duration_sec: float | None = None

    steps: list[AgentStepTrace] = field(default_factory=list)

    final_evidence_files_count: int = 0
    final_evidence_chars: int = 0 # Deprecated/Replaced below
    evidence_file_paths: list[str] = field(default_factory=list)
    cached_investigation_tool_sequence: list[str] = field(default_factory=list)
    answer_chunks_emitted: int = 0

    # Phase F Observability
    observed_evidence_chars: int = 0
    observed_evidence_spans_count: int = 0
    relevant_excerpts_count: int = 0
    final_selected_evidence_chars: int = 0
    citation_count: int = 0
    cited_evidence_chars: int = 0
    public_trace_step_count: int = 0
    answer_citation_ids: list[str] = field(default_factory=list)
    unknown_answer_citation_ids: list[str] = field(default_factory=list)
    malformed_answer_citations: list[str] = field(default_factory=list)
    answer_citations_valid: bool | None = None

    # PydanticAI metrics
    model_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model_request_usage: list[ModelRequestUsage] = field(default_factory=list)

    investigation_cache_hit: bool = False
    investigation_cache_lookup_duration_sec: float = 0.0
    investigation_cache_write_duration_sec: float = 0.0
    repository_snapshot_cache_hit: bool = False
    repository_cache_lookup_duration_sec: float = 0.0
    final_prompt_chars: int = 0

    termination_reason: TerminationReason | None = None
    failure_stage: FailureStage | None = None
    error_type: str | None = None

    _start_time: float = field(default=0.0, repr=False)
    _emitted: bool = field(default=False, repr=False)


def bound_trace_string(val: str | None, max_len: int = 100) -> str:
    if val is None:
        return "None"
    return val if len(val) <= max_len else val[:max_len] + "...[Truncated]"


def _dataclass_to_dict_safe(obj: Any) -> dict[str, Any]:
    """
    Recursively converts a dataclass to a dict, excluding private fields starting with `_`,
    and resolving Enum values to strings explicitly.
    """
    if not is_dataclass(obj):
        raise ValueError("Expected a dataclass")

    result = {}
    for field_name in obj.__dataclass_fields__:
        if field_name.startswith("_"):
            continue

        value = getattr(obj, field_name)

        if is_dataclass(value):
            result[field_name] = _dataclass_to_dict_safe(value)
        elif isinstance(value, list) and all(is_dataclass(v) for v in value):
            result[field_name] = [_dataclass_to_dict_safe(v) for v in value]
        elif isinstance(value, Enum):
            result[field_name] = value.value
        else:
            result[field_name] = value

    return result


def trace_to_dict(trace: InvestigationTrace) -> dict[str, Any]:
    return _dataclass_to_dict_safe(trace)


def record_model_request_usage(trace: InvestigationTrace, result: Any) -> None:
    """Record public PydanticAI per-response usage without provider internals.

    ``AgentRunResult.all_messages()`` is a supported API. Each public
    ``ModelResponse`` carries a public ``RequestUsage`` object. A response's
    preceding ``ToolReturnPart`` values identify the tool results that were
    present before that model request.
    """
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart

    try:
        messages = result.all_messages()
    except AttributeError:
        return

    cumulative_input = 0
    cumulative_output = 0
    pending_tool_results: list[str] = []
    request_number = 0

    for message in messages:
        if isinstance(message, ModelRequest):
            pending_tool_results.extend(
                part.tool_name for part in message.parts if isinstance(part, ToolReturnPart)
            )
        elif isinstance(message, ModelResponse):
            request_number += 1
            usage = message.usage
            input_tokens = usage.input_tokens
            output_tokens = usage.output_tokens
            cumulative_input += input_tokens
            cumulative_output += output_tokens
            trace.model_request_usage.append(
                ModelRequestUsage(
                    request_number=request_number,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cumulative_input_tokens=cumulative_input,
                    cumulative_output_tokens=cumulative_output,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_write_tokens=usage.cache_write_tokens,
                    preceding_tool_results=pending_tool_results,
                )
            )
            pending_tool_results = []


def emit_trace(trace: InvestigationTrace):
    if trace._emitted:
        return
    trace._emitted = True

    # If stream ended without assigning a termination reason, it implies generator exit/disconnect
    if not trace.termination_reason and not trace.failure_stage:
        trace.termination_reason = TerminationReason.CLIENT_DISCONNECTED

    trace.total_duration_sec = time.perf_counter() - trace._start_time
    logger.info(json.dumps(trace_to_dict(trace)))
