import pytest
import os
from unittest.mock import patch, MagicMock

# Mock the API key so pydantic_ai initialization doesn't fail at import time
os.environ["GOOGLE_API_KEY"] = "mock"

from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from app.services.investigation_agent import investigation_agent, AgentDeps, InvestigationResult, DomainTerminationException
from app.services.investigation_workspace import InvestigationWorkspace
from app.investigation_trace import InvestigationTrace, TerminationReason
from pathlib import Path
import tempfile
import json
from pydantic_ai.usage import UsageLimits

def test_evidence_incomplete_rejection():
    """Agent output validator rejects a finish attempt if evidence is incomplete."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "main.py").write_text("from strategy import jwt\\ndef foo(): pass")
        
        mock_repo = MagicMock()
        mock_repo.get_readme.return_value = None
        mock_repo.list_top_level_files.return_value = ["main.py"]
        
        mock_snapshot = MagicMock()
        mock_snapshot.gh_repo = mock_repo
        mock_snapshot.root_path = root
        mock_snapshot.extracted_files = frozenset(["main.py"])

        workspace = InvestigationWorkspace(mock_snapshot, allowed_paths=frozenset(["main.py"]))
        trace = InvestigationTrace(started_at="now", question_chars=10, _start_time=0.0)
        deps = AgentDeps(workspace=workspace, trace=trace)
        
        # Read a file that delegates to 'jwt'
        workspace.read_file("main.py")
        
        # Force a finish attempt
        result_payload = InvestigationResult(
            summary_of_evidence="- read main.py",
            delegated_interfaces_discovered=["jwt"],
            concrete_implementations_read=[],
            remaining_ambiguities="none",
            final_answer="The token is handled here."
        )
        
        def model_func(messages, info):
            return ModelResponse(parts=[TextPart(content=result_payload.model_dump_json())])
            
        test_model = FunctionModel(model_func)
        
        with pytest.raises(Exception) as excinfo:
            import asyncio
            asyncio.run(investigation_agent.run(
                "Find the token.",
                deps=deps,
                model=test_model,
                usage_limits=UsageLimits(request_limit=3)
            ))
            
        assert "Exceeded maximum output retries" in str(excinfo.value)

def test_fatal_domain_termination_handling():
    """Agent execution terminates immediately on fatal domain errors."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        mock_repo = MagicMock()
        mock_repo.get_readme.return_value = None
        mock_repo.list_top_level_files.return_value = []
        
        mock_snapshot = MagicMock()
        mock_snapshot.gh_repo = mock_repo
        mock_snapshot.root_path = root
        mock_snapshot.extracted_files = frozenset()

        workspace = InvestigationWorkspace(mock_snapshot, allowed_paths=frozenset())
        trace = InvestigationTrace(started_at="now", question_chars=10, _start_time=0.0)
        deps = AgentDeps(workspace=workspace, trace=trace)
        
        # Manually trigger consecutive_no_progress limit
        workspace.consecutive_no_progress = workspace.MAX_CONSECUTIVE_NO_PROGRESS
        
        def model_func(messages, info):
            return ModelResponse(parts=[ToolCallPart(tool_name="read_file", args={"file_path": "foo"})])
            
        test_model = FunctionModel(model_func)
        
        with pytest.raises(DomainTerminationException) as excinfo:
            import asyncio
            asyncio.run(investigation_agent.run(
                "Find something.",
                deps=deps,
                model=test_model,
                usage_limits=UsageLimits(request_limit=5)
            ))
            
        assert excinfo.value.reason == "consecutive_no_progress"
