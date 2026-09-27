"""Tests for MockLLMClient and offline simulation."""

from app.llm.base import LLMMessage
from app.llm.mock import MockLLMClient


def test_mock_llm_greeting() -> None:
    """Ensure mock LLM handles greetings directly."""
    client = MockLLMClient()
    resp = client.generate([LLMMessage(role="user", content="Hi")])
    assert "Agentic AI" in resp.content
    assert resp.tool_calls is None or len(resp.tool_calls) == 0


def test_mock_llm_unsupported_browser() -> None:
    """Ensure mock LLM informs user when browser tools are requested."""
    client = MockLLMClient()
    resp = client.generate([LLMMessage(role="user", content="open a new tab in my browser")])
    assert "browser" in resp.content.lower()
    assert "calculator" in resp.content.lower()


def test_mock_llm_weather_flow() -> None:
    """Ensure mock LLM requests weather tool."""
    client = MockLLMClient()
    resp = client.generate([LLMMessage(role="user", content="What is the weather in Chennai?")])
    assert resp.tool_calls is not None
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].tool_name == "weather"
    assert resp.tool_calls[0].arguments["city"] == "Chennai"


def test_mock_llm_calculator_flow() -> None:
    """Ensure mock LLM requests calculator tool."""
    client = MockLLMClient()
    resp = client.generate([LLMMessage(role="user", content="Calculate 25 * 4")])
    assert resp.tool_calls is not None
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].tool_name == "calculator"
    assert "25 * 4" in resp.tool_calls[0].arguments["expression"]
