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
        execution_success=True,
        evaluation_pass=True,
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
        execution_success=True, evaluation_pass=True, evidence_completeness_score=1.0, answer_expected_terms_score=1.0,
        files_read=[], tool_sequence=[], searches_used=1, directory_listings_used=0,
        model_requests=2, input_tokens=100, output_tokens=50,
        repository_resolution_latency=1.0, materialization_latency=1.0, investigation_latency=5.0,
        answer_generation_latency=2.0, total_latency=9.0, termination_reason="", evidence_char_count=0
    )
    res2 = EvaluationResult(
        metadata=RunMetadata(timestamp="", model="", case_id="2"),
        execution_success=False, evaluation_pass=False, evidence_completeness_score=0.0, answer_expected_terms_score=0.0,
        files_read=[], tool_sequence=[], searches_used=0, directory_listings_used=0,
        model_requests=1, input_tokens=50, output_tokens=10,
        repository_resolution_latency=0.5, materialization_latency=0.5, investigation_latency=2.0,
        answer_generation_latency=1.0, total_latency=4.0, termination_reason="", evidence_char_count=0
    )

    summary = aggregate_suite_results([res1, res2])
    assert summary.total_cases == 2
    assert summary.successful_executions == 1
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
    assert not fail_res.execution_success
    assert fail_res.termination_reason == "error: simulated error"
    assert fail_res.metadata.repository_revision == "rev1"
    assert fail_res.total_latency is None

    # Simulating suite containing one failed and one successful
    summary = aggregate_suite_results([fail_res])
    assert summary.total_cases == 1
    assert summary.successful_executions == 0


from unittest.mock import patch, MagicMock
import pytest
from app.investigation_trace import AgentStepTrace, SearchCodeTraceMetadata, ReadFileTraceMetadata
from app.investigation_events import InvestigationAnswerChunk, InvestigationCompleted, CitationMetadata

@pytest.mark.anyio
async def test_evaluate_case_absence_verification():
    case = EvaluationCase(
        id="test-absence-001",
        repository_url="https://github.com/a/b",
        question="Is OAuth1 supported?",
        expected_evidence_groups=[],
        expected_concrete_implementations=set(),
        expected_answer_terms={"not supported"},
        require_absence_searches=["oauth"],
        require_absence_files=["auth.py"],
        forbid_citations=True
    )

    # 1. Search-only fails when BOTH are required
    async def mock_run_investigation_search_only(snapshot, question, trace, max_actions):
        trace.termination_reason = "model_finished"
        trace.model_requests = 1
        trace.evidence_file_paths = []
        step = AgentStepTrace(
            action_number=1,
            action_chosen="search_code",
            action_arguments={},
            prompt_chars=0, history_chars=0, repo_map_chars=0,
            search_code_metadata=SearchCodeTraceMetadata(
                query="oauth1",
                scope=None,
                case_sensitive=False,
                matches_returned=0,
                returned_chars=0
            )
        )
        trace.steps = [step]
        yield InvestigationAnswerChunk(chunk="OAuth1 is not supported.")
        yield InvestigationCompleted()

    with patch("evals.runner.GitHubRepository") as mock_gh, \
         patch("evals.runner.RepositorySnapshot") as mock_snap, \
         patch("evals.runner.run_investigation", side_effect=mock_run_investigation_search_only):
        
        mock_gh.from_url.return_value = MagicMock(
            revision="rev1",
            metadata={"default_branch": "main"}
        )
        from evals.runner import evaluate_case
        res = await evaluate_case(case)
        assert res.execution_success
        assert not res.evaluation_pass

    # 2. File-read-only fails when BOTH are required
    async def mock_run_investigation_read_only(snapshot, question, trace, max_actions):
        trace.termination_reason = "model_finished"
        trace.model_requests = 1
        trace.evidence_file_paths = ["auth.py"]
        step = AgentStepTrace(
            action_number=1,
            action_chosen="read_file",
            action_arguments={"file_path": "src/auth.py"},
            prompt_chars=0, history_chars=0, repo_map_chars=0,
            read_file_metadata=ReadFileTraceMetadata(
                requested_path="src/auth.py",
                requested_start_line=1,
                requested_end_line=10,
                actual_start_line=1,
                actual_end_line=10,
                total_file_lines=10,
                truncated=False,
                returned_chars=100
            )
        )
        trace.steps = [step]
        yield InvestigationAnswerChunk(chunk="OAuth1 is not supported.")
        yield InvestigationCompleted()

    with patch("evals.runner.GitHubRepository") as mock_gh, \
         patch("evals.runner.RepositorySnapshot") as mock_snap, \
         patch("evals.runner.run_investigation", side_effect=mock_run_investigation_read_only):
        
        mock_gh.from_url.return_value = MagicMock(
            revision="rev1",
            metadata={"default_branch": "main"}
        )
        res = await evaluate_case(case)
        assert res.execution_success
        assert not res.evaluation_pass

    # 3. Both together pass
    async def mock_run_investigation_both(snapshot, question, trace, max_actions):
        trace.termination_reason = "model_finished"
        trace.model_requests = 1
        trace.evidence_file_paths = ["auth.py"]
        step1 = AgentStepTrace(
            action_number=1,
            action_chosen="search_code",
            action_arguments={},
            prompt_chars=0, history_chars=0, repo_map_chars=0,
            search_code_metadata=SearchCodeTraceMetadata(
                query="oauth1",
                scope=None,
                case_sensitive=False,
                matches_returned=0,
                returned_chars=0
            )
        )
        step2 = AgentStepTrace(
            action_number=2,
            action_chosen="read_file",
            action_arguments={"file_path": "src/auth.py"},
            prompt_chars=0, history_chars=0, repo_map_chars=0,
            read_file_metadata=ReadFileTraceMetadata(
                requested_path="src/auth.py",
                requested_start_line=1,
                requested_end_line=10,
                actual_start_line=1,
                actual_end_line=10,
                total_file_lines=10,
                truncated=False,
                returned_chars=100
            )
        )
        trace.steps = [step1, step2]
        yield InvestigationAnswerChunk(chunk="OAuth1 is not supported.")
        yield InvestigationCompleted()

    with patch("evals.runner.GitHubRepository") as mock_gh, \
         patch("evals.runner.RepositorySnapshot") as mock_snap, \
         patch("evals.runner.run_investigation", side_effect=mock_run_investigation_both):
        
        mock_gh.from_url.return_value = MagicMock(
            revision="rev1",
            metadata={"default_branch": "main"}
        )
        res = await evaluate_case(case)
        assert res.execution_success
        assert res.evaluation_pass


@pytest.mark.anyio
async def test_evaluate_case_forbidden_citations():
    # Case where forbid_citations=True, but some citations are validly supplied.
    case = EvaluationCase(
        id="test-forbid-citations-002",
        repository_url="https://github.com/a/b",
        question="Question?",
        expected_evidence_groups=[],
        expected_concrete_implementations=set(),
        expected_answer_terms={"answer"},
        forbid_citations=True
    )

    # If the model uses citation [1], and citation [1] is indeed supplied (so it is valid),
    # then citations_valid should remain True, but evaluation_pass must be False.
    async def mock_run_investigation_with_valid_citations(snapshot, question, trace, max_actions):
        trace.termination_reason = "model_finished"
        trace.model_requests = 1
        trace.evidence_file_paths = []
        trace.steps = []
        # We simulate the service supplying citation_id "1"
        yield CitationMetadata(citations=[{"citation_id": "1", "path": "file.py"}])
        yield InvestigationAnswerChunk(chunk="The answer is yes [1].")
        yield InvestigationCompleted()

    with patch("evals.runner.GitHubRepository") as mock_gh, \
         patch("evals.runner.RepositorySnapshot") as mock_snap, \
         patch("evals.runner.run_investigation", side_effect=mock_run_investigation_with_valid_citations):
        
        mock_gh.from_url.return_value = MagicMock(
            revision="rev1",
            metadata={"default_branch": "main"}
        )
        from evals.runner import evaluate_case
        res = await evaluate_case(case)
        assert res.execution_success
        # Citations are semantically valid because "1" was supplied.
        assert res.citations_valid
        # But since citations are forbidden, the pass check fails.
        assert not res.evaluation_pass


@pytest.mark.anyio
async def test_evaluate_case_cache_hit_properties():
    case = EvaluationCase(
        id="test-cache-hit-003",
        repository_url="https://github.com/a/b",
        question="Question?",
        expected_evidence_groups=[],
        expected_concrete_implementations=set(),
        expected_answer_terms={"answer"},
        require_cache_hit=True
    )

    # 1. Cache hit fails if trace.investigation_cache_hit is False
    async def mock_run_investigation_no_cache_flag(snapshot, question, trace, max_actions):
        trace.termination_reason = "model_finished"
        trace.investigation_cache_hit = False
        trace.model_requests = 0
        trace.steps = []
        trace.cached_investigation_tool_sequence = ["read_file"]
        yield InvestigationAnswerChunk(chunk="The answer.")
        yield InvestigationCompleted()

    with patch("evals.runner.GitHubRepository") as mock_gh, \
         patch("evals.runner.RepositorySnapshot") as mock_snap, \
         patch("evals.runner.run_investigation", side_effect=mock_run_investigation_no_cache_flag):
        
        mock_gh.from_url.return_value = MagicMock(revision="rev1", metadata={"default_branch": "main"})
        from evals.runner import evaluate_case
        res = await evaluate_case(case)
        assert not res.evaluation_pass

    # 2. Cache hit fails if trace.model_requests > 0
    async def mock_run_investigation_requests(snapshot, question, trace, max_actions):
        trace.termination_reason = "model_finished"
        trace.investigation_cache_hit = True
        trace.model_requests = 1
        trace.steps = []
        trace.cached_investigation_tool_sequence = ["read_file"]
        yield InvestigationAnswerChunk(chunk="The answer.")
        yield InvestigationCompleted()

    with patch("evals.runner.GitHubRepository") as mock_gh, \
         patch("evals.runner.RepositorySnapshot") as mock_snap, \
         patch("evals.runner.run_investigation", side_effect=mock_run_investigation_requests):
        
        mock_gh.from_url.return_value = MagicMock(revision="rev1", metadata={"default_branch": "main"})
        res = await evaluate_case(case)
        assert not res.evaluation_pass

    # 3. Cache hit fails if trace.steps is non-empty (new tools executed)
    async def mock_run_investigation_fresh_steps(snapshot, question, trace, max_actions):
        trace.termination_reason = "model_finished"
        trace.investigation_cache_hit = True
        trace.model_requests = 0
        step = AgentStepTrace(action_number=1, action_chosen="read_file", action_arguments={}, prompt_chars=0, history_chars=0, repo_map_chars=0)
        trace.steps = [step]
        trace.cached_investigation_tool_sequence = ["read_file"]
        yield InvestigationAnswerChunk(chunk="The answer.")
        yield InvestigationCompleted()

    with patch("evals.runner.GitHubRepository") as mock_gh, \
         patch("evals.runner.RepositorySnapshot") as mock_snap, \
         patch("evals.runner.run_investigation", side_effect=mock_run_investigation_fresh_steps):
        
        mock_gh.from_url.return_value = MagicMock(revision="rev1", metadata={"default_branch": "main"})
        res = await evaluate_case(case)
        assert not res.evaluation_pass

    # 4. Cache hit fails if cached_investigation_tool_sequence is empty
    async def mock_run_investigation_empty_sequence(snapshot, question, trace, max_actions):
        trace.termination_reason = "model_finished"
        trace.investigation_cache_hit = True
        trace.model_requests = 0
        trace.steps = []
        trace.cached_investigation_tool_sequence = []
        yield InvestigationAnswerChunk(chunk="The answer.")
        yield InvestigationCompleted()

    with patch("evals.runner.GitHubRepository") as mock_gh, \
         patch("evals.runner.RepositorySnapshot") as mock_snap, \
         patch("evals.runner.run_investigation", side_effect=mock_run_investigation_empty_sequence):
        
        mock_gh.from_url.return_value = MagicMock(revision="rev1", metadata={"default_branch": "main"})
        res = await evaluate_case(case)
        assert not res.evaluation_pass


