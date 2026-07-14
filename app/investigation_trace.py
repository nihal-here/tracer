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
    MAX_ITERATIONS = "max_iterations"
    MAX_UNIQUE_FILES = "max_unique_files"
    MAX_EVIDENCE_CHARS = "max_evidence_chars"
    MAX_INVALID_ACTIONS = "max_invalid_actions"
    CONSECUTIVE_NO_PROGRESS = "consecutive_no_progress"
    ERROR = "error"
    CLIENT_DISCONNECTED = "client_disconnected"


@dataclass
class AgentStepTrace:
    iteration: int
    decision_duration_sec: float
    action_chosen: str
    action_arguments: dict[str, str]
    prompt_chars: int
    history_chars: int
    allowed_paths_chars: int
    execution_duration_sec: float
    search_results_count: int | None = None


@dataclass
class InvestigationTrace:
    started_at: str
    question_chars: int

    repository_resolution_duration_sec: float = 0.0
    materialization_duration_sec: float = 0.0
    total_duration_sec: float = 0.0
    answer_generation_duration_sec: float = 0.0

    steps: list[AgentStepTrace] = field(default_factory=list)

    final_evidence_files_count: int = 0
    final_evidence_chars: int = 0
    answer_chunks_emitted: int = 0
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


def emit_trace(trace: InvestigationTrace):
    if trace._emitted:
        return
    trace._emitted = True

    # If stream ended without assigning a termination reason, it implies generator exit/disconnect
    if not trace.termination_reason and not trace.failure_stage:
        trace.termination_reason = TerminationReason.CLIENT_DISCONNECTED

    trace.total_duration_sec = time.perf_counter() - trace._start_time
    logger.info(json.dumps(trace_to_dict(trace)))
