from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class InvestigationMetadata:
    repo: str
    provider: str
    owner: str
    name: str
    question: str
    description: str
    stars: int
    language: str
    summary: str
    readme_available: bool
    sources: list[str]


@dataclass(frozen=True)
class InvestigationFileRead:
    path: str
    chars_read: int
    cached: bool


@dataclass(frozen=True)
class InvestigationAnswerChunk:
    chunk: str


@dataclass(frozen=True)
class InvestigationCompleted:
    pass


InvestigationEvent = Union[
    InvestigationMetadata,
    InvestigationFileRead,
    InvestigationAnswerChunk,
    InvestigationCompleted
]
