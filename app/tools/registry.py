"""Tool abstraction, Pydantic validation, safe execution, and tool registry.

Complies with Phase 3 requirements:
- Abstract BaseTool with name, description, Pydantic input schema, and execute() method
- Safe arithmetic calculator using AST (ZERO arbitrary code execution)
- Deterministic mock tools: calculator, weather, search
- ToolRegistry with discovery, schema export, and argument validation
"""

from abc import ABC, abstractmethod
import ast
import math
import operator
import os
import re
from typing import Any, Dict, List, Optional, Type
import uuid
import webbrowser

from pydantic import BaseModel, Field, ValidationError

from app.models.schemas import ToolResult

# ---------------------------------------------------------------------------
# Base Tool Abstraction
# ---------------------------------------------------------------------------


class BaseTool(ABC):
    """Abstract base class for all tools available to the Agent."""

    name: str
    description: str
    args_schema: Type[BaseModel]

    @abstractmethod
    def execute(self, **kwargs: Any) -> Any:
        """Core execution logic of the tool with validated keyword arguments."""
        pass

    @property
    def parameters(self) -> Dict[str, Any]:
        """Generate JSON Schema parameter definition from the Pydantic args_schema."""
        if hasattr(self, "args_schema") and self.args_schema:
            schema = self.args_schema.model_json_schema()
            return {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            }
        return {"type": "object", "properties": {}}

    def to_function_schema(self) -> Dict[str, Any]:
        """Convert tool definition to standard OpenAI/Hugging Face function schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def run(
        self,
        call_id: Optional[str] = None,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """Validate input arguments against Pydantic schema and execute safely."""
        actual_call_id = call_id or f"call_{uuid.uuid4().hex[:8]}"
        raw_args = arguments or {}

        # 1. Pydantic validation (Requirement 6 & 10)
        if hasattr(self, "args_schema") and self.args_schema:
            try:
                validated = self.args_schema.model_validate(raw_args)
                clean_args = validated.model_dump()
            except ValidationError as val_err:
                error_details = "; ".join(
                    f"{err.get('loc', [''])[0]}: {err.get('msg')}"
                    for err in val_err.errors()
                )
                return ToolResult(
                    call_id=actual_call_id,
                    tool_name=self.name,
                    output=None,
                    success=False,
                    error=f"Invalid arguments for tool '{self.name}': {error_details}",
                )
            except Exception as exc:
                return ToolResult(
                    call_id=actual_call_id,
                    tool_name=self.name,
                    output=None,
                    success=False,
                    error=f"Argument parsing error for tool '{self.name}': {str(exc)}",
                )
        else:
            clean_args = raw_args

        # 2. Execution with error handling (Requirement 7 & 10)
        try:
            output = self.execute(**clean_args)
            return ToolResult(
                call_id=actual_call_id,
                tool_name=self.name,
                output=output,
                success=True,
                error=None,
            )
        except Exception as exc:
            return ToolResult(
                call_id=actual_call_id,
                tool_name=self.name,
                output=None,
                success=False,
                error=f"Error executing tool '{self.name}': {str(exc)}",
            )


# ---------------------------------------------------------------------------
# Safe AST-Based Math Evaluator (Requirement 11: Zero arbitrary code execution)
# ---------------------------------------------------------------------------

_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_SAFE_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "pow": pow,
}

_SAFE_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
}


_SAFE_COMPARISONS = {
    ast.Gt: operator.gt,
    ast.Lt: operator.lt,
    ast.GtE: operator.ge,
    ast.LtE: operator.le,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


def _safe_eval_ast(node: ast.AST) -> Any:
    """Recursively evaluate an AST expression using whitelisted math operations only."""
    if isinstance(node, ast.Expression):
        return _safe_eval_ast(node.body)
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool)):
            return node.value
        raise ValueError(f"Disallowed constant type in expression: {type(node.value).__name__}")
    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError(f"Unsupported binary operator: {op_type.__name__}")
        left = _safe_eval_ast(node.left)
        right = _safe_eval_ast(node.right)
        return _SAFE_OPERATORS[op_type](left, right)
    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        operand = _safe_eval_ast(node.operand)
        return _SAFE_OPERATORS[op_type](operand)
    elif isinstance(node, ast.Compare):
        left = _safe_eval_ast(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            op_type = type(op)
            if op_type not in _SAFE_COMPARISONS:
                raise ValueError(f"Unsupported comparison operator: {op_type.__name__}")
            right = _safe_eval_ast(comparator)
            if not _SAFE_COMPARISONS[op_type](left, right):
                return False
            left = right
        return True
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only direct math function calls (e.g., sqrt, abs) are permitted.")
        func_name = node.func.id
        if func_name not in _SAFE_FUNCTIONS:
            raise ValueError(f"Function '{func_name}' is not authorized in calculator.")
        args = [_safe_eval_ast(arg) for arg in node.args]
        return _SAFE_FUNCTIONS[func_name](*args)
    elif isinstance(node, ast.Name):
        if node.id in _SAFE_CONSTANTS:
            return _SAFE_CONSTANTS[node.id]
        raise ValueError(f"Variable '{node.id}' is not recognized.")
    else:
        raise ValueError(f"Unsupported syntax in math expression: {type(node).__name__}")


# ---------------------------------------------------------------------------
# Mock Tool Implementations (Requirement 2)
# ---------------------------------------------------------------------------


class CalculatorInput(BaseModel):
    """Input schema for the calculator tool."""

    expression: str = Field(
        ...,
        min_length=1,
        description="The mathematical expression to evaluate (e.g., '25 * 4', 'sqrt(16) + 10')",
    )


class CalculatorTool(BaseTool):
    """Safe arithmetic calculation tool with strict AST parsing."""

    name = "calculator"
    description = (
        "Calculates basic mathematical expressions safely. "
        "Supports operators (+, -, *, /, //, %, **) and functions (sqrt, abs, round, min, max)."
    )
    args_schema = CalculatorInput

    def execute(self, **kwargs: Any) -> Any:
        expr = kwargs.get("expression", "").strip()
        if not expr:
            raise ValueError("Expression cannot be empty.")

        # Parse into Python AST
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as syn_err:
            raise ValueError(f"Invalid math syntax: {syn_err.msg}") from syn_err

        # Safely evaluate without eval() or exec()
        result = _safe_eval_ast(tree)
        # Return int if result is a whole float (e.g., 100.0 -> 100)
        if isinstance(result, float) and result.is_integer():
            return int(result)
        return result


class WeatherInput(BaseModel):
    """Input schema for the weather tool."""

    city: str = Field(
        ...,
        min_length=1,
        description="The name of the city to retrieve weather conditions for (e.g., 'Chennai')",
    )


class WeatherTool(BaseTool):
    """Deterministic mock weather reporting tool."""

    name = "weather"
    description = "Provides current weather conditions, temperature, and humidity for a given city."
    args_schema = WeatherInput

    MOCK_WEATHER = {
        "chennai": {
            "city": "Chennai",
            "temperature": "32°C",
            "condition": "Humid and partly cloudy",
            "humidity": "78%",
            "wind": "14 km/h",
        },
        "london": {
            "city": "London",
            "temperature": "15°C",
            "condition": "Overcast with light rain",
            "humidity": "80%",
            "wind": "18 km/h",
        },
        "tokyo": {
            "city": "Tokyo",
            "temperature": "19°C",
            "condition": "Clear and sunny",
            "humidity": "52%",
            "wind": "10 km/h",
        },
        "san francisco": {
            "city": "San Francisco",
            "temperature": "17°C",
            "condition": "Foggy and cool",
            "humidity": "74%",
            "wind": "16 km/h",
        },
        "new york": {
            "city": "New York",
            "temperature": "21°C",
            "condition": "Sunny",
            "humidity": "58%",
            "wind": "12 km/h",
        },
        "paris": {
            "city": "Paris",
            "temperature": "20°C",
            "condition": "Partly cloudy",
            "humidity": "62%",
            "wind": "11 km/h",
        },
    }

    def execute(self, **kwargs: Any) -> Any:
        city = kwargs.get("city", "").strip()
        key = city.lower()
        if key in self.MOCK_WEATHER:
            return self.MOCK_WEATHER[key]
        return {
            "city": city,
            "temperature": "25°C",
            "condition": "Pleasant and clear",
            "humidity": "60%",
            "wind": "12 km/h",
        }


class SearchInput(BaseModel):
    """Input schema for the search tool."""

    query: str = Field(
        ...,
        min_length=1,
        description="The search keyword or question to search the knowledge base for",
    )


class SearchTool(BaseTool):
    """Deterministic mock search tool for local knowledge lookup."""

    name = "search"
    description = "Searches a local mock knowledge base for information, articles, and facts."
    args_schema = SearchInput

    MOCK_KNOWLEDGE = {
        "python": "Python is a high-level, general-purpose programming language released in 1991 by Guido van Rossum.",
        "fastapi": "FastAPI is a modern, high-performance web framework for building APIs with Python 3.8+ using standard type hints.",
        "agentic": "Agentic AI refers to autonomous systems capable of reasoning, planning, and using tools to accomplish user goals.",
        "sqlite": "SQLite is a C-language library that provides a lightweight disk-based SQL database without a separate server process.",
        "chennai": "Chennai is the capital of the Indian state of Tamil Nadu, located on the Coromandel Coast off the Bay of Bengal.",
    }

    def execute(self, **kwargs: Any) -> Any:
        query = kwargs.get("query", "").strip().lower()
        matches = []
        for term, content in self.MOCK_KNOWLEDGE.items():
            if term in query or query in term:
                matches.append(content)
        if matches:
            return " | ".join(matches)
        return f"No mock search results found for query: '{query}'."


class EchoInput(BaseModel):
    """Input schema for the echo tool."""

    message: str = Field(..., description="Message string to echo back")


class EchoTool(BaseTool):
    """Simple mock tool returning the provided message."""

    name = "echo"
    description = "Echoes back the provided message. Useful for testing tool pipelines."
    args_schema = EchoInput

    def execute(self, **kwargs: Any) -> Any:
        message = kwargs.get("message", "")
        return f"Echo: {message}"


# Backwards compatibility alias
MockSearchTool = SearchTool


class BrowserInput(BaseModel):
    """Input schema for the browser tool."""

    url: str = Field(
        default="about:blank",
        description="The URL or web address to open in a new browser tab (e.g., 'https://www.google.com' or 'about:blank' for a new empty tab).",
    )


class BrowserTool(BaseTool):
    """Tool for opening web pages or new tabs in the default web browser."""

    name = "browser"
    description = (
        "Opens a web URL or a new blank tab in the user's default browser. "
        "Can open websites (e.g., 'https://www.google.com') or a blank new tab with 'about:blank'."
    )
    args_schema = BrowserInput

    def execute(self, **kwargs: Any) -> Any:
        url = kwargs.get("url") or "about:blank"
        url = str(url).strip().strip("'\"<>")
        if not url or url.lower() in (
            "new tab",
            "blank",
            "new_tab",
            "about:blank",
            "new tab in browser",
            "a new tab in my browser",
        ):
            target_url = "about:blank"
        elif not (
            url.startswith("http://")
            or url.startswith("https://")
            or url.startswith("file://")
            or url.startswith("about:")
        ):
            target_url = f"https://{url}"
        else:
            target_url = url

        try:
            opened = webbrowser.open_new_tab(target_url)
            
            # Windows native shell execute to open browser directly in active desktop session
            if hasattr(os, "startfile") and target_url.startswith(("http://", "https://", "file://")):
                try:
                    os.startfile(target_url)
                    opened = True
                except Exception:
                    pass

            try:
                import subprocess
                subprocess.Popen(["cmd", "/c", "start", "", target_url], shell=False)
                opened = True
            except Exception:
                pass

            return {
                "status": "success",
                "opened": opened,
                "url": target_url,
                "message": f"Successfully opened '{target_url}' in a new browser tab.",
            }
        except Exception as exc:
            raise RuntimeError(f"Failed to open browser tab: {exc}") from exc


class OpenTabTool(BrowserTool):
    """Alias for BrowserTool handling explicit 'open_tab' requests."""

    name = "open_tab"
    description = "Opens a new tab in the user's web browser, optionally with a specified URL."


# ---------------------------------------------------------------------------
# Tool Registry (Requirement 3 & 12)
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Registry maintaining available tools for discovery and execution."""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a new tool instance."""
        if tool.name in self._tools:
            raise ValueError(f"Tool with name '{tool.name}' is already registered.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[BaseTool]:
        """Fetch a registered tool by its unique name."""
        return self._tools.get(name)

    def list_tools(self) -> List[BaseTool]:
        """List all currently registered tool instances."""
        return list(self._tools.values())

    def list_tool_names(self) -> List[str]:
        """List names of all registered tools."""
        return list(self._tools.keys())

    def get_schemas(self) -> List[Dict[str, Any]]:
        """Get standard function-calling schemas for all registered tools."""
        return [tool.to_function_schema() for tool in self._tools.values()]

    def get_descriptions_prompt(self) -> str:
        """Format human-readable tool descriptions for inclusion in LLM prompt."""
        descriptions = []
        for tool in self._tools.values():
            params_desc = ", ".join(
                f"{k}: {v.get('type', 'any')} ({v.get('description', '')})"
                for k, v in tool.parameters.get("properties", {}).items()
            )
            descriptions.append(
                f"- **{tool.name}**: {tool.description}\n  Inputs: {{{params_desc}}}"
            )
        return "\n".join(descriptions)

    def execute(
        self, tool_name: str, arguments: Dict[str, Any], call_id: Optional[str] = None
    ) -> ToolResult:
        """Execute a tool by name. If tool is not registered, returns a descriptive error."""
        actual_call_id = call_id or f"call_{uuid.uuid4().hex[:8]}"

        # Requirement 12: The LLM should ONLY be able to call registered tools.
        tool = self.get(tool_name)
        if not tool:
            return ToolResult(
                call_id=actual_call_id,
                tool_name=tool_name,
                output=None,
                success=False,
                error=(
                    f"Tool '{tool_name}' is not registered. "
                    f"Available tools: {self.list_tool_names()}."
                ),
            )

        return tool.run(call_id=actual_call_id, arguments=arguments)


def get_default_registry() -> ToolRegistry:
    """Factory creating a ToolRegistry preloaded with default tools."""
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(WeatherTool())
    registry.register(SearchTool())
    registry.register(EchoTool())
    registry.register(BrowserTool())
    registry.register(OpenTabTool())
    return registry
