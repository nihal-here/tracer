from pydantic import BaseModel, Field
from typing import Optional

class EvidenceGroupRequirement(BaseModel):
    name: str = Field(..., description="Semantic name for this group of evidence, e.g., 'Token Extraction'")
    alternatives: set[str] = Field(..., description="A set of valid file paths. The model only needs to read AT LEAST ONE of these to satisfy this group requirement.")

class EvaluationCase(BaseModel):
    id: str
    repository_url: str
    question: str
    expected_evidence_groups: list[EvidenceGroupRequirement]
    expected_answer_terms: set[str]

class RunMetadata(BaseModel):
    timestamp: str
    model: str
    repository_revision: Optional[str] = Field(None, description="The exact commit SHA resolved for this run.")
    repository_branch: Optional[str] = Field(None, description="The human readable branch name.")
    case_id: str
    schema_version: str = "1.1"
    pricing_assumption: str = "Pricing not dynamically queried. See manual pricing docs."

class EvaluationResult(BaseModel):
    metadata: RunMetadata
    success: bool
    evidence_completeness_score: float
    answer_expected_terms_score: float
    files_read: list[str]
    tool_sequence: list[str]
    searches_used: int
    directory_listings_used: int
    model_requests: int
    input_tokens: int
    output_tokens: int
    repository_resolution_latency: Optional[float] = None
    materialization_latency: Optional[float] = None
    investigation_latency: Optional[float] = None
    answer_generation_latency: Optional[float] = None
    total_latency: Optional[float] = None
    termination_reason: Optional[str] = None
    evidence_char_count: int
    note: str = "per-request context growth is not yet measured"

class SuiteSummary(BaseModel):
    total_cases: int
    successful_cases: int
    average_evidence_completeness: float
    average_answer_expected_terms_score: float
    total_input_tokens: int
    total_output_tokens: int
    
    # Averages across all cases
    all_average_input_tokens: float
    all_average_output_tokens: float
    all_average_investigation_latency: float
    all_average_answer_generation_latency: float
    all_average_total_latency: float
    
    # Averages across successful cases only
    success_average_input_tokens: float
    success_average_output_tokens: float
    success_average_investigation_latency: float
    success_average_answer_generation_latency: float
    success_average_total_latency: float
    
    total_searches_used: int
    total_directory_listings_used: int

class SuiteEvaluationResult(BaseModel):
    summary: SuiteSummary
    results: list[EvaluationResult]
