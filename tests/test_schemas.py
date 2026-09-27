"""Tests for Pydantic data models and validation."""

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    AgentResponse,
    AgentState,
    ToolCall,
    ToolResult,
    UserRequest,
)


def test_user_request_valid() -> None:
    """Test valid UserRequest creation."""
    req = UserRequest(query="Calculate 5 * 10", session_id="s123")
    assert req.query == "Calculate 5 * 10"
    assert req.session_id == "s123"
    assert req.context == {}


def test_user_request_empty_validation() -> None:
    """Test that empty queries raise validation error."""
    with pytest.raises(ValidationError):
        UserRequest(query="")

    with pytest.raises(ValidationError):
        UserRequest(query="   ")


def test_tool_call_and_result() -> None:
    """Test ToolCall and ToolResult models."""
    call = ToolCall(tool_name="calculator", arguments={"expression": "10 / 2"})
    assert call.tool_name == "calculator"
    assert call.arguments["expression"] == "10 / 2"
    assert call.call_id.startswith("call_")

    res = ToolResult(
        call_id=call.call_id,
        tool_name=call.tool_name,
        output=5.0,
        success=True,
    )
    assert res.output == 5.0
    assert res.success is True
    assert res.error is None


def test_agent_response_model() -> None:
    """Test AgentResponse structure."""
    resp = AgentResponse(
        response="Here is your result.",
        session_id="sess_abc",
        status="completed",
        iterations=1,
    )
    assert resp.response == "Here is your result."
    assert resp.session_id == "sess_abc"
    assert resp.status == "completed"
    assert resp.tool_calls == []


def test_agent_state_model() -> None:
    """Test AgentState model."""
    state = AgentState(
        session_id="sess_xyz",
        user_goal="Find info on Python",
        current_step=1,
        max_steps=5,
    )
    assert state.session_id == "sess_xyz"
    assert state.user_goal == "Find info on Python"
    assert state.current_step == 1
    assert state.is_finished is False
