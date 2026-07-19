from app.services.llm_provider import generate_answer_stream
from dataclasses import dataclass
from typing import Iterator, Any

@dataclass
class AnswerGeneratorResult:
    prompt_chars: int
    chunk_generator: Iterator[str]

def prepare_answer_stream(question: str, context: dict[str, Any]) -> AnswerGeneratorResult:
    prompt = build_prompt(question, context)
    return AnswerGeneratorResult(
        prompt_chars=len(prompt),
        chunk_generator=generate_answer_stream(prompt)
    )

def compose_answer_stream(question: str, context: dict[str, Any]):
    prompt = build_prompt(question, context)
    yield from generate_answer_stream(prompt)


def build_prompt(question: str, context: dict[str, Any]) -> str:
    # Prefer deterministic citation blocks. The legacy file_contents fallback
    # remains for direct callers and older integrations.
    files_context = ""
    citation_blocks = context.get("citation_blocks", [])
    file_contents = context.get("file_contents", {})
    if citation_blocks:
        files_context = "\nValidated Source Evidence:\n"
        for block in citation_blocks:
            citation = block["citation"]
            files_context += (
                f"\n[{citation['citation_id']}] {citation['path']}"
                f":L{citation['start_line']}-L{citation['end_line']}\n"
                f"{block['evidence']}\n"
            )
    elif file_contents:
        files_context = "\nSpecific File Contents:\n"
        for path, content in file_contents.items():
            files_context += f"\n--- {path} ---\n{content}\n"

    return f"""
You are a helpful assistant that investigates GitHub repositories to answer questions.

Question:
{question}

Repository context:
- owner: {context["owner"]}
- name: {context["name"]}
- language: {context["language"]}
- description: {context["description"]}
- stars: {context["stars"]}
- readme_available: {context["readme_available"]}
- readme_preview: {context["readme_preview"]}
- top_level_files: {context["top_level_files"]}
- detected_stack: {context["detected_stack"]}
- default_branch: {context["default_branch"]}
{files_context}
Instructions:
- Answer using only the repository context above.
- Be concise and specific.
- Every repository-specific factual claim must cite one or more supplied citation IDs such as [1].
- Use only citation IDs supplied in Validated Source Evidence. Never invent citation IDs, paths, or line ranges.
- If the evidence is insufficient, explicitly say what is missing rather than relying on parametric knowledge.
"""
