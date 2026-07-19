import pytest
from app.services.investigation_agent import EvidenceExcerpt, DelegatedImplementationEvidence, InvestigationResult
from app.services.investigation_workspace import EvidenceSpan
from app.services.evidence_reconstruction import reconstruct_evidence_text

def test_evidence_union():
    # Spans (what was read)
    spans = [
        EvidenceSpan(path="a.py", start_line=1, end_line=10, content="1\n2\n3\n4\n5\n6\n7\n8\n9\n10", source_action_index=0),
        EvidenceSpan(path="b.py", start_line=1, end_line=5, content="1\n2\n3\n4\n5", source_action_index=1),
    ]

    # Excerpts (what the model selected)
    relevant_excerpts = [
        EvidenceExcerpt(path="a.py", start_line=1, end_line=2, justification="a1"),
    ]

    concrete_implementations_read = [
        DelegatedImplementationEvidence(
            delegated_interface="MyInterface",
            implementations=[
                EvidenceExcerpt(path="a.py", start_line=2, end_line=4, justification="a2"), # Overlaps with relevant
                EvidenceExcerpt(path="b.py", start_line=1, end_line=2, justification="b1"),
            ]
        )
    ]

    excerpts = list(relevant_excerpts)
    for impl in concrete_implementations_read:
        excerpts.extend(impl.implementations)

    text = reconstruct_evidence_text(excerpts, spans)

    assert "--- a.py ---" in text
    assert "--- b.py ---" in text
    # The union of lines 1-2 and 2-4 is 1-4 for a.py.
    # Lines 1-4 should be reconstructed correctly.
    # "1\n2\n3\n4" should be in the text.
    assert "Lines 1-4:" in text
    assert "1\n2\n3\n4\n" in text
    # "5" should not be in the text for a.py
    assert "5\n" not in text.split("--- b.py ---")[0]
    print("Test passed! Reconstructed text:")
    print(text)

if __name__ == '__main__':
    test_evidence_union()
