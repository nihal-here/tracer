import pytest
from unittest.mock import patch, MagicMock
from app.services.investigation_agent import choose_next_action, InvestigationAction, ActionType
from app.services.investigation_workspace import AgentObservation

@patch("app.services.investigation_agent.genai.Client")
@patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"})
def test_choose_next_action_history_serialization(mock_client_class):
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    mock_response = MagicMock()
    mock_response.parsed = InvestigationAction(action_type=ActionType.FINISH)
    mock_client.models.generate_content.return_value = mock_response

    history = [
        AgentObservation(action_type="read_file", path="valid.py", result_status="success", content="def foo():\n    pass", new_evidence_added=True),
        AgentObservation(action_type="read_file", path="valid.py", result_status="already_read", content=None, new_evidence_added=False),
        AgentObservation(action_type="read_file", path="bad.py", result_status="invalid_path", content=None, new_evidence_added=False)
    ]

    result = choose_next_action("Q?", frozenset(["valid.py"]), history)
    assert result.action.action_type == ActionType.FINISH
    
    # Verify exactly what was sent in the prompt
    prompt_sent = mock_client.models.generate_content.call_args[1]["contents"]
    
    # 1. Success contains the actual source content
    assert "Action: read_file, Path: valid.py, Result: success" in prompt_sent
    assert "--- Content Start ---\ndef foo():\n    pass\n--- Content End ---" in prompt_sent
    
    # 2. Duplicate contains no source content, just the metadata
    assert "Action: read_file, Path: valid.py, Result: already_read" in prompt_sent
    # Make sure duplicate content block is NOT there (already proven because only the success block has it, but lets make sure it didn't do it)
    assert prompt_sent.count("--- Content Start ---") == 1
    
    # 3. Invalid contains no source content
    assert "Action: read_file, Path: bad.py, Result: invalid_path" in prompt_sent
