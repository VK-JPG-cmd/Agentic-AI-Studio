from unittest.mock import patch
import pytest
from app.tools.registry import (
    BaseTool,
    BrowserTool,
    CalculatorTool,
    EchoTool,
    OpenTabTool,
    SearchTool,
    WeatherTool,
    ToolRegistry,
    get_default_registry,
)


def test_echo_tool() -> None:
    """Test EchoTool execution and argument validation."""
    tool = EchoTool()
    result = tool.run(arguments={"message": "hello agent"})
    assert result.success is True
    assert result.output == "Echo: hello agent"

    # Missing argument
    fail_res = tool.run(arguments={})
    assert fail_res.success is False
    assert "Invalid arguments" in (fail_res.error or "")


def test_calculator_tool_valid() -> None:
    """Test CalculatorTool with arithmetic expressions and functions."""
    calc = CalculatorTool()
    res1 = calc.run(arguments={"expression": "25 * 4"})
    assert res1.success is True
    assert res1.output == 100

    res2 = calc.run(arguments={"expression": "sqrt(144) + 10"})
    assert res2.success is True
    assert res2.output == 22.0

    res3 = calc.run(arguments={"expression": "100 / 4"})
    assert res3.success is True
    assert res3.output == 25


def test_calculator_tool_safety_no_arbitrary_code() -> None:
    """Requirement 11: Ensure calculator strictly rejects arbitrary Python code execution."""
    calc = CalculatorTool()

    # Attempt import
    res1 = calc.run(arguments={"expression": "__import__('os').system('ls')"})
    assert res1.success is False
    assert "Error executing tool 'calculator'" in (res1.error or "")

    # Attempt eval/exec
    res2 = calc.run(arguments={"expression": "eval('2+2')"})
    assert res2.success is False

    # Attempt attribute access
    res3 = calc.run(arguments={"expression": "().__class__.__bases__"})
    assert res3.success is False


def test_calculator_division_by_zero() -> None:
    """Ensure math execution errors (e.g. division by zero) are caught cleanly."""
    calc = CalculatorTool()
    res = calc.run(arguments={"expression": "10 / 0"})
    assert res.success is False
    assert "division by zero" in (res.error or "").lower()


def test_weather_tool() -> None:
    """Test WeatherTool deterministic outputs and Pydantic validation."""
    weather = WeatherTool()

    # Deterministic Chennai weather
    res_chennai = weather.run(arguments={"city": "Chennai"})
    assert res_chennai.success is True
    assert res_chennai.output["city"] == "Chennai"
    assert res_chennai.output["temperature"] == "32°C"
    assert "Humid" in res_chennai.output["condition"]

    # London weather
    res_london = weather.run(arguments={"city": "London"})
    assert res_london.success is True
    assert res_london.output["temperature"] == "15°C"

    # Default fallback for unlisted city
    res_other = weather.run(arguments={"city": "Berlin"})
    assert res_other.success is True
    assert res_other.output["city"] == "Berlin"

    # Pydantic validation failure (missing city)
    res_invalid = weather.run(arguments={})
    assert res_invalid.success is False
    assert "Invalid arguments" in (res_invalid.error or "")


def test_search_tool() -> None:
    """Test SearchTool deterministic mock results and Pydantic validation."""
    search = SearchTool()

    # Search for known term
    res_fastapi = search.run(arguments={"query": "fastapi"})
    assert res_fastapi.success is True
    assert "FastAPI is a modern" in str(res_fastapi.output)

    # Search for unknown term
    res_unknown = search.run(arguments={"query": "nonexistent_topic_12345"})
    assert res_unknown.success is True
    assert "No mock search results found" in str(res_unknown.output)

    # Pydantic validation failure (missing query)
    res_invalid = search.run(arguments={})
    assert res_invalid.success is False
    assert "Invalid arguments" in (res_invalid.error or "")


def test_tool_registry() -> None:
    """Test ToolRegistry discovery, schemas, prompts, and unregistered tool handling."""
    registry = get_default_registry()
    names = registry.list_tool_names()
    assert "calculator" in names
    assert "weather" in names
    assert "search" in names
    assert "echo" in names

    # Prevent duplicate
    with pytest.raises(ValueError):
        registry.register(EchoTool())

    # Descriptions prompt formatting (Requirement 4)
    prompt_desc = registry.get_descriptions_prompt()
    assert "calculator" in prompt_desc
    assert "weather" in prompt_desc
    assert "search" in prompt_desc

    # Execute valid tool through registry
    exec_res = registry.execute("calculator", {"expression": "10 * 5"})
    assert exec_res.success is True
    assert exec_res.output == 50

    # Execute unregistered tool (Requirement 12)
    unknown_res = registry.execute("magic_wand", {})
    assert unknown_res.success is False
    assert "Tool 'magic_wand' is not registered" in (unknown_res.error or "")
    assert "Available tools:" in (unknown_res.error or "")


def test_browser_tool_valid_url() -> None:
    """Test BrowserTool opening a valid URL with mock."""
    tool = BrowserTool()
    with patch("webbrowser.open_new_tab", return_value=True) as mock_open:
        res = tool.run(arguments={"url": "https://www.google.com"})
        assert res.success is True
        assert res.output["status"] == "success"
        assert res.output["url"] == "https://www.google.com"
        mock_open.assert_called_once_with("https://www.google.com")


def test_browser_tool_default_blank_tab() -> None:
    """Test BrowserTool with default arguments opens about:blank."""
    tool = BrowserTool()
    with patch("webbrowser.open_new_tab", return_value=True) as mock_open:
        res = tool.run(arguments={})
        assert res.success is True
        assert res.output["url"] == "about:blank"
        mock_open.assert_called_once_with("about:blank")


def test_browser_tool_url_scheme_prepended() -> None:
    """Test BrowserTool automatically prepends https:// if missing."""
    tool = BrowserTool()
    with patch("webbrowser.open_new_tab", return_value=True) as mock_open:
        res = tool.run(arguments={"url": "github.com"})
        assert res.success is True
        assert res.output["url"] == "https://github.com"
        mock_open.assert_called_once_with("https://github.com")


def test_open_tab_alias_tool() -> None:
    """Test OpenTabTool alias works identically."""
    tool = OpenTabTool()
    assert tool.name == "open_tab"
    with patch("webbrowser.open_new_tab", return_value=True) as mock_open:
        res = tool.run(arguments={"url": "about:blank"})
        assert res.success is True
        mock_open.assert_called_once_with("about:blank")
