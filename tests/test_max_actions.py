import pytest
from unittest.mock import MagicMock
from app.services.investigation_workspace import InvestigationWorkspace, AgentObservation
from app.services.repository_snapshot import RepositorySnapshot
from app.investigation_trace import TerminationReason

@pytest.fixture
def workspace():
    snapshot = MagicMock(spec=RepositorySnapshot)
    snapshot.root_path = None
    ws = InvestigationWorkspace(snapshot, allowed_paths=["dir/file.py"], max_actions_override=8)
    return ws


def simulate_action(ws: InvestigationWorkspace, action: str):
    if not ws.can_continue():
        return
    ws.actions += 1
    if action == "read_file":
        ws.total_evidence_chars += 100
        ws.gathered_evidence[f"file_{ws.actions}.py"] = "content"
    elif action == "search_code":
        ws.total_searches += 1


def test_scenario_a_perfect_path(workspace):
    actions = ["list_directory", "search_code", "read_file"]
    for action in actions:
        simulate_action(workspace, action)
    assert workspace.actions == 3
    assert workspace.can_continue()

def test_scenario_b_redundant_searches(workspace):
    actions = ["list_directory", "search_code", "search_code", "search_code", "read_file"]
    for action in actions:
        simulate_action(workspace, action)
    assert workspace.actions == 5
    assert workspace.can_continue()

def test_scenario_c_abstraction_traversal(workspace):
    actions = ["list_directory", "search_code", "read_file", "search_code", "read_file"]
    for action in actions:
        simulate_action(workspace, action)
    assert workspace.actions == 5
    assert workspace.can_continue()

def test_scenario_d_boundary_overhead(workspace):
    actions = ["search_code", "read_file", "read_file", "read_file"]
    for action in actions:
        simulate_action(workspace, action)
    assert workspace.actions == 4
    assert workspace.can_continue()

def test_scenario_e_validator_retry(workspace):
    actions = ["list_directory", "search_code", "search_code", "read_file", "search_code", "read_file"]
    for action in actions:
        simulate_action(workspace, action)
    assert workspace.actions == 6
    assert workspace.can_continue()

    simulate_action(workspace, "search_code")
    simulate_action(workspace, "read_file")

    assert workspace.actions == 8
    assert not workspace.can_continue()
    assert workspace.get_termination_reason() == TerminationReason.MAX_ACTIONS
