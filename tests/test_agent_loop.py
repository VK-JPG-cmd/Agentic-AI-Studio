"""Tests for the autonomous Agentic AI Loop (Phase 3 requirements).

Verifies:
- Calculator task: User Goal -> LLM Decision -> Tool Call -> Tool Result -> Final Answer
- Weather task: User Goal -> weather("Chennai") -> Result -> Final Answer
- Search task: User Goal -> search("fastapi") -> Result -> Final Answer
- Invalid tool request handling (unregistered tool)
- Invalid argument handling (Pydantic validation failure)
- Tool execution failure handling
- Maximum iteration limit enforcement
- Execution state tracking in AgentState
"""

from unittest.mock import MagicMock
import pytest

from app.agent.agent import Agent
from app.db.database import get_logs_for_session, get_session
from app.llm.base import LLMClient, LLMResponse
from app.models.schemas import ToolCall, UserRequest
from app.tools.registry import get_default_registry


def test_agent_loop_calculator_task() -> None:
    """Test full loop for: 'Calculate 25 * 4 and tell me the result.'"""
    mock_llm = MagicMock(spec=LLMClient)

    # Step 1: LLM decides to call calculator tool
    llm_step1 = LLMResponse(
        content='```json\n{"tool": "calculator", "arguments": {"expression": "25 * 4"}}\n```',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    # Step 2: After observing 100, LLM gives final answer
    llm_step2 = LLMResponse(
        content="The result of 25 * 4 is 100.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    mock_llm.generate.side_effect = [llm_step1, llm_step2]

    agent = Agent(llm_client=mock_llm)
    req = UserRequest(query="Calculate 25 * 4 and tell me the result.", session_id="calc_task_sess")
    resp = agent.run(req)

    # Assertions on response
    assert resp.status == "completed"
    assert resp.response == "The result of 25 * 4 is 100."
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].tool_name == "calculator"
    assert resp.tool_calls[0].arguments == {"expression": "25 * 4"}
    assert len(resp.tool_results) == 1
    assert resp.tool_results[0].success is True
    assert resp.tool_results[0].output == 100
    assert resp.iterations == 2

    # Assert persistence
    session = get_session("calc_task_sess")
    assert session is not None
    assert session["status"] == "completed"

    logs = get_logs_for_session("calc_task_sess")
    assert len(logs) >= 3
    roles = [l["role"] for l in logs]
    assert "user" in roles
    assert "TOOL_RESULT" in roles
    assert "FINAL" in roles


def test_agent_loop_weather_task() -> None:
    """Test full loop for: 'What is the weather in Chennai?'"""
    mock_llm = MagicMock(spec=LLMClient)

    # Step 1: LLM decides to call weather tool
    llm_step1 = LLMResponse(
        content='{"tool": "weather", "arguments": {"city": "Chennai"}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    # Step 2: LLM provides weather summary
    llm_step2 = LLMResponse(
        content="The current weather in Chennai is 32°C and humid with partly cloudy skies.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    mock_llm.generate.side_effect = [llm_step1, llm_step2]

    agent = Agent(llm_client=mock_llm)
    req = UserRequest(query="What is the weather in Chennai?", session_id="weather_task_sess")
    resp = agent.run(req)

    assert resp.status == "completed"
    assert "32°C" in resp.response
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].tool_name == "weather"
    assert resp.tool_results[0].output["city"] == "Chennai"
    assert resp.tool_results[0].output["temperature"] == "32°C"


def test_agent_loop_search_task() -> None:
    """Test full loop for knowledge search task."""
    mock_llm = MagicMock(spec=LLMClient)

    # Step 1: LLM decides to call search tool
    llm_step1 = LLMResponse(
        content='{"tool": "search", "arguments": {"query": "fastapi"}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    # Step 2: LLM answers using search observation
    llm_step2 = LLMResponse(
        content="FastAPI is a modern, high-performance web framework for building APIs with Python.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    mock_llm.generate.side_effect = [llm_step1, llm_step2]

    agent = Agent(llm_client=mock_llm)
    req = UserRequest(query="Tell me about FastAPI", session_id="search_task_sess")
    resp = agent.run(req)

    assert resp.status == "completed"
    assert "FastAPI" in resp.response
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].tool_name == "search"
    assert "FastAPI is a modern" in str(resp.tool_results[0].output)


def test_agent_loop_invalid_tool_request() -> None:
    """Requirement 10 & 12: Handle unregistered tool request gracefully."""
    mock_llm = MagicMock(spec=LLMClient)

    # Step 1: LLM requests unregistered tool
    llm_step1 = LLMResponse(
        content='{"tool": "hacker_tool", "arguments": {"target": "system"}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    # Step 2: LLM reads observation that tool is unregistered, and yields graceful message
    llm_step2 = LLMResponse(
        content="I am unable to perform this action because 'hacker_tool' is not an authorized tool.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    mock_llm.generate.side_effect = [llm_step1, llm_step2]

    agent = Agent(llm_client=mock_llm)
    req = UserRequest(query="Run hacker tool", session_id="invalid_tool_sess")
    resp = agent.run(req)

    assert resp.status == "completed"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].tool_name == "hacker_tool"
    assert resp.tool_results[0].success is False
    assert "not registered" in resp.tool_results[0].error
    assert "not an authorized tool" in resp.response


def test_agent_loop_invalid_arguments_handling() -> None:
    """Requirement 6 & 10: Handle Pydantic validation failure on invalid arguments."""
    mock_llm = MagicMock(spec=LLMClient)

    # Step 1: LLM requests calculator without required 'expression' field
    llm_step1 = LLMResponse(
        content='{"tool": "calculator", "arguments": {"wrong_field": 123}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    # Step 2: LLM receives Pydantic validation error observation and corrects/explains
    llm_step2 = LLMResponse(
        content="I encountered a validation error because 'expression' is required.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    mock_llm.generate.side_effect = [llm_step1, llm_step2]

    agent = Agent(llm_client=mock_llm)
    req = UserRequest(query="Calculate something invalid", session_id="invalid_args_sess")
    resp = agent.run(req)

    assert resp.status == "completed"
    assert resp.tool_results[0].success is False
    assert "Invalid arguments for tool 'calculator'" in resp.tool_results[0].error
    assert "validation error" in resp.response.lower()


def test_agent_loop_tool_execution_failure() -> None:
    """Requirement 10: Handle runtime tool execution exceptions (e.g. division by zero)."""
    mock_llm = MagicMock(spec=LLMClient)

    # Step 1: LLM requests division by zero
    llm_step1 = LLMResponse(
        content='{"tool": "calculator", "arguments": {"expression": "50 / 0"}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    # Step 2: LLM explains division by zero
    llm_step2 = LLMResponse(
        content="The calculation cannot be completed because division by zero is undefined.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    mock_llm.generate.side_effect = [llm_step1, llm_step2]

    agent = Agent(llm_client=mock_llm)
    req = UserRequest(query="What is 50 divided by 0?", session_id="div_zero_sess")
    resp = agent.run(req)

    assert resp.status == "completed"
    assert resp.tool_results[0].success is False
    assert "division by zero" in resp.tool_results[0].error.lower()
    assert "division by zero" in resp.response.lower()


def test_agent_loop_maximum_iteration_limit() -> None:
    """Requirement 10: Enforce maximum iteration limit if LLM keeps calling tools indefinitely."""
    mock_llm = MagicMock(spec=LLMClient)

    # LLM always returns a tool call without ever terminating
    infinite_tool_call = LLMResponse(
        content='{"tool": "echo", "arguments": {"message": "looping"}}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    mock_llm.generate.return_value = infinite_tool_call

    # Set tight max_iterations limit of 3
    agent = Agent(llm_client=mock_llm, max_iterations=3)
    req = UserRequest(query="Keep looping forever", session_id="max_iter_sess")
    resp = agent.run(req)

    assert resp.status == "max_iterations_reached"
    assert resp.iterations == 3
    assert len(resp.tool_calls) == 3
    assert "maximum allowable iterations" in resp.response.lower()

    session = get_session("max_iter_sess")
    assert session is not None
    assert session["status"] == "max_iterations_reached"


def test_agent_loop_function_tag_extraction() -> None:
    """Verify agent correctly extracts <function=name>{args} emitted by Llama 3.1."""
    mock_llm = MagicMock(spec=LLMClient)

    # Step 1: LLM outputs <function=browser>{"url": "https://huggingface.co/welcome"}
    llm_step1 = LLMResponse(
        content='<function=browser>{"url": "https://huggingface.co/welcome"}</function>',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    llm_step2 = LLMResponse(
        content="Opened Hugging Face in your browser.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    mock_llm.generate.side_effect = [llm_step1, llm_step2]

    agent = Agent(llm_client=mock_llm)
    req = UserRequest(query="open https://huggingface.co/welcome", session_id="fn_tag_sess")
    resp = agent.run(req)

    assert resp.status == "completed"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].tool_name == "browser"
    assert resp.tool_calls[0].arguments == {"url": "https://huggingface.co/welcome"}
    assert resp.tool_results[0].success is True


def test_agent_loop_echo_url_auto_repair() -> None:
    """Verify agent automatically redirects echo tool to browser tool when user requested opening a URL."""
    mock_llm = MagicMock(spec=LLMClient)

    # Step 1: LLM mistakenly outputs <function=echo>{"message": "https://huggingface.co/welcome"}
    llm_step1 = LLMResponse(
        content='<function=echo>{"message": "https://huggingface.co/welcome"}',
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    llm_step2 = LLMResponse(
        content="The page has been opened successfully.",
        model="meta-llama/Meta-Llama-3-8B-Instruct",
    )
    mock_llm.generate.side_effect = [llm_step1, llm_step2]

    agent = Agent(llm_client=mock_llm)
    req = UserRequest(query="open https://huggingface.co/welcome", session_id="echo_repair_sess")
    resp = agent.run(req)

    assert resp.status == "completed"
    assert len(resp.tool_calls) == 1
    # Successfully repaired to browser!
    assert resp.tool_calls[0].tool_name == "browser"
    assert resp.tool_calls[0].arguments == {"url": "https://huggingface.co/welcome"}
    assert resp.tool_results[0].success is True

