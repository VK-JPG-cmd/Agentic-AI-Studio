"""Tests for FastAPI endpoints /health, /agent/run, and dev test endpoints."""

from unittest.mock import patch
from fastapi.testclient import TestClient

from app.db.database import get_logs_for_session, get_session
from app.llm.base import (
    LLMAuthenticationError,
    LLMProviderError,
    LLMResponse,
    LLMTimeoutError,
)


def test_health_endpoint(client: TestClient) -> None:
    """Test GET /health returns 200 with system status and no secrets."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert "echo" in data["tools_available"]
    assert "calculator" in data["tools_available"]
    assert "llm_model" in data
    assert "llm_configured" in data
    # Ensure no secrets leaked
    assert "hf_token" not in data
    assert "token" not in data


def test_agent_run_basic(client: TestClient) -> None:
    """Test POST /agent/run returns LLM response and records state in SQLite."""
    mock_llm_resp = LLMResponse(
        content="Simulated LLM response for research best practices.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
        role="assistant",
        finish_reason="stop",
    )

    with patch("app.llm.huggingface.HuggingFaceLLMClient.generate", return_value=mock_llm_resp):
        payload = {
            "query": "Research best practices for agentic architecture",
            "session_id": "api_test_session_1",
        }
        response = client.post("/agent/run", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert data["session_id"] == "api_test_session_1"
        assert data["status"] == "completed"
        assert data["response"] == "Simulated LLM response for research best practices."
        assert data["tool_calls"] == []
        assert data["tool_results"] == []

        # Verify session and logs were saved in SQLite
        session = get_session("api_test_session_1")
        assert session is not None
        assert session["status"] == "completed"

        logs = get_logs_for_session("api_test_session_1")
        assert len(logs) == 2
        assert logs[0]["role"] == "user"
        assert logs[0]["content"] == "Research best practices for agentic architecture"
        assert logs[1]["role"] in ("FINAL", "assistant")
        assert logs[1]["content"] == "Simulated LLM response for research best practices."


def test_agent_run_validation_error(client: TestClient) -> None:
    """Test POST /agent/run with invalid blank query."""
    payload = {"query": "   "}
    response = client.post("/agent/run", json=payload)
    assert response.status_code == 422


def test_agent_run_llm_auth_error(client: TestClient) -> None:
    """Test POST /agent/run returns 401 when LLM authentication fails."""
    with patch(
        "app.llm.huggingface.HuggingFaceLLMClient.generate",
        side_effect=LLMAuthenticationError("Authentication with LLM provider failed."),
    ):
        payload = {"query": "Test auth failure", "session_id": "api_test_auth_fail"}
        response = client.post("/agent/run", json=payload)
        assert response.status_code == 401
        assert "Authentication" in response.json()["detail"]


def test_agent_run_llm_timeout_error(client: TestClient) -> None:
    """Test POST /agent/run returns 504 when LLM request times out."""
    with patch(
        "app.llm.huggingface.HuggingFaceLLMClient.generate",
        side_effect=LLMTimeoutError("Hugging Face request timed out after 30s."),
    ):
        payload = {"query": "Test timeout failure", "session_id": "api_test_timeout_fail"}
        response = client.post("/agent/run", json=payload)
        assert response.status_code == 504
        assert "timed out" in response.json()["detail"].lower()


def test_agent_run_llm_provider_error(client: TestClient) -> None:
    """Test POST /agent/run returns 502 on upstream LLM provider error."""
    with patch(
        "app.llm.huggingface.HuggingFaceLLMClient.generate",
        side_effect=LLMProviderError("Upstream model error", status_code=502),
    ):
        payload = {"query": "Test provider failure", "session_id": "api_test_provider_fail"}
        response = client.post("/agent/run", json=payload)
        assert response.status_code == 502
        assert "LLM provider error" in response.json()["detail"]


def test_dev_llm_test_endpoint(client: TestClient) -> None:
    """Test dev test endpoint processing 'Say hello in one sentence.'"""
    mock_llm_resp = LLMResponse(
        content="Hello! It is a pleasure to meet you today.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
        role="assistant",
        finish_reason="stop",
    )

    with patch("app.llm.huggingface.HuggingFaceLLMClient.generate", return_value=mock_llm_resp):
        response = client.post("/agent/dev/llm-test")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["prompt"] == "Say hello in one sentence."
        assert data["response"] == "Hello! It is a pleasure to meet you today."
        assert data["model"] == "meta-llama/Meta-Llama-3-8B-Instruct"


def test_dev_llm_test_disabled_in_production(client: TestClient) -> None:
    """Ensure dev test endpoint is rejected with 404 when in production environment."""
    with patch("app.config.Settings.is_production", return_value=True):
        response = client.post("/agent/dev/llm-test")
        assert response.status_code == 404
        assert "disabled in production" in response.json()["detail"]


def test_agent_run_with_tool_call(client: TestClient) -> None:
    """Test POST /agent/run executing a full tool loop via FastAPI."""
    step1_resp = LLMResponse(
        content='{"tool": "calculator", "arguments": {"expression": "25 * 4"}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    step2_resp = LLMResponse(
        content="25 * 4 equals 100.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )

    with patch("app.llm.huggingface.HuggingFaceLLMClient.generate", side_effect=[step1_resp, step2_resp]):
        payload = {
            "query": "Calculate 25 * 4 and tell me the result.",
            "session_id": "api_test_tool_loop",
        }
        response = client.post("/agent/run", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert "100" in data["response"]
        assert len(data["tool_calls"]) == 1
        assert data["tool_calls"][0]["tool_name"] == "calculator"
        assert len(data["tool_results"]) == 1
        assert data["tool_results"][0]["output"] == 100
