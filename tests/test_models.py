from pydantic import HttpUrl, ValidationError
import pytest
from app.models import InvestigateRequest

def test_investigate_request_valid_url():
    # Valid GitHub URL
    request = InvestigateRequest(
        repo=HttpUrl("https://github.com/openai/openai-python"),
        question="What does this do?"
    )
    assert str(request.repo) == "https://github.com/openai/openai-python"
    assert request.question == "What does this do?"

def test_investigate_request_invalid_url():
    # Invalid URL format (Pydantic validation should fail)
    with pytest.raises(ValidationError):
        InvestigateRequest(
            repo="not-a-url", # pyright: ignore
            question="What does this do?"
        )
