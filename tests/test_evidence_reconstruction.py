import pytest
from app.services.evidence_reconstruction import reconstruct_evidence_text, GapInCoverageError
from app.services.investigation_workspace import EvidenceSpan
from app.services.investigation_agent import EvidenceExcerpt

def test_reconstruct_single_span():
    spans = [
        EvidenceSpan(path="foo.py", start_line=10, end_line=12, content="line 10\nline 11\nline 12", source_action_index=1, truncated=False)
    ]
    excerpts = [
        EvidenceExcerpt(path="foo.py", start_line=10, end_line=11, justification="justification 1")
    ]

    result = reconstruct_evidence_text(excerpts, spans)
    assert "--- foo.py ---" in result
    assert "Justifications: justification 1" in result
    assert "Lines 10-11:" in result
    assert "line 10\nline 11" in result
    assert "line 12" not in result

def test_reconstruct_adjacent_spans():
    spans = [
        EvidenceSpan(path="foo.py", start_line=10, end_line=11, content="line 10\nline 11", source_action_index=1, truncated=False),
        EvidenceSpan(path="foo.py", start_line=12, end_line=13, content="line 12\nline 13", source_action_index=2, truncated=False)
    ]
    excerpts = [
        EvidenceExcerpt(path="foo.py", start_line=11, end_line=12, justification="")
    ]

    result = reconstruct_evidence_text(excerpts, spans)
    assert "Lines 11-12:" in result
    assert "line 11\nline 12" in result

def test_reconstruct_overlapping_spans_deduplication():
    spans = [
        EvidenceSpan(path="foo.py", start_line=10, end_line=15, content="line 10\nline 11\nline 12\nline 13\nline 14\nline 15", source_action_index=1, truncated=False),
        EvidenceSpan(path="foo.py", start_line=12, end_line=18, content="line 12\nline 13\nline 14\nline 15\nline 16\nline 17\nline 18", source_action_index=2, truncated=False)
    ]
    excerpts = [
        EvidenceExcerpt(path="foo.py", start_line=10, end_line=12, justification="a"),
        EvidenceExcerpt(path="foo.py", start_line=14, end_line=16, justification="b")
    ]

    result = reconstruct_evidence_text(excerpts, spans)
    assert "Lines 10-12:" in result
    assert "Lines 14-16:" in result
    assert "line 10\nline 11\nline 12\n" in result
    assert "line 14\nline 15\nline 16\n" in result
    assert "line 13" not in result  # Not requested

def test_reconstruct_gap_rejected():
    spans = [
        EvidenceSpan(path="foo.py", start_line=10, end_line=11, content="line 10\nline 11", source_action_index=1, truncated=False),
        EvidenceSpan(path="foo.py", start_line=14, end_line=15, content="line 14\nline 15", source_action_index=2, truncated=False)
    ]
    excerpts = [
        EvidenceExcerpt(path="foo.py", start_line=10, end_line=14, justification="")
    ]

    with pytest.raises(GapInCoverageError, match="Coverage gap for foo.py: lines 12-13 were not observed"):
        reconstruct_evidence_text(excerpts, spans)
