import os
import json
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

def generate_answer(prompt: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)
    model = os.environ.get("TRACE_LLM_MODEL", "gemini-3.1-flash-lite")

    response = client.models.generate_content(
        model=model,
        contents=prompt,
    )

    return response.text or ""


class FileSelection(BaseModel):
    selected_files: list[str] = Field(
        description="List of exact file paths (max 5) most relevant to the question."
    )


def select_files(question: str, candidate_files: list[str]) -> list[str]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)
    model = os.environ.get("TRACE_LLM_MODEL", "gemini-3.1-flash-lite")

    prompt = (
        f"Question: {question}\n\n"
        f"Files available:\n" + "\n".join(candidate_files) + "\n\n"
        "Select up to 5 most relevant file paths to answer the question."
    )

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=FileSelection,
            temperature=0.1,
        ),
    )

    try:
        parsed = response.parsed
        if isinstance(parsed, FileSelection):
            return parsed.selected_files
        return []
    except Exception:
        return []
