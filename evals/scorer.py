from evals.schema import EvidenceGroupRequirement

def score_evidence_completeness(files_read: list[str], expected_groups: list[EvidenceGroupRequirement]) -> float:
    if not expected_groups:
        return 1.0
        
    read_set = set(files_read)
    matched_groups = 0
    
    for group in expected_groups:
        # A group is satisfied if ANY of its valid alternatives were read
        if any(alt in read_set for alt in group.alternatives):
            matched_groups += 1
            
    return min(1.0, matched_groups / len(expected_groups))

import re

def extract_used_citation_ids(answer: str) -> list[str]:
    # Extract [1], [2], etc. from the answer
    # Matches strings like "[1]", returns "1"
    matches = re.findall(r'\[(\d+)\]', answer)
    # Deduplicate while preserving order
    seen = set()
    result = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result

def score_concrete_implementation(selected_paths: set[str], expected_implementations: set[str]) -> bool:
    if not expected_implementations:
        return True
    return expected_implementations.issubset(selected_paths)

def score_expected_terms(answer: str, expected_terms: set[str]) -> float:
    if not expected_terms:
        return 1.0
        
    terms_found = 0
    # Case insensitive exact string match for simplicity.
    answer_lower = answer.lower()
    
    for term in expected_terms:
        if term.lower() in answer_lower:
            terms_found += 1
            
    return terms_found / len(expected_terms)
