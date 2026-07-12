import logging

from fastapi import HTTPException

from app.services.llm_provider import generate_answer, generate_answer_stream


logger = logging.getLogger(__name__)


def compose_answer(question: str, context: dict) -> str:
    prompt = build_prompt(question, context)
    try:
        return generate_answer(prompt)
    except Exception as e:
        logger.exception("Answer generation failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Answer generation failed",
        )

def compose_answer_stream(question: str, context: dict):
    prompt = build_prompt(question, context)
    try:
        yield from generate_answer_stream(prompt)
    except Exception as e:
        logger.exception("Answer stream failed: %s", e)
        yield "Answer generation failed."


def build_prompt(question: str, context: dict) -> str:
    # Build a string of the specific file contents we gathered
    files_context = ""
    file_contents = context.get("file_contents", {})
    if file_contents:
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
- If the context is not enough to answer confidently, say what is missing.
"""
