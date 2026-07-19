import asyncio
import json
import os
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

os.environ.setdefault("GOOGLE_API_KEY", "offline-test")

from app.investigation_events import (
    CitationMetadata,
    InvestigationAnswerChunk,
    InvestigationCompleted,
    InvestigationTraceMetadata,
)
from app.investigation_trace import (
    AgentStepTrace,
    InvestigationTrace,
    ReadFileTraceMetadata,
)
from app.services.answer_service import build_prompt
from app.services.citations import (
    CitationValidationError,
    SourceCitation,
    build_citation_evidence,
    immutable_github_url,
    validate_answer_citations,
)
from app.services.investigation_agent import EvidenceExcerpt
from app.services.investigation_workspace import EvidenceSpan
from app.services.public_trace import build_public_investigation_steps


def make_spans() -> list[EvidenceSpan]:
    return [
        EvidenceSpan(
            path="src/auth.py",
            start_line=10,
            end_line=18,
            content="line 10\nline 11\nline 12\nline 13\nline 14\nline 15\nline 16\nline 17\nline 18",
            source_action_index=1,
        )
    ]


def test_citations_are_stable_merge_overlaps_and_include_immutable_url():
    excerpts = [
        EvidenceExcerpt(path="src/auth.py", start_line=12, end_line=14, justification="a"),
        EvidenceExcerpt(path="src/auth.py", start_line=14, end_line=16, justification="b"),
    ]
    first = build_citation_evidence(excerpts, make_spans(), "owner", "repo", "a" * 40)
    second = build_citation_evidence(list(reversed(excerpts)), make_spans(), "owner", "repo", "a" * 40)

    assert [item.citation.model_dump() for item in first] == [item.citation.model_dump() for item in second]
    assert len(first) == 1
    assert first[0].citation.citation_id == "1"
    assert first[0].citation.start_line == 12
    assert first[0].citation.end_line == 16
    assert first[0].citation.url == "https://github.com/owner/repo/blob/" + "a" * 40 + "/src/auth.py#L12-L16"


def test_unobserved_citation_range_is_rejected():
    excerpt = EvidenceExcerpt(path="src/auth.py", start_line=17, end_line=20, justification="missing")
    try:
        build_citation_evidence([excerpt], make_spans(), "owner", "repo", "a" * 40)
    except CitationValidationError as exc:
        assert "Coverage gap" in str(exc)
    else:
        raise AssertionError("unobserved citation was accepted")


def test_invalid_repository_path_never_gets_a_github_url():
    assert immutable_github_url("owner", "repo", "a" * 40, "/Users/nihal/secret.py", 1, 2) is None
    assert immutable_github_url("owner", "repo", "not-a-sha", "src/auth.py", 1, 2) is None


def test_answer_citation_validation_handles_valid_unknown_and_malformed_tokens():
    citations = [SourceCitation(citation_id="1", path="a.py", start_line=1, end_line=2, commit_sha="sha")]
    valid = validate_answer_citations("The behavior is here [1].", citations)
    invalid = validate_answer_citations("Unknown [99] and malformed [abc].", citations)

    assert valid.valid is True
    assert valid.referenced_ids == ("1",)
    assert invalid.valid is False
    assert invalid.unknown_ids == ("99",)
    assert invalid.malformed_tokens == ("abc",)


def test_answer_prompt_contains_only_selected_citation_evidence():
    prompt = build_prompt(
        "How does auth work?",
        {
            "owner": "owner",
            "name": "repo",
            "language": "Python",
            "description": "",
            "stars": 0,
            "readme_available": False,
            "readme_preview": "",
            "top_level_files": "src",
            "detected_stack": "Python",
            "default_branch": "main",
            "file_contents": {"unselected.py": "must not be sent"},
            "citation_blocks": [
                {
                    "citation": {"citation_id": "1", "path": "src/auth.py", "start_line": 10, "end_line": 12},
                    "evidence": "selected evidence",
                }
            ],
        },
    )

    assert "[1] src/auth.py:L10-L12" in prompt
    assert "selected evidence" in prompt
    assert "unselected.py" not in prompt
    assert "Never invent citation IDs" in prompt


def test_public_trace_is_sanitized_and_contains_observable_metadata_only():
    steps = [
        AgentStepTrace(
            action_number=1,
            action_chosen="read_file",
            action_arguments={"file_path": "masked"},
            prompt_chars=0,
            history_chars=0,
            repo_map_chars=0,
            read_file_metadata=ReadFileTraceMetadata(
                requested_path="/Users/nihal/private.txt",
                requested_start_line=None,
                requested_end_line=None,
                actual_start_line=1,
                actual_end_line=4,
                total_file_lines=4,
                truncated=False,
                returned_chars=20,
            ),
        )
    ]

    public = build_public_investigation_steps(steps)

    assert public[0]["path"] is None
    assert "/Users/nihal" not in json.dumps(public)
    assert "prompt" not in json.dumps(public).lower()
    assert "chain" not in json.dumps(public).lower()


def test_sse_serializes_citations_trace_and_completion():
    from app.main import _sse_adapter

    events = [
        InvestigationTraceMetadata(steps=[{"action_number": 1, "tool": "read_file"}]),
        CitationMetadata(citations=[{"citation_id": "1", "path": "a.py", "start_line": 1, "end_line": 2}]),
        InvestigationAnswerChunk(chunk="answer [1]"),
        InvestigationCompleted(),
    ]

    async def generate_events():
        for event in events:
            yield event

    serialized = asyncio.run(_collect(_sse_adapter(generate_events())))
    payloads = [json.loads(item.removeprefix("data: ").strip()) for item in serialized]

    assert payloads[0]["investigation_trace"][0]["tool"] == "read_file"
    assert payloads[1]["citations"][0]["citation_id"] == "1"
    assert payloads[2]["chunk"] == "answer [1]"
    assert payloads[3] == {"completed": True}


async def _collect(iterator):
    return [item async for item in iterator]


def test_failed_investigation_does_not_emit_citation_metadata(tmp_path, monkeypatch):
    from app.services.github import GitHubRepository
    from app.services.investigation_agent import DomainTerminationException
    from app.services.investigation_service import run_investigation

    monkeypatch.setenv("TRACE_CACHE_DIR", str(tmp_path / "cache"))
    repo = GitHubRepository("owner", "repo", "sha1", "main", {"language": "Python"})
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("print('hello')", encoding="utf-8")
    snapshot = cast(Any, SimpleNamespace(
        gh_repo=repo,
        root_path=root,
        extracted_files=frozenset({"main.py"}),
        list_top_level_files=lambda: ["main.py"],
        get_readme=lambda: None,
    ))
    trace = InvestigationTrace(started_at="now", question_chars=1)

    async def collect_events():
        with patch(
            "app.services.investigation_service.investigation_agent.run",
            new=AsyncMock(side_effect=DomainTerminationException("max_actions")),
        ):
            return [event async for event in run_investigation(snapshot, "q", trace)]

    events = asyncio.run(collect_events())

    assert not any(isinstance(event, CitationMetadata) for event in events)
