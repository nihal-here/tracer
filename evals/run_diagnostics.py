import asyncio
import argparse
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
    print(f"Running {case.id} with MAX_ACTIONS={limit}")
    print(f"==============================================\n")
    
    setattr(InvestigationWorkspace, "MAX_ACTIONS", limit)
    
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
    
    # Restore the production action budget after diagnostics.
    setattr(InvestigationWorkspace, "MAX_ACTIONS", 8)


DIAGNOSTIC_CASES = {
    httpx_transport_case.id: httpx_transport_case,
    pydantic_types_case.id: pydantic_types_case,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Trace diagnostics explicitly against live services.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--case", metavar="CASE_ID", help="Run one diagnostic case.")
    selection.add_argument("--all", action="store_true", help="Run both diagnostic cases.")
    parser.add_argument("--confirm-live", action="store_true", help="Confirm live GitHub/Gemini calls.")
    return parser


async def run_selected(cases: list[EvaluationCase]) -> None:
    for limit in [8, 12]:
        for index, case in enumerate(cases):
            await run_case_with_limit(case, limit)
            if index < len(cases) - 1 or limit != 12:
                await asyncio.sleep(60)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.case and not args.all:
        parser.print_usage()
        print("No live diagnostics were run. Use --case CASE_ID, or --all --confirm-live.")
        return 0

    if args.case:
        case = DIAGNOSTIC_CASES.get(args.case)
        if case is None:
            print(f"Unknown diagnostic case ID: {args.case}")
            print("Available case IDs:")
            for case_id in DIAGNOSTIC_CASES:
                print(f"  {case_id}")
            return 2
        asyncio.run(run_selected([case]))
        return 0

    if not args.confirm_live:
        print("Refusing to run all diagnostics without --confirm-live.")
        print("Diagnostics make multiple live GitHub and Gemini API calls.")
        return 2

    print("WARNING: diagnostics make multiple live GitHub and Gemini API calls.")
    asyncio.run(run_selected(list(DIAGNOSTIC_CASES.values())))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
