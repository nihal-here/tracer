# Trace: Intelligent GitHub Repository Investigator

Trace is a high-performance, AI-powered investigation tool that allows you to instantly ask deep architectural questions about any public GitHub repository. It uses Google's Gemini AI to analyze file structures and code contents, delivering real-time streaming answers directly to your browser.

## Key Features
- **Intelligent File Selection**: Trace doesn't just blindly read code. It fetches the GitHub repository tree, filters out noise (like `node_modules`), and autonomously selects the 5 most relevant files to answer your specific question.
- **Server-Sent Events (SSE) Streaming**: Trace delivers answers byte-by-byte in real-time, providing a premium ChatGPT-like typing experience.
- **Premium Glassmorphism UI**: Built with vanilla HTML/CSS and JavaScript, the frontend features a dynamic, dark-mode glassmorphic aesthetic with Markdown syntax highlighting.

## Getting Started

### Prerequisites
- Python 3.10+
- A Google Gemini API Key
- A GitHub Personal Access Token (for increased rate limits)

### Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/trace.git
   cd trace
   ```
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Create a `.env` file in the root directory and add your API keys:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   GITHUB_TOKEN=your_github_personal_access_token_here
   TRACE_LLM_MODEL=gemini-3.1-flash-lite
   ```
   > **Note:** The `.env` file is included in `.gitignore` to prevent you from accidentally committing your secret keys to GitHub!

### Running the Application
1. Start the FastAPI server:
   ```bash
   uvicorn app.main:app --reload
   ```
2. Open your browser and navigate to `http://localhost:8000/`.
3. Paste a GitHub repository URL (e.g., `https://github.com/vitejs/vite`) and ask a question!

## Security
- **API Keys**: All API keys are loaded securely from your local `.env` file using `python-dotenv`. They are never exposed to the frontend or committed to source control.

## Testing
To run the integration tests (which use `unittest.mock` to prevent actual API calls):
```bash
pytest tests/
```