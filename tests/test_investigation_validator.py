import pytest
from app.services.investigation_workspace import InvestigationWorkspace, EvidenceSpan
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai import RunContext
from app.services.investigation_agent import AgentDeps, InvestigationResult, validate_evidence_completeness, EvidenceExcerpt, DelegatedImplementationEvidence
from dataclasses import dataclass
from typing import cast, Any

@dataclass
class MockDeps:
    workspace: InvestigationWorkspace

@dataclass
class MockContext:
    deps: MockDeps

@pytest.fixture
def workspace_with_evidence():
    from unittest.mock import MagicMock
    ws = InvestigationWorkspace(MagicMock(), ["test_repo"])
    ws.evidence_spans = [
        EvidenceSpan(path="caller.py", start_line=10, end_line=20, content="", source_action_index=1, truncated=False),
        EvidenceSpan(path="impl.py", start_line=100, end_line=140, content="", source_action_index=2, truncated=False),
        EvidenceSpan(path="impl.py", start_line=1, end_line=30, content="", source_action_index=3, truncated=False)
    ]
    return ws

def test_validator_fails_without_concrete_implementation(workspace_with_evidence):
    # Agent observed the call site but no concrete implementation
    result = InvestigationResult(
        summary_of_evidence="",
        delegated_interfaces_discovered=["some.interface"],
        relevant_excerpts=[
            EvidenceExcerpt(path="caller.py", start_line=10, end_line=15, justification="")
        ],
        concrete_implementations_read=[]
    )

    ctx = cast(RunContext[AgentDeps], cast(Any, MockContext(deps=MockDeps(workspace=workspace_with_evidence))))
    with pytest.raises(ModelRetry, match="You discovered delegated interfaces but did not read their concrete implementations"):
        validate_evidence_completeness(ctx, result)  # type: ignore

def test_validator_succeeds_with_concrete_implementation(workspace_with_evidence):
    result = InvestigationResult(
        summary_of_evidence="",
        delegated_interfaces_discovered=["some.interface"],
        relevant_excerpts=[
            EvidenceExcerpt(path="caller.py", start_line=10, end_line=15, justification="")
        ],
        concrete_implementations_read=[
            DelegatedImplementationEvidence(
                delegated_interface="some.interface",
                implementations=[
                    EvidenceExcerpt(path="impl.py", start_line=100, end_line=140, justification="")
                ]
            )
        ]
    )
    ctx = cast(RunContext[AgentDeps], cast(Any, MockContext(deps=MockDeps(workspace=workspace_with_evidence))))
    res = validate_evidence_completeness(ctx, result)  # type: ignore
    assert res == result

def test_validator_fails_if_unobserved_lines_claimed(workspace_with_evidence):
    # Agent read impl.py 1-30, but claims 100-140 without reading it (wait, it did read 100-140 in fixture. Let's claim 150-160)
    result = InvestigationResult(
        summary_of_evidence="",
        delegated_interfaces_discovered=["some.interface"],
        relevant_excerpts=[
            EvidenceExcerpt(path="caller.py", start_line=10, end_line=15, justification="")
        ],
        concrete_implementations_read=[
            DelegatedImplementationEvidence(
                delegated_interface="some.interface",
                implementations=[
                    EvidenceExcerpt(path="impl.py", start_line=150, end_line=160, justification="")
                ]
            )
        ]
    )
    ctx = cast(RunContext[AgentDeps], cast(Any, MockContext(deps=MockDeps(workspace=workspace_with_evidence))))
    with pytest.raises(ModelRetry, match="were never observed"):
        validate_evidence_completeness(ctx, result)  # type: ignore
