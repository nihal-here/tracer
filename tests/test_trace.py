import json
import pytest
from app.investigation_trace import (
    InvestigationTrace,
    AgentStepTrace,
    FailureStage,
    TerminationReason,
    bound_trace_string,
    trace_to_dict,
    emit_trace
)
from app.services.investigation_workspace import InvestigationWorkspace
from app.services.investigation_agent import ActionType, InvestigationAction


def test_bound_trace_string():
    long_string = "a" * 150
    bounded = bound_trace_string(long_string, max_len=100)
    assert len(bounded) == 100 + len("...[Truncated]")
    assert bounded.endswith("...[Truncated]")
    
    assert bound_trace_string(None) == "None"
    assert bound_trace_string("short") == "short"


def test_no_raw_question_in_trace():
    trace = InvestigationTrace(started_at="mock", question_chars=15, _start_time=0.0)
    
    step = AgentStepTrace(
        iteration=1,
        decision_duration_sec=0.1,
        action_chosen="search_code",
        action_arguments={"search_query_chars": "4", "case_sensitive": "False"},
        prompt_chars=1000,
        history_chars=500,
        allowed_paths_chars=200,
        execution_duration_sec=0.2
    )
    trace.steps.append(step)
    
    trace_dict = trace_to_dict(trace)
    
    assert "question" not in trace_dict
    assert "question_chars" in trace_dict
    assert trace_dict["question_chars"] == 15
    assert "_emitted" not in trace_dict
    
    trace.failure_stage = FailureStage.MATERIALIZATION
    trace_dict2 = trace_to_dict(trace)
    assert trace_dict2["failure_stage"] == "materialization"


def test_deterministic_termination_precedence():
    class MockSnapshot:
        root_path = None
    
    workspace = InvestigationWorkspace(snapshot=MockSnapshot(), allowed_paths=["main.py"]) # pyright: ignore
    
    workspace.iterations = InvestigationWorkspace.MAX_ITERATIONS
    workspace.invalid_actions = InvestigationWorkspace.MAX_INVALID_ACTIONS
    workspace.consecutive_no_progress = InvestigationWorkspace.MAX_CONSECUTIVE_NO_PROGRESS
    
    assert workspace.get_termination_reason() == TerminationReason.MAX_ITERATIONS
    
    workspace.iterations = 0
    assert workspace.get_termination_reason() == TerminationReason.MAX_INVALID_ACTIONS
    
    workspace.invalid_actions = 0
    assert workspace.get_termination_reason() == TerminationReason.CONSECUTIVE_NO_PROGRESS


def test_client_disconnect_termination(caplog):
    import logging
    caplog.set_level(logging.INFO)
    trace = InvestigationTrace(started_at="mock", question_chars=5, _start_time=0.0)
    
    # We call emit_trace without assigning a termination reason or failure stage
    emit_trace(trace)
    
    # emit_trace should auto-assign CLIENT_DISCONNECTED
    assert trace.termination_reason == TerminationReason.CLIENT_DISCONNECTED
    
    # Calling it twice should do nothing because of _emitted
    trace.termination_reason = TerminationReason.MAX_ITERATIONS
    emit_trace(trace)
    
    records = [r for r in caplog.records if r.name == "investigation.trace"]
    assert len(records) == 1
    
    emitted_json = json.loads(records[0].message)
    assert emitted_json["termination_reason"] == "client_disconnected"


def test_answer_generation_mid_failure():
    # If a generator raises mid-stream, the elapsed duration is preserved and the error propagates
    from app.main import _sse_adapter
    from app.investigation_events import InvestigationAnswerChunk
    
    trace = InvestigationTrace(started_at="mock", question_chars=5, _start_time=0.0)
    
    def mock_generator():
        yield "chunk1"
        yield "chunk2"
        trace.answer_chunks_emitted += 2
        raise ValueError("Network issue")
        
    def stream_runner():
        try:
            for chunk in mock_generator():
                pass
        except Exception as e:
            trace.failure_stage = FailureStage.ANSWER_GENERATION
            trace.error_type = type(e).__name__
            raise
    
    with pytest.raises(ValueError):
        stream_runner()
        
    assert trace.failure_stage == FailureStage.ANSWER_GENERATION
    assert trace.error_type == "ValueError"
    assert trace.answer_chunks_emitted == 2
