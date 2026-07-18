import asyncio
import json
from evals.schema import EvaluationCase
from evals.cases import httpx_transport_case, pydantic_types_case
from app.services.github import GitHubRepository
from app.services.repository_snapshot import RepositorySnapshot
from app.services.investigation_service import run_investigation
from app.investigation_trace import InvestigationTrace
from app.investigation_events import InvestigationAnswerChunk, InvestigationCompleted
from app.services.investigation_workspace import InvestigationWorkspace

async def run_case_with_limit(case: EvaluationCase, limit: int):
    print(f"\n==============================================")
    print(f"Running {case.id} with MAX_ITERATIONS={limit}")
    print(f"==============================================\n")
    
    InvestigationWorkspace.MAX_ITERATIONS = limit
    
    gh_repo = GitHubRepository.from_url(case.repository_url)
    snapshot = RepositorySnapshot(gh_repo=gh_repo)
    snapshot.materialize()
    
    trace = InvestigationTrace(started_at="now", question_chars=len(case.question))
    
    try:
        async for event in run_investigation(snapshot, case.question, trace):
            pass
    except Exception as e:
        print(f"Exception during run: {e}")
    finally:
        snapshot.cleanup()
        
    print(f"Termination Reason: {trace.termination_reason}")
    files_read = [s.action_arguments.get("file_path") for s in trace.steps if s.action_chosen == "read_file"]
    print(f"Final Evidence Files: {files_read}")
    
    for i, step in enumerate(trace.steps):
        print(f"\n--- Request {i+1} ---")
        print(f"Tool Selected: {step.action_chosen}")
        # sanitize args (limit length of strings)
        sanitized_args = {}
        for k, v in step.action_arguments.items():
            if isinstance(v, str) and len(v) > 100:
                sanitized_args[k] = v[:100] + "..."
            else:
                sanitized_args[k] = v
        print(f"Arguments: {sanitized_args}")
        # Not all fields might exist on step
        print(f"Added Evidence: {getattr(step, 'evidence_added', 'Unknown')}")
        
    print("\n")
    
    # Let's restore limit
    InvestigationWorkspace.MAX_ITERATIONS = 8

async def main():
    for limit in [8, 12]:
        await run_case_with_limit(httpx_transport_case, limit)
        await asyncio.sleep(60)
        await run_case_with_limit(pydantic_types_case, limit)
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
