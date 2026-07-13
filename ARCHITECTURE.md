# Trace Architecture Documentation

This document outlines the architectural flow of Trace, detailing how requests travel from the User Interface to the AI and back.

## 1. System Overview
Trace is a monolithic Python application built on **FastAPI**. It serves a static frontend and exposes a single unified streaming endpoint (`/investigate`). It relies on two external APIs:
- **GitHub REST API**: For fetching repository metadata, file trees, and raw file contents.
- **Google Gemini API**: For intelligent file selection and answer generation.

## 2. Request Flow
When a user submits a question via the UI, the following sequence occurs:

1. **GitHub Data Gathering**:
   - `investigation_service.py` fetches the repository metadata (stars, description).
   - It fetches the recursive Git tree (`/git/trees/{branch}?recursive=1`).
   - Standard HTTP responses from GitHub are cached in memory via `cache_service.py` to prevent duplicate network requests if the same repo is queried repeatedly.

2. **Intelligent File Selection**:
   - The massive file tree is filtered to remove noise (e.g., `.venv/`, `node_modules/`).
   - The remaining file paths are sent to Gemini using Structured Outputs (`pydantic` schemas) to strictly select the 5 most relevant files.
   - The backend downloads the raw contents of those 5 specific files.

3. **Streaming the Answer**:
   - The backend constructs a master prompt containing the repository metadata and the exact contents of the selected files.
   - It initiates a `generate_content_stream` call to Gemini.
   - As byte chunks arrive from Google, `investigation_service.py` yields them as Server-Sent Events (`data: {"chunk": "..."}\n\n`).
   - The frontend reads the `ReadableStream` directly from the TCP socket, appending strings and re-rendering Markdown (`marked.js`) in real-time.

## 3. Technology Stack
- **Backend Framework**: FastAPI (Python)
- **AI Integration**: `google-genai` SDK
- **Frontend**: Vanilla HTML/CSS/JS, `marked.js`, `highlight.js`
- **Testing**: `pytest`, `unittest.mock`

## 4. Future Improvements (Phase 6+)
- **AST Parsing**: Instead of downloading entire files, clone the repo locally and parse it into an Abstract Syntax Tree (AST) to chunk code by functions/classes.
- **Context Caching**: Utilize Gemini's native Context Caching API to upload massive repositories directly to Google's servers, drastically reducing Input Tokens Per Minute (TPM) limits for consecutive questions on the same repository.
