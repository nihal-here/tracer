from evals.schema import EvidenceGroupRequirement

def score_evidence_completeness(files_read: list[str], expected_groups: list[EvidenceGroupRequirement]) -> float:
    if not expected_groups:
        return 1.0
        
    read_set = set(files_read)
    groups_satisfied = 0
    
    for group in expected_groups:
        # A group is satisfied if ANY of its valid alternatives were read
        if any(alt in read_set for alt in group.alternatives):
            groups_satisfied += 1
            
    return groups_satisfied / len(expected_groups)

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
