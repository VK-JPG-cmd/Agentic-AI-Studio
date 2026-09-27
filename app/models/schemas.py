"""Pydantic schemas for the Agentic AI system.

Defines data contracts for requests, tool invocations, tool outcomes, agent responses,
and execution state with step categorization and unique tracking IDs.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field, field_validator


class StepType(str, Enum):
    """Categorization of agent execution steps."""

    DECISION = "THINK/DECISION"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    FINAL = "FINAL"


class AgentStep(BaseModel):
    """A distinct recorded step in the agent's execution process."""

    step_id: str = Field(
        default_factory=lambda: f"step_{uuid.uuid4().hex[:8]}",
        description="Unique identifier for this step",
    )
    step_type: str = Field(
        ...,
        description="Classification: THINK/DECISION, TOOL_CALL, TOOL_RESULT, or FINAL",
    )
    content: str = Field(
        ...,
        description="Concise description of the decision, tool call, observation, or final answer",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO-8601 UTC timestamp of execution",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Structured step metadata (arguments, result outputs, decision rationale)",
    )


class UserRequest(BaseModel):
    """Incoming user request with the goal or prompt and optional session/context."""

    query: str = Field(..., min_length=1, description="The goal or instruction for the agent")
    session_id: Optional[str] = Field(
        default=None,
        description="Optional unique identifier for conversation/session continuity"
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional auxiliary contextual data provided by the caller"
    )

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, v: str) -> str:
        """Validate that query is not composed only of whitespace."""
        if not v.strip():
            raise ValueError("Query cannot be empty or purely whitespace.")
        return v.strip()


class ToolCall(BaseModel):
    """Specification of a tool call selected by the agent/orchestrator."""

    call_id: str = Field(
        default_factory=lambda: f"call_{uuid.uuid4().hex[:8]}",
        description="Unique identifier for this tool call"
    )
    tool_name: str = Field(..., description="Name of the tool to invoke")
    arguments: Dict[str, Any] = Field(
        default_factory=dict,
        description="Key-value arguments provided to the tool"
    )


class ToolResult(BaseModel):
    """Outcome of an executed tool call."""

    call_id: str = Field(..., description="Corresponding tool call ID")
    tool_name: str = Field(..., description="Name of the tool that executed")
    output: Any = Field(default=None, description="Result data produced by the tool")
    success: bool = Field(default=True, description="Whether the tool executed without error")
    error: Optional[str] = Field(default=None, description="Error message if execution failed")


class AgentResponse(BaseModel):
    """Final or intermediate response returned to the user/client."""

    run_id: Optional[str] = Field(default=None, description="Unique execution run identifier")
    response: str = Field(..., description="Final natural language response or summary")
    session_id: str = Field(..., description="Session identifier")
    status: str = Field(
        default="completed",
        description="Execution status (e.g., 'completed', 'tool_limit_reached', 'failed')"
    )
    steps: List[AgentStep] = Field(
        default_factory=list,
        description="Chronological sequence of typed steps executed during the run"
    )
    tool_calls: List[ToolCall] = Field(
        default_factory=list,
        description="Tool calls planned or executed in this run"
    )
    tool_results: List[ToolResult] = Field(
        default_factory=list,
        description="Results from executed tool calls"
    )
    iterations: int = 0


class AgentState(BaseModel):
    """Internal working state of an agent during execution of a goal."""

    run_id: str = Field(
        default_factory=lambda: f"run_{uuid.uuid4().hex[:8]}",
        description="Unique execution run identifier",
    )
    session_id: str = Field(..., description="Unique session identifier")
    user_goal: str = Field(..., description="Original user goal/instruction")
    current_step: int = Field(default=0, ge=0, description="Current step/iteration count")
    max_steps: int = Field(default=10, gt=0, description="Maximum allowable loop iterations")
    max_tool_calls: int = Field(default=8, gt=0, description="Maximum allowable tool calls per run")
    is_finished: bool = Field(default=False, description="Whether goal completion has been reached")
    status: str = Field(default="running", description="Current lifecycle status")
    steps: List[AgentStep] = Field(
        default_factory=list,
        description="Chronological sequence of typed steps (DECISION, TOOL_CALL, TOOL_RESULT, FINAL)"
    )
    tool_calls: List[ToolCall] = Field(
        default_factory=list,
        description="Cumulative list of tool calls executed"
    )
    tool_results: List[ToolResult] = Field(
        default_factory=list,
        description="Cumulative list of tool results obtained"
    )
    final_answer: Optional[str] = Field(
        default=None,
        description="Final answer produced by the agent when finished"
    )
    error: Optional[str] = Field(
        default=None,
        description="Execution error message if failed"
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO-8601 UTC timestamp of run initialization"
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO-8601 UTC timestamp of last update"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary execution metadata or scratchpad storage"
    )
