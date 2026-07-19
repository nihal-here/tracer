"""Sanitized user-visible investigation steps."""

from __future__ import annotations

from typing import Any

from app.investigation_trace import AgentStepTrace
from app.services.citations import sanitize_repository_path


def build_public_investigation_steps(steps: list[AgentStepTrace]) -> list[dict[str, Any]]:
    """Convert internal action telemetry into safe summaries without tool output or prompts."""
    public_steps: list[dict[str, Any]] = []
    for step in steps:
        item: dict[str, Any] = {
            "action_number": step.action_number,
            "tool": step.action_chosen,
            "path": None,
            "query": None,
            "start_line": None,
            "end_line": None,
            "result_summary": "Action completed.",
        }
        if step.action_chosen == "read_file" and step.read_file_metadata is not None:
            metadata = step.read_file_metadata
            item["path"] = sanitize_repository_path(metadata.requested_path)
            item["start_line"] = metadata.actual_start_line
            item["end_line"] = metadata.actual_end_line
            if item["path"] is not None and metadata.actual_start_line is not None:
                item["result_summary"] = f"Read {item['path']} lines {metadata.actual_start_line}-{metadata.actual_end_line}."
                if metadata.truncated:
                    item["result_summary"] += " The span was truncated."
        elif step.action_chosen == "search_code" and step.search_code_metadata is not None:
            metadata = step.search_code_metadata
            item["query"] = metadata.query[:120]
            item["path"] = sanitize_repository_path(metadata.scope) if metadata.scope else None
            scope = f" in {item['path']}" if item["path"] else ""
            item["result_summary"] = f"Searched for {metadata.query[:120]!r}{scope}; {metadata.matches_returned or 0} matches returned."
        elif step.action_chosen == "list_directory" and step.list_directory_metadata is not None:
            metadata = step.list_directory_metadata
            item["path"] = sanitize_repository_path(metadata.directory_path) if metadata.directory_path else "."
            item["result_summary"] = f"Listed {item['path']}; {metadata.entries_returned or 0} entries returned."
        public_steps.append(item)
    return public_steps
