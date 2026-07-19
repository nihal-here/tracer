from pydantic import BaseModel, HttpUrl, Field


class InvestigateRequest(BaseModel):
    repo: HttpUrl
    question: str = Field(..., max_length=1000)


class InvestigateResponse(BaseModel):
    repo: HttpUrl
    provider: str
    owner: str
    name: str
    question: str = Field(..., max_length=1000)
    description: str | None
    stars: int
    language: str | None
    summary: str | None
    readme_available: bool
    answer: str
    sources: list[str]

class RepoRequest(BaseModel):
    repo: HttpUrl

class ReadmeResponse(BaseModel):
    repo: HttpUrl
    readme_text: str | None


class ContextResponse(BaseModel):
    repo: HttpUrl
    owner: str
    name: str
    description: str | None
    language: str | None
    readme_available: bool
    readme_preview: str | None
    top_level_files: list[str] | None
    detected_stack: str | None
    default_branch: str | None

class HealthResponse(BaseModel):
    status: str
