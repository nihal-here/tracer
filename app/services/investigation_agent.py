import os
from enum import Enum
from pydantic import BaseModel, model_validator
from google import genai
from google.genai import types
from app.services.investigation_workspace import AgentObservation

class ActionType(str, Enum):
    READ_FILE = "read_file"
    SEARCH_CODE = "search_code"
    FINISH = "finish"

class InvestigationAction(BaseModel):
    action_type: ActionType
    file_path: str | None = None
    search_query: str | None = None
    case_sensitive: bool = False

    @model_validator(mode="after")
    def validate_action(self):
        if self.action_type == ActionType.READ_FILE:
            if not self.file_path:
                raise ValueError("READ_FILE requires file_path.")
            if self.search_query is not None:
                raise ValueError("READ_FILE must not contain search_query.")
            if self.case_sensitive:
                raise ValueError("READ_FILE must not have case_sensitive=True.")

        elif self.action_type == ActionType.SEARCH_CODE:
            if not self.search_query:
                raise ValueError("SEARCH_CODE requires search_query.")
            if self.file_path is not None:
                raise ValueError("SEARCH_CODE must not contain file_path.")

        elif self.action_type == ActionType.FINISH:
            if self.file_path is not None or self.search_query is not None:
                raise ValueError("FINISH must not contain file_path or search_query.")
            if self.case_sensitive:
                raise ValueError("FINISH must not have case_sensitive=True.")

        return self

def choose_next_action(question: str, allowed_paths: frozenset[str], history: list[AgentObservation]) -> InvestigationAction:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)
    model = os.environ.get("TRACE_LLM_MODEL", "gemini-3.1-flash-lite")

    history_text = "None"
    if history:
        lines = []
        for obs in history:
            line = f"Action: {obs.action_type}, Path: {obs.path}, Result: {obs.result_status}"
            if obs.content:
                line += f"\n--- Content Start ---\n{obs.content}\n--- Content End ---"
            lines.append(line)
        history_text = "\n\n".join(lines)

    prompt = (
        f"You are an expert software investigator. You have access to the following repository tree:\n"
        f"{', '.join(allowed_paths)}\n\n"
        f"Question: {question}\n\n"
        f"History of actions taken so far:\n{history_text}\n\n"
        "Choose the next action. You can use:\n"
        "1. 'search_code' to find literal substrings (e.g. function names or keywords) across all files. Provide 'search_query' and optionally 'case_sensitive'.\n"
        "2. 'read_file' to fetch a complete file if you know the exact path from the tree or search results. Provide 'file_path'.\n"
        "3. 'finish' if you have enough evidence to answer the question confidently, or if you cannot proceed further."
    )

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=InvestigationAction,
            temperature=0.1,
        ),
    )

    try:
        parsed = response.parsed
        if isinstance(parsed, InvestigationAction):
            return parsed
        return InvestigationAction(action_type=ActionType.FINISH)
    except Exception:
        return InvestigationAction(action_type=ActionType.FINISH)
