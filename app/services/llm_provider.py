import os

from openai import OpenAI


def generate_answer(prompt: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set")

    client = OpenAI(api_key=api_key)
    model = os.environ.get("TRACE_LLM_MODEL", "gpt-4.1-mini")

    response = client.responses.create(
        model=model,
        input=prompt,
    )

    return response.output_text
