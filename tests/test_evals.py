from datetime import datetime, timezone
from evals.schema import EvidenceGroupRequirement, RunMetadata, EvaluationResult, SuiteEvaluationResult, EvaluationCase
from evals.scorer import score_evidence_completeness, score_expected_terms
from evals.runner import aggregate_suite_results, _build_failure_result

def test_evidence_completeness_scorer():
    groups = [
        EvidenceGroupRequirement(name="A", alternatives={"fileA.py"}),
        EvidenceGroupRequirement(name="B", alternatives={"fileB1.py", "fileB2.py"})
    ]
    
    assert score_evidence_completeness([], groups) == 0.0
    assert score_evidence_completeness(["fileA.py"], groups) == 0.5
    assert score_evidence_completeness(["fileA.py", "fileB2.py"], groups) == 1.0
    assert score_evidence_completeness(["fileC.py"], groups) == 0.0
    assert score_evidence_completeness(["fileA.py"], []) == 1.0

def test_expected_terms_scorer():
    terms = {"Token", "Authenticator"}
    
    assert score_expected_terms("I don't know.", terms) == 0.0
    assert score_expected_terms("Here is the token extraction logic.", terms) == 0.5
    assert score_expected_terms("The Authenticator processes the token.", terms) == 1.0
    assert score_expected_terms("Anything", set()) == 1.0

def test_metadata_and_latency_fields():
    # Test schema instantiation
    res = EvaluationResult(
        metadata=RunMetadata(
            timestamp=datetime.now(timezone.utc).isoformat(),
            model="gemini-3.1-flash-lite",
            repository_revision="deadbeef123",
            repository_branch="main",
            case_id="test-1"
        ),
        success=True,
        evidence_completeness_score=1.0,
        answer_expected_terms_score=1.0,
        files_read=[],
        tool_sequence=[],
        searches_used=1,
        directory_listings_used=0,
        model_requests=5,
        input_tokens=100,
        output_tokens=50,
        repository_resolution_latency=0.5,
        materialization_latency=1.5,
        investigation_latency=5.0,
        answer_generation_latency=2.0,
        total_latency=9.0,
        termination_reason="model_finished",
        evidence_char_count=100
    )
    
    assert res.metadata.repository_revision == "deadbeef123"
    assert res.repository_resolution_latency == 0.5

def test_suite_aggregation():
    res1 = EvaluationResult(
        metadata=RunMetadata(timestamp="", model="", case_id="1"),
        success=True, evidence_completeness_score=1.0, answer_expected_terms_score=1.0,
        files_read=[], tool_sequence=[], searches_used=1, directory_listings_used=0,
        model_requests=2, input_tokens=100, output_tokens=50,
        repository_resolution_latency=1.0, materialization_latency=1.0, investigation_latency=5.0,
        answer_generation_latency=2.0, total_latency=9.0, termination_reason="", evidence_char_count=0
    )
    res2 = EvaluationResult(
        metadata=RunMetadata(timestamp="", model="", case_id="2"),
        success=False, evidence_completeness_score=0.0, answer_expected_terms_score=0.0,
        files_read=[], tool_sequence=[], searches_used=0, directory_listings_used=0,
        model_requests=1, input_tokens=50, output_tokens=10,
        repository_resolution_latency=0.5, materialization_latency=0.5, investigation_latency=2.0,
        answer_generation_latency=1.0, total_latency=4.0, termination_reason="", evidence_char_count=0
    )
    
    summary = aggregate_suite_results([res1, res2])
    assert summary.total_cases == 2
    assert summary.successful_cases == 1
    assert summary.average_evidence_completeness == 0.5
    assert summary.total_input_tokens == 150
    assert summary.all_average_total_latency == 6.5
    assert summary.success_average_total_latency == 9.0
    assert summary.total_searches_used == 1

def test_failure_handling_preserves_suite():
    case = EvaluationCase(
        id="test-fail", repository_url="https://github.com/a/b",
        question="", expected_evidence_groups=[], expected_answer_terms=set()
    )
    fail_res = _build_failure_result(case, "simulated error", "rev1", 0.1, 0.2)
    assert not fail_res.success
    assert fail_res.termination_reason == "error: simulated error"
    assert fail_res.metadata.repository_revision == "rev1"
    assert fail_res.total_latency is None
    
    # Simulating suite containing one failed and one successful
    summary = aggregate_suite_results([fail_res])
    assert summary.total_cases == 1
    assert summary.successful_cases == 0
