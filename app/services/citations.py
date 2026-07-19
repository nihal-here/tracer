"""Deterministic, evidence-backed source citations for final answers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Sequence
from urllib.parse import quote

from pydantic import BaseModel

from app.services.evidence_reconstruction import reconstruct_evidence_text


class CitationValidationError(ValueError):
    """Raised when a citation cannot be grounded in observed repository lines."""


class SourceCitation(BaseModel):
    citation_id: str
    path: str
    start_line: int
    end_line: int
    commit_sha: str
    url: str | None = None


@dataclass(frozen=True)
class CitationEvidence:
    citation: SourceCitation
    evidence: str


@dataclass(frozen=True)
class AnswerCitationValidation:
    referenced_ids: tuple[str, ...]
    unknown_ids: tuple[str, ...]
    malformed_tokens: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.unknown_ids and not self.malformed_tokens


def sanitize_repository_path(path: str) -> str | None:
    """Return a safe repository-relative path, or None for host/path traversal input."""
    if not path or path.startswith(("/", "\\")) or "\\" in path:
        return None
    if any(ord(char) < 32 for char in path):
        return None
    parts = PurePosixPath(path).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return None
    return path


def immutable_github_url(owner: str, name: str, commit_sha: str, path: str, start_line: int, end_line: int) -> str | None:
    """Build an immutable GitHub blob URL only for validated-looking inputs."""
    safe_path = sanitize_repository_path(path)
    if safe_path is None or not re.fullmatch(r"[0-9a-fA-F]{7,64}", commit_sha):
        return None
    if not owner or not name or any(
        char in owner + name for char in "/\\"
    ) or any(ord(char) < 32 for char in owner + name):
        return None
    if start_line < 1 or end_line < start_line:
        return None
    return (
        f"https://github.com/{quote(owner, safe='-_.~')}/{quote(name, safe='-_.~')}"
        f"/blob/{quote(commit_sha, safe='-_.~')}/{quote(safe_path, safe='/~_.-')}"
        f"#L{start_line}-L{end_line}"
    )


def build_citation_evidence(
    excerpts: Sequence[Any],
    spans: Sequence[Any],
    owner: str,
    name: str,
    commit_sha: str,
) -> list[CitationEvidence]:
    """Validate excerpts, merge overlaps, and assign stable citation IDs."""
    ranges: set[tuple[str, int, int]] = set()
    for excerpt in excerpts:
        if sanitize_repository_path(excerpt.path) is None:
            raise CitationValidationError(f"Unsafe repository path: {excerpt.path!r}")
        if excerpt.start_line < 1 or excerpt.end_line < excerpt.start_line:
            raise CitationValidationError(f"Invalid citation range for {excerpt.path}")
        try:
            reconstruct_evidence_text([excerpt], spans)
        except Exception as exc:
            raise CitationValidationError(str(exc)) from exc
        ranges.add((excerpt.path, excerpt.start_line, excerpt.end_line))

    merged: list[tuple[str, int, int]] = []
    for path, start_line, end_line in sorted(ranges):
        if merged and merged[-1][0] == path and start_line <= merged[-1][2]:
            previous_path, previous_start, previous_end = merged[-1]
            merged[-1] = (previous_path, previous_start, max(previous_end, end_line))
        else:
            merged.append((path, start_line, end_line))

    citations: list[CitationEvidence] = []
    for index, (path, start_line, end_line) in enumerate(merged, start=1):
        excerpt = type("CitationExcerpt", (), {
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "justification": "",
        })()
        evidence = reconstruct_evidence_text([excerpt], spans)
        citation = SourceCitation(
            citation_id=str(index),
            path=path,
            start_line=start_line,
            end_line=end_line,
            commit_sha=commit_sha,
            url=immutable_github_url(owner, name, commit_sha, path, start_line, end_line),
        )
        citations.append(CitationEvidence(citation=citation, evidence=evidence))
    return citations


def validate_answer_citations(answer: str, citations: Sequence[SourceCitation]) -> AnswerCitationValidation:
    """Validate citation tokens after streaming without altering the answer text."""
    allowed = {citation.citation_id for citation in citations}
    referenced: set[str] = set()
    unknown: set[str] = set()
    malformed: set[str] = set()
    for match in re.finditer(r"\[([^]\n]*)\]", answer):
        token = match.group(1).strip()
        if token.isdigit():
            if token in allowed:
                referenced.add(token)
            else:
                unknown.add(token)
        elif token:
            malformed.add(token)
    return AnswerCitationValidation(
        referenced_ids=tuple(sorted(referenced, key=lambda value: int(value))),
        unknown_ids=tuple(sorted(unknown, key=lambda value: int(value))),
        malformed_tokens=tuple(sorted(malformed)),
    )
