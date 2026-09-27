"""Mock LLM client implementation.

Provides deterministic, offline LLM responses and multi-step tool call reasoning
for local testing, demonstrations, and when external LLM API credits are depleted.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Union

from app.llm.base import LLMClient, LLMMessage, LLMResponse
from app.models.schemas import ToolCall

logger = logging.getLogger(__name__)


class MockLLMClient(LLMClient):
    """Mock LLM provider that simulates multi-step agent reasoning offline."""

    def __init__(self, model: str = "mock-agent-model-offline") -> None:
        self.model = model

    def __repr__(self) -> str:
        return f"MockLLMClient(model='{self.model}')"

    def generate(
        self,
        messages: List[Union[LLMMessage, Dict[str, str]]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> LLMResponse:
        """Simulate LLM tool reasoning and generation based on message history."""
        # 1. Normalize messages
        history: List[Dict[str, str]] = []
        for m in messages:
            if isinstance(m, LLMMessage):
                history.append(m.to_dict())
            elif isinstance(m, dict):
                history.append({"role": str(m.get("role", "")), "content": str(m.get("content", ""))})

        # Find the original user query
        user_query = ""
        for m in history:
            if m["role"] == "user":
                user_query = m["content"].strip()
                break

        query_lower = user_query.lower()

        # Check existing tool results in history
        tool_results = [m["content"] for m in history if m["role"] in ("tool", "TOOL_RESULT")]

        # Determine next step based on query and accumulated tool results
        # A) Unsupported browser/system commands
        if any(w in query_lower for w in ["open a new tab", "browser tab", "open browser", "close tab", "chrome"]):
            return LLMResponse(
                content=(
                    "I cannot open or control browser tabs because I do not have a browser automation tool. "
                    "My available tools are:\n"
                    "- `calculator`: Evaluate mathematical expressions\n"
                    "- `weather`: Fetch temperature, humidity, and forecast\n"
                    "- `search`: Look up knowledge base topics\n"
                    "- `echo`: Echo test payloads\n\n"
                    "Feel free to try asking for weather, calculations, or knowledge search!"
                ),
                model=self.model,
                role="assistant",
            )

        # B) Greetings
        if query_lower in ["hi", "hello", "hey", "say hello in one sentence.", "say hello in one sentence"]:
            return LLMResponse(
                content="Hello! I am your Agentic AI assistant, ready to plan and execute multi-step tasks.",
                model=self.model,
                role="assistant",
            )

        # C) Multi-step: Weather + Calculator (e.g. "Find the weather in Chennai and calculate whether...")
        is_weather_calc = ("weather" in query_lower and any(w in query_lower for w in ["calculate", "compare", "above", "below", "higher", "greater"]))
        if is_weather_calc:
            if not tool_results:
                # Step 1: Request weather
                city = "Chennai"
                city_match = re.search(r"in\s+([A-Za-z\s]+?)(?:\s+and|\s*$|\?)", user_query, re.IGNORECASE)
                if city_match:
                    city = city_match.group(1).strip()
                return LLMResponse(
                    content=f'weather information required for {city}\n```json\n{{"tool": "weather", "arguments": {{"city": "{city}"}}}}\n```',
                    model=self.model,
                    role="assistant",
                    tool_calls=[ToolCall(call_id="call_mock_weather", tool_name="weather", arguments={"city": city})],
                )
            elif len(tool_results) == 1:
                # Step 2: Request calculation based on weather observation
                temp = "32"
                expr = f"{temp} > 30"
                return LLMResponse(
                    content=f'calculation required: {expr}\n```json\n{{"tool": "calculator", "arguments": {{"expression": "{expr}"}}}}\n```',
                    model=self.model,
                    role="assistant",
                    tool_calls=[ToolCall(call_id="call_mock_calc", tool_name="calculator", arguments={"expression": expr})],
                )
            else:
                # Step 3: Conclude final answer
                return LLMResponse(
                    content=(
                        "Based on the observed weather data, Chennai currently has a temperature of 32°C. "
                        "Evaluating the condition (32 > 30) yields True. "
                        "Therefore, the temperature in Chennai is indeed above 30°C."
                    ),
                    model=self.model,
                    role="assistant",
                )

        # D) Weather-only query
        if "weather" in query_lower or "temperature" in query_lower:
            if not tool_results:
                city = "Chennai"
                city_match = re.search(r"in\s+([A-Za-z\s]+?)(?:\s*$|\?)", user_query, re.IGNORECASE)
                if city_match:
                    city = city_match.group(1).strip()
                return LLMResponse(
                    content=f'weather information required for {city}\n```json\n{{"tool": "weather", "arguments": {{"city": "{city}"}}}}\n```',
                    model=self.model,
                    role="assistant",
                    tool_calls=[ToolCall(call_id="call_mock_weather", tool_name="weather", arguments={"city": city})],
                )
            else:
                return LLMResponse(
                    content=f"The current weather details retrieved for your query are:\n{tool_results[0]}",
                    model=self.model,
                    role="assistant",
                )

        # E) Calculator query
        if any(w in query_lower for w in ["calculate", "math", "+", "-", "*", "/", "expression"]):
            if not tool_results:
                expr = "25 * 4"
                expr_match = re.search(r"([0-9\.\s\+\-\*\/\(\)\^\%]+)", user_query)
                if expr_match and any(c.isdigit() for c in expr_match.group(1)):
                    expr = expr_match.group(1).strip()
                return LLMResponse(
                    content=f'calculation required: {expr}\n```json\n{{"tool": "calculator", "arguments": {{"expression": "{expr}"}}}}\n```',
                    model=self.model,
                    role="assistant",
                    tool_calls=[ToolCall(call_id="call_mock_calc", tool_name="calculator", arguments={"expression": expr})],
                )
            else:
                return LLMResponse(
                    content=f"Calculation complete. Result: {tool_results[0]}",
                    model=self.model,
                    role="assistant",
                )

        # F) Search query
        if "search" in query_lower or "find" in query_lower or "lookup" in query_lower:
            if not tool_results:
                q = user_query.replace("search for", "").replace("search", "").strip() or "agentic ai"
                return LLMResponse(
                    content=f'knowledge search required for \'{q}\'\n```json\n{{"tool": "search", "arguments": {{"query": "{q}"}}}}\n```',
                    model=self.model,
                    role="assistant",
                    tool_calls=[ToolCall(call_id="call_mock_search", tool_name="search", arguments={"query": q})],
                )
            else:
                return LLMResponse(
                    content=f"Search findings for '{user_query}':\n{tool_results[0]}",
                    model=self.model,
                    role="assistant",
                )

        # G) Fallback direct answer
        return LLMResponse(
            content=(
                f"I processed your request: '{user_query}'. "
                "You can ask me to evaluate math expressions, check weather in any city, "
                "or search our knowledge base."
            ),
            model=self.model,
            role="assistant",
        )
