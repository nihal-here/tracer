from app.services.llm_provider import generate_answer_stream


def compose_answer_stream(question: str, context: dict):
    prompt = build_prompt(question, context)
    yield from generate_answer_stream(prompt)


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
