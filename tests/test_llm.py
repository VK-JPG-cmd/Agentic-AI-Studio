"""Unit tests for the LLM client abstraction and HuggingFaceLLMClient with mocks."""

from unittest.mock import MagicMock, patch
import httpx
import pytest

from app.config import Settings
from app.llm.base import (
    LLMAuthenticationError,
    LLMError,
    LLMMessage,
    LLMProviderError,
    LLMResponse,
    LLMTimeoutError,
)
from app.llm.huggingface import HuggingFaceLLMClient


def test_llm_message_model() -> None:
    """Test LLMMessage instantiation and dictionary conversion."""
    msg = LLMMessage(role="user", content="Hello, LLM!")
    assert msg.role == "user"
    assert msg.content == "Hello, LLM!"
    assert msg.to_dict() == {"role": "user", "content": "Hello, LLM!"}


def test_hf_client_initialization_no_token() -> None:
    """Ensure HuggingFaceLLMClient raises LLMAuthenticationError when no token is configured."""
    empty_settings = Settings(HF_TOKEN=None)
    client = HuggingFaceLLMClient(api_token="", settings=empty_settings)
    
    with pytest.raises(LLMAuthenticationError) as exc_info:
        client.generate([LLMMessage(role="user", content="Test")])
    assert "HF_TOKEN" in str(exc_info.value)


def test_hf_client_token_never_in_repr() -> None:
    """Verify that the client's string representation never reveals the API token."""
    secret = "hf_super_secret_token_abcdef123456"
    client = HuggingFaceLLMClient(api_token=secret, model="custom-model")
    
    repr_str = repr(client)
    str_val = str(client)
    
    assert secret not in repr_str
    assert secret not in str_val
    assert "custom-model" in repr_str


def test_hf_client_generate_success() -> None:
    """Test successful generation using a mocked HTTP response."""
    client = HuggingFaceLLMClient(
        api_token="hf_mock_token_123",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
        base_url="https://api-inference.huggingface.co/v1",
        timeout=15.0,
    )

    mock_json_response = {
        "id": "chatcmpl-mock-123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "meta-llama/Meta-Llama-3-8B-Instruct",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello! I am ready to help you.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21},
    }

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_json_response
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        messages = [
            LLMMessage(role="system", content="You are helpful."),
            {"role": "user", "content": "Say hello in one sentence."},
        ]
        res = client.generate(messages)

        assert isinstance(res, LLMResponse)
        assert res.content == "Hello! I am ready to help you."
        assert res.role == "assistant"
        assert res.finish_reason == "stop"
        assert res.usage["total_tokens"] == 21

        # Check call arguments
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer hf_mock_token_123"
        payload = kwargs["json"]
        assert payload["model"] == "meta-llama/Meta-Llama-3-8B-Instruct"
        assert len(payload["messages"]) == 2
        assert payload["messages"][1]["content"] == "Say hello in one sentence."


def test_hf_client_generate_timeout() -> None:
    """Test that httpx.TimeoutException maps to LLMTimeoutError."""
    client = HuggingFaceLLMClient(api_token="hf_mock_token", timeout=5.0)

    with patch("httpx.Client.post", side_effect=httpx.TimeoutException("Read timed out")):
        with pytest.raises(LLMTimeoutError) as exc_info:
            client.generate([LLMMessage(role="user", content="Timeout test")])
        assert "timed out after 5.0s" in str(exc_info.value)


def test_hf_client_generate_auth_error_401() -> None:
    """Test that HTTP 401 Unauthorized maps to LLMAuthenticationError."""
    client = HuggingFaceLLMClient(api_token="invalid_token")

    mock_request = httpx.Request("POST", "https://api-inference.huggingface.co/v1/chat/completions")
    mock_response = httpx.Response(status_code=401, text='{"error": "Invalid username or password."}', request=mock_request)
    http_error = httpx.HTTPStatusError("401 Unauthorized", request=mock_request, response=mock_response)

    with patch("httpx.Client.post", side_effect=http_error):
        with pytest.raises(LLMAuthenticationError) as exc_info:
            client.generate([LLMMessage(role="user", content="Auth test")])
        assert "authentication failed" in str(exc_info.value).lower()


def test_hf_client_generate_rate_limit_429() -> None:
    """Test that HTTP 429 maps to LLMProviderError with status 429."""
    client = HuggingFaceLLMClient(api_token="hf_mock_token")

    mock_request = httpx.Request("POST", "https://api-inference.huggingface.co/v1/chat/completions")
    mock_response = httpx.Response(status_code=429, text='{"error": "Rate limit exceeded"}', request=mock_request)
    http_error = httpx.HTTPStatusError("429 Too Many Requests", request=mock_request, response=mock_response)

    with patch("httpx.Client.post", side_effect=http_error):
        with pytest.raises(LLMProviderError) as exc_info:
            client.generate([LLMMessage(role="user", content="Rate test")])
        assert exc_info.value.status_code == 429
        assert "rate limit" in str(exc_info.value).lower()


def test_hf_client_token_redacted_in_error() -> None:
    """Ensure upstream error messages containing the token have the token redacted."""
    secret = "hf_token_secret_12345"
    client = HuggingFaceLLMClient(api_token=secret)

    mock_request = httpx.Request("POST", "https://api-inference.huggingface.co/v1/chat/completions")
    mock_response = httpx.Response(
        status_code=500,
        text=f"Error upstream echoing header: Bearer {secret}",
        request=mock_request
    )
    http_error = httpx.HTTPStatusError("500 Server Error", request=mock_request, response=mock_response)

    with patch("httpx.Client.post", side_effect=http_error):
        with pytest.raises(LLMProviderError) as exc_info:
            client.generate([LLMMessage(role="user", content="Test")])
        assert secret not in str(exc_info.value)
        assert "[REDACTED]" in str(exc_info.value)
