import os
from google import genai
from dotenv import load_dotenv

load_dotenv()

def get_gemini_api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")
    return api_key

def get_llm_model_name() -> str:
    return os.environ.get("TRACE_LLM_MODEL", "gemini-3.1-flash-lite")

def generate_answer(prompt: str) -> str:
    client = genai.Client(api_key=get_gemini_api_key())
    model = get_llm_model_name()

    response = client.models.generate_content(
        model=model,
        contents=prompt,
    )

    return response.text or ""


def generate_answer_stream(prompt: str):
    client = genai.Client(api_key=get_gemini_api_key())
    model = get_llm_model_name()

    response_stream = client.models.generate_content_stream(
        model=model,
        contents=prompt,
    )

    for chunk in response_stream:
        yield chunk.text or ""
