"""Unit tests for Agent orchestrator with mocked LLM."""

from unittest.mock import MagicMock
import pytest

from app.agent.agent import Agent
from app.db.database import get_logs_for_session, get_session
from app.llm.base import LLMClient, LLMResponse, LLMTimeoutError
from app.models.schemas import UserRequest


def test_agent_run_success() -> None:
    """Test standard Agent pipeline: Application -> LLM -> response with DB persistence."""
    mock_llm = MagicMock(spec=LLMClient)
    mock_llm.generate.return_value = LLMResponse(
        content="This is an LLM response generated for the goal.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
        role="assistant",
        finish_reason="stop",
    )

    agent = Agent(llm_client=mock_llm)
    req = UserRequest(query="Explain microservices in one sentence.", session_id="test_agent_sess_1")

    resp = agent.run(req)

    # Validate returned schema
    assert resp.session_id == "test_agent_sess_1"
    assert resp.status == "completed"
    assert resp.response == "This is an LLM response generated for the goal."
    assert resp.tool_calls == []
    assert resp.tool_results == []

    # Verify LLM was called with user query
    mock_llm.generate.assert_called_once()
    call_args = mock_llm.generate.call_args
    called_messages = call_args.kwargs.get("messages") or call_args.args[0]
    assert any(m.content == "Explain microservices in one sentence." for m in called_messages)

    # Verify SQLite persistence
    session = get_session("test_agent_sess_1")
    assert session is not None
    assert session["status"] == "completed"

    logs = get_logs_for_session("test_agent_sess_1")
    assert len(logs) == 2
    assert logs[0]["role"] == "user"
    assert logs[0]["content"] == "Explain microservices in one sentence."
    assert logs[1]["role"] in ("FINAL", "assistant")
    assert logs[1]["content"] == "This is an LLM response generated for the goal."


def test_agent_run_llm_failure_records_failed_state() -> None:
    """Ensure that if the LLM client fails, the session is marked as failed in SQLite."""
    mock_llm = MagicMock(spec=LLMClient)
    mock_llm.generate.side_effect = LLMTimeoutError("Request timed out after 30s.")

    agent = Agent(llm_client=mock_llm)
    req = UserRequest(query="Should fail gracefully", session_id="test_agent_sess_fail")

    with pytest.raises(LLMTimeoutError):
        agent.run(req)

    # Verify SQLite record marked as failed
    session = get_session("test_agent_sess_fail")
    assert session is not None
    assert session["status"] == "failed"

    logs = get_logs_for_session("test_agent_sess_fail")
    assert len(logs) == 2
    assert logs[0]["role"] == "user"
    assert logs[1]["role"] == "system"
    assert "LLMTimeoutError" in logs[1]["content"]
