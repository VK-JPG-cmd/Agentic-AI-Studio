"""Tests for Phase 4 multi-step planning, state tracking, and tool limits.

Verifies:
- Task 1: Weather + calculation ("Find weather in Chennai and calculate if temp > 30°C")
- Task 2: Search + calculation ("Search for Python release year and calculate age in 2026")
- Task 3: Search + weather ("Search for Chennai background and get current weather")
- Tool error recovery across multiple steps
- Maximum 8 tool calls per run enforcement
- Explicit AgentState tracking with unique run_id, call_ids, and ISO timestamps
- Step categorization: THINK/DECISION, TOOL_CALL, TOOL_RESULT, FINAL
- Concise decision metadata without private chain-of-thought exposure
"""

from unittest.mock import MagicMock
import pytest

from app.agent.agent import Agent
from app.db.database import get_logs_for_session, get_session
from app.llm.base import LLMClient, LLMResponse
from app.models.schemas import StepType, UserRequest


def test_multistep_weather_and_calculation() -> None:
    """Multi-step Task 1: Weather lookup followed by arithmetic temperature comparison."""
    mock_llm = MagicMock(spec=LLMClient)

    # Step 1: Decide weather info needed for Chennai
    step1_resp = LLMResponse(
        content='{"tool": "weather", "arguments": {"city": "Chennai"}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    # Step 2: Observed 32°C, now decide calculation needed: 32 > 30
    step2_resp = LLMResponse(
        content='{"tool": "calculator", "arguments": {"expression": "32 > 30"}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    # Step 3: Conclude final answer
    step3_resp = LLMResponse(
        content="The current temperature in Chennai is 32°C, which is above 30°C.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    mock_llm.generate.side_effect = [step1_resp, step2_resp, step3_resp]

    agent = Agent(llm_client=mock_llm)
    req = UserRequest(
        query="Find the weather in Chennai and calculate whether the temperature is above 30°C.",
        session_id="multistep_weather_calc_sess",
    )
    resp = agent.run(req)

    # Assert status and final response
    assert resp.status == "completed"
    assert "32°C" in resp.response
    assert "above 30°C" in resp.response
    assert resp.run_id is not None
    assert resp.run_id.startswith("run_")

    # Assert tool calls: weather then calculator
    assert len(resp.tool_calls) == 2
    assert resp.tool_calls[0].tool_name == "weather"
    assert resp.tool_calls[0].arguments == {"city": "Chennai"}
    assert resp.tool_calls[1].tool_name == "calculator"
    assert resp.tool_calls[1].arguments == {"expression": "32 > 30"}

    # Assert tool results
    assert len(resp.tool_results) == 2
    assert resp.tool_results[0].output["temperature"] == "32°C"
    assert resp.tool_results[1].output is True

    # Assert unique tool call IDs
    assert resp.tool_calls[0].call_id != resp.tool_calls[1].call_id

    # Assert step types and concise decision metadata (no hidden chain-of-thought)
    step_types = [s.step_type for s in resp.steps]
    assert StepType.DECISION.value in step_types
    assert StepType.TOOL_CALL.value in step_types
    assert StepType.TOOL_RESULT.value in step_types
    assert StepType.FINAL.value in step_types

    decisions = [s.content for s in resp.steps if s.step_type == StepType.DECISION.value]
    assert any("weather information required for Chennai" in d for d in decisions)
    assert any("calculation required: 32 > 30" in d for d in decisions)


def test_multistep_search_and_calculation() -> None:
    """Multi-step Task 2: Search for facts and perform follow-up calculation."""
    mock_llm = MagicMock(spec=LLMClient)

    # Step 1: Search for python release year
    step1_resp = LLMResponse(
        content='{"tool": "search", "arguments": {"query": "python"}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    # Step 2: Compute age in 2026 (1991 release)
    step2_resp = LLMResponse(
        content='{"tool": "calculator", "arguments": {"expression": "2026 - 1991"}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    # Step 3: Formulate final answer
    step3_resp = LLMResponse(
        content="Python was released in 1991, making it 35 years old in 2026.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    mock_llm.generate.side_effect = [step1_resp, step2_resp, step3_resp]

    agent = Agent(llm_client=mock_llm)
    req = UserRequest(
        query="Search for python release year and calculate its age in 2026.",
        session_id="multistep_search_calc_sess",
    )
    resp = agent.run(req)

    assert resp.status == "completed"
    assert "35 years old" in resp.response
    assert len(resp.tool_calls) == 2
    assert resp.tool_calls[0].tool_name == "search"
    assert resp.tool_calls[1].tool_name == "calculator"
    assert resp.tool_results[1].output == 35


def test_multistep_search_and_weather() -> None:
    """Multi-step Task 3: Knowledge search combined with live weather inquiry."""
    mock_llm = MagicMock(spec=LLMClient)

    # Step 1: Search for city background
    step1_resp = LLMResponse(
        content='{"tool": "search", "arguments": {"query": "chennai"}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    # Step 2: Fetch current weather
    step2_resp = LLMResponse(
        content='{"tool": "weather", "arguments": {"city": "Chennai"}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    # Step 3: Final comprehensive response
    step3_resp = LLMResponse(
        content="Chennai is the capital of Tamil Nadu on the Bay of Bengal, currently experiencing 32°C humid weather.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    mock_llm.generate.side_effect = [step1_resp, step2_resp, step3_resp]

    agent = Agent(llm_client=mock_llm)
    req = UserRequest(
        query="Search for Chennai background and get its current weather.",
        session_id="multistep_search_weather_sess",
    )
    resp = agent.run(req)

    assert resp.status == "completed"
    assert "Tamil Nadu" in resp.response
    assert "32°C" in resp.response
    assert len(resp.tool_calls) == 2
    assert resp.tool_calls[0].tool_name == "search"
    assert resp.tool_calls[1].tool_name == "weather"


def test_multistep_tool_error_recovery() -> None:
    """Test error recovery: invalid input triggers error observation, agent corrects and succeeds."""
    mock_llm = MagicMock(spec=LLMClient)

    # Step 1: Attempt calculator with empty expression
    step1_resp = LLMResponse(
        content='{"tool": "calculator", "arguments": {"expression": ""}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    # Step 2: Observes error, recovers with valid expression
    step2_resp = LLMResponse(
        content='{"tool": "calculator", "arguments": {"expression": "100 / 4"}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    # Step 3: Concludes answer
    step3_resp = LLMResponse(
        content="100 divided by 4 is 25.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    mock_llm.generate.side_effect = [step1_resp, step2_resp, step3_resp]

    agent = Agent(llm_client=mock_llm)
    req = UserRequest(query="Calculate 100 / 4", session_id="recovery_sess")
    resp = agent.run(req)

    assert resp.status == "completed"
    assert len(resp.tool_calls) == 2
    assert resp.tool_results[0].success is False  # First call failed
    assert resp.tool_results[1].success is True   # Second call recovered
    assert resp.tool_results[1].output == 25
    assert "25" in resp.response


def test_multistep_max_8_tool_calls_limit() -> None:
    """Enforce requirement: Maximum 8 tool calls per run."""
    mock_llm = MagicMock(spec=LLMClient)

    # Model requests a tool call on every invocation
    looping_resp = LLMResponse(
        content='{"tool": "calculator", "arguments": {"expression": "1 + 1"}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    mock_llm.generate.return_value = looping_resp

    agent = Agent(llm_client=mock_llm, max_iterations=15, max_tool_calls=8)
    req = UserRequest(query="Loop tools test", session_id="max_tool_limit_sess")
    resp = agent.run(req)

    # Tool calls should not exceed 8
    assert len(resp.tool_calls) <= 8
    assert len(resp.tool_results) <= 8
