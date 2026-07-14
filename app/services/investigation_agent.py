import os
from enum import Enum
from pydantic import BaseModel, model_validator
from google import genai
from google.genai import types
from app.services.investigation_workspace import AgentObservation

class ActionType(str, Enum):
    READ_FILE = "read_file"
    FINISH = "finish"

class InvestigationAction(BaseModel):
    action_type: ActionType
    file_path: str | None = None
    
    @model_validator(mode="after")
    def validate_action(self):
        if self.action_type == ActionType.READ_FILE and not self.file_path:
            raise ValueError("READ_FILE requires file_path.")
        if self.action_type == ActionType.FINISH and self.file_path is not None:
            raise ValueError("FINISH must not contain file_path.")
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
        "Choose the next action. If you need more evidence, use 'read_file' and provide a valid 'file_path' from the tree. "
        "If you have enough evidence to answer the question confidently, or if you cannot proceed further, use 'finish'."
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
