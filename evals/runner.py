import asyncio
import argparse
import json
import time
from datetime import datetime, timezone
from app.services.github import GitHubRepository
from app.services.repository_snapshot import RepositorySnapshot
from app.services.investigation_service import run_investigation
from app.investigation_trace import InvestigationTrace
from app.investigation_events import InvestigationAnswerChunk, InvestigationCompleted
from evals.schema import RunMetadata, EvaluationResult, EvaluationCase, SuiteSummary, SuiteEvaluationResult
from evals.cases import ALL_CASES
from evals.scorer import score_evidence_completeness, score_expected_terms

async def evaluate_case(case: EvaluationCase) -> EvaluationResult:
    print(f"Starting evaluation case: {case.id}")
    t_start = time.perf_counter()
    
    gh_repo = GitHubRepository.from_url(case.repository_url)
    t_resolved = time.perf_counter()
    repository_resolution_latency = t_resolved - t_start
    
    snapshot = RepositorySnapshot(gh_repo=gh_repo)
    try:
        snapshot.materialize()
    except Exception as e:
        print(f"Failed to materialize {case.repository_url}: {e}")
        return _build_failure_result(case, str(e), gh_repo.revision, repository_resolution_latency, time.perf_counter() - t_resolved)
    t_materialized = time.perf_counter()
    materialization_latency = t_materialized - t_resolved
    
    trace = InvestigationTrace(
        started_at=datetime.now(timezone.utc).isoformat(),
        question_chars=len(case.question),
        _start_time=t_start
    )
    
    answer_chunks = []
    t_inv_start = time.perf_counter()
    t_inv_end = None
    
    try:
        async for event in run_investigation(snapshot, case.question, trace):
            if isinstance(event, InvestigationAnswerChunk):
                if t_inv_end is None:
                    t_inv_end = time.perf_counter()
                answer_chunks.append(event.chunk)
            elif isinstance(event, InvestigationCompleted):
                pass
    except Exception as e:
        print(f"Failed during run_investigation: {e}")
        return _build_failure_result(case, str(e), gh_repo.revision, repository_resolution_latency, materialization_latency)
    finally:
        try:
            snapshot.cleanup()
        except:
            pass
            
    t_end = time.perf_counter()
    
    if t_inv_end is None:
        # If there were no answer chunks (e.g., immediate failure before answering)
        t_inv_end = t_end
        
    investigation_latency = t_inv_end - t_inv_start
    answer_generation_latency = t_end - t_inv_end
    total_latency = t_end - t_start
    
    final_answer = "".join(answer_chunks)
    
    files_read = list(trace.evidence_file_paths)
    tool_sequence = []
    for step in trace.steps:
        tool_sequence.append(step.action_chosen)
        file_path = step.action_arguments.get("file_path")
        if step.action_chosen == "read_file" and file_path and file_path not in files_read:
            files_read.append(file_path)
            
    evidence_score = score_evidence_completeness(files_read, case.expected_evidence_groups)
    terms_score = score_expected_terms(final_answer, case.expected_answer_terms)
    
    success = (evidence_score == 1.0) and (trace.termination_reason == "model_finished")
    
    return EvaluationResult(
        metadata=RunMetadata(
            timestamp=datetime.now(timezone.utc).isoformat(),
            model="gemini-3.1-flash-lite",
            repository_revision=gh_repo.revision,
            repository_branch=gh_repo.metadata.get("default_branch") if gh_repo.metadata else None,
            case_id=case.id
        ),
        success=success,
        evidence_completeness_score=evidence_score,
        answer_expected_terms_score=terms_score,
        files_read=files_read,
        tool_sequence=tool_sequence,
        searches_used=sum(1 for s in tool_sequence if s == "search_code"),
        directory_listings_used=sum(1 for s in tool_sequence if s == "list_directory"),
        model_requests=trace.model_requests,
        input_tokens=trace.input_tokens,
        output_tokens=trace.output_tokens,
        repository_resolution_latency=repository_resolution_latency,
        materialization_latency=materialization_latency,
        investigation_latency=investigation_latency,
        answer_generation_latency=answer_generation_latency,
        total_latency=total_latency,
        termination_reason=trace.termination_reason,
        evidence_char_count=trace.final_evidence_chars
    )

def _build_failure_result(case: EvaluationCase, error_msg: str, revision: str | None, res_lat: float, mat_lat: float) -> EvaluationResult:
    return EvaluationResult(
        metadata=RunMetadata(
            timestamp=datetime.now(timezone.utc).isoformat(),
            model="gemini-3.1-flash-lite",
            repository_revision=revision,
            repository_branch=None,
            case_id=case.id
        ),
        success=False,
        evidence_completeness_score=0.0,
        answer_expected_terms_score=0.0,
        files_read=[],
        tool_sequence=[],
        searches_used=0,
        directory_listings_used=0,
        model_requests=0,
        input_tokens=0,
        output_tokens=0,
        repository_resolution_latency=res_lat,
        materialization_latency=mat_lat,
        investigation_latency=None,
        answer_generation_latency=None,
        total_latency=None,
        termination_reason=f"error: {error_msg}",
        evidence_char_count=0
    )

def aggregate_suite_results(results: list[EvaluationResult]) -> SuiteSummary:
    total = len(results)
    if total == 0:
        return SuiteSummary(
            total_cases=0, successful_cases=0, average_evidence_completeness=0.0,
            average_answer_expected_terms_score=0.0, total_input_tokens=0,
            total_output_tokens=0, all_average_input_tokens=0.0, all_average_output_tokens=0.0,
            all_average_investigation_latency=0.0, all_average_answer_generation_latency=0.0,
            all_average_total_latency=0.0, success_average_input_tokens=0.0,
            success_average_output_tokens=0.0, success_average_investigation_latency=0.0,
            success_average_answer_generation_latency=0.0, success_average_total_latency=0.0,
            total_searches_used=0, total_directory_listings_used=0
        )
        
    successful_results = [r for r in results if r.success]
    successful = len(successful_results)
    
    def avg(lst):
        filtered = [x for x in lst if x is not None]
        return sum(filtered) / len(filtered) if filtered else 0.0

    return SuiteSummary(
        total_cases=total,
        successful_cases=successful,
        average_evidence_completeness=sum(r.evidence_completeness_score for r in results) / total,
        average_answer_expected_terms_score=sum(r.answer_expected_terms_score for r in results) / total,
        total_input_tokens=sum(r.input_tokens for r in results),
        total_output_tokens=sum(r.output_tokens for r in results),
        
        all_average_input_tokens=sum(r.input_tokens for r in results) / total,
        all_average_output_tokens=sum(r.output_tokens for r in results) / total,
        all_average_investigation_latency=avg([r.investigation_latency for r in results]),
        all_average_answer_generation_latency=avg([r.answer_generation_latency for r in results]),
        all_average_total_latency=avg([r.total_latency for r in results]),
        
        success_average_input_tokens=sum(r.input_tokens for r in successful_results) / successful if successful > 0 else 0.0,
        success_average_output_tokens=sum(r.output_tokens for r in successful_results) / successful if successful > 0 else 0.0,
        success_average_investigation_latency=avg([r.investigation_latency for r in successful_results]),
        success_average_answer_generation_latency=avg([r.answer_generation_latency for r in successful_results]),
        success_average_total_latency=avg([r.total_latency for r in successful_results]),
        
        total_searches_used=sum(r.searches_used for r in results),
        total_directory_listings_used=sum(r.directory_listings_used for r in results)
    )

async def run_cases(cases: list[EvaluationCase]) -> None:
    results = []
    for case in cases:
        res = await evaluate_case(case)
        results.append(res)
        if len(cases) > 1:
            await asyncio.sleep(20) # Avoid Gemini API rate limits
        
    summary = aggregate_suite_results(results)
    suite_result = SuiteEvaluationResult(summary=summary, results=results)
    
    with open("eval_results.json", "w") as f:
        json.dump(suite_result.model_dump(), f, indent=2)
        
    print(f"Evaluated {len(results)} cases. Successful: {summary.successful_cases}. Results saved to eval_results.json.")


def _case_by_id(case_id: str) -> EvaluationCase | None:
    return next((case for case in ALL_CASES if case.id == case_id), None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Trace's live Gemini/GitHub evaluation cases explicitly.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--case", metavar="CASE_ID", help="Run exactly one live evaluation case.")
    selection.add_argument("--all", action="store_true", help="Run the complete live evaluation suite.")
    parser.add_argument("--confirm-live", action="store_true", help="Confirm that the selected run makes live API/model calls.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.case and not args.all:
        parser.print_usage()
        print("No live evaluation was run. Use --case CASE_ID, or --all --confirm-live.")
        return 0

    if args.case:
        case = _case_by_id(args.case)
        if case is None:
            print(f"Unknown case ID: {args.case}")
            print("Available case IDs:")
            for available in ALL_CASES:
                print(f"  {available.id}")
            return 2
        asyncio.run(run_cases([case]))
        return 0

    if not args.confirm_live:
        print("Refusing to run the full live suite without --confirm-live.")
        print("The full suite makes multiple GitHub and Gemini API calls.")
        return 2

    print("WARNING: running all evaluation cases will make multiple live GitHub and Gemini API calls.")
    asyncio.run(run_cases(ALL_CASES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
