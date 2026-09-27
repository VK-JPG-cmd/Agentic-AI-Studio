"""Core domain models and state machine definitions for AG02 Transactional Execution Layer."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def generate_transaction_id() -> str:
    """Generate a unique transaction identifier."""
    return f"tx_{uuid.uuid4().hex[:12]}"


def generate_step_id() -> str:
    """Generate a unique step execution identifier."""
    return f"step_{uuid.uuid4().hex[:12]}"


def generate_plan_id() -> str:
    """Generate a unique workflow plan identifier."""
    return f"plan_{uuid.uuid4().hex[:10]}"


def generate_approval_id() -> str:
    """Generate a unique approval request identifier."""
    return f"appr_{uuid.uuid4().hex[:10]}"


def generate_log_id() -> str:
    """Generate a unique durable log entry identifier."""
    return f"log_{uuid.uuid4().hex[:12]}"


class ExecutionStatus(str, Enum):
    """Execution states for transactions and steps in the AG02 Saga state machine."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    PARTIAL_ROLLBACK = "PARTIAL_ROLLBACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    RECOVERING = "RECOVERING"
    ABORTED = "ABORTED"


class ApprovalStatus(str, Enum):
    """Approval gate states for human-in-the-loop verification."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class LogEventType(str, Enum):
    """Structured transaction and step event types recorded in DurableExecutionLog."""

    TRANSACTION_CREATED = "TRANSACTION_CREATED"
    TRANSACTION_STATUS_CHANGED = "TRANSACTION_STATUS_CHANGED"
    STEP_STARTED = "STEP_STARTED"
    STEP_COMPLETED = "STEP_COMPLETED"
    STEP_FAILED = "STEP_FAILED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_RESOLVED = "APPROVAL_RESOLVED"
    COMPENSATION_STARTED = "COMPENSATION_STARTED"
    STEP_COMPENSATING = "STEP_COMPENSATING"
    STEP_COMPENSATED = "STEP_COMPENSATED"
    STEP_COMPENSATION_FAILED = "STEP_COMPENSATION_FAILED"
    COMPENSATION_COMPLETED = "COMPENSATION_COMPLETED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"
    TRANSACTION_COMPLETED = "TRANSACTION_COMPLETED"
    TRANSACTION_COMPENSATED = "TRANSACTION_COMPENSATED"
    PARTIAL_ROLLBACK = "PARTIAL_ROLLBACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    RECOVERY_STARTED = "RECOVERY_STARTED"
    RECOVERY_COMPLETED = "RECOVERY_COMPLETED"


# Valid state transitions for a transaction lifecycle
VALID_TRANSACTION_TRANSITIONS: dict[ExecutionStatus, set[ExecutionStatus]] = {
    ExecutionStatus.PENDING: {
        ExecutionStatus.RUNNING,
        ExecutionStatus.WAITING_FOR_APPROVAL,
        ExecutionStatus.COMPENSATING,
        ExecutionStatus.ABORTED,
    },
    ExecutionStatus.RUNNING: {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.WAITING_FOR_APPROVAL,
        ExecutionStatus.COMPENSATING,
        ExecutionStatus.RECOVERING,
        ExecutionStatus.ABORTED,
    },
    ExecutionStatus.WAITING_FOR_APPROVAL: {
        ExecutionStatus.RUNNING,
        ExecutionStatus.COMPENSATING,
        ExecutionStatus.ABORTED,
    },
    ExecutionStatus.FAILED: {
        ExecutionStatus.COMPENSATING,
        ExecutionStatus.RECOVERING,
        ExecutionStatus.PARTIAL_ROLLBACK,
        ExecutionStatus.ROLLBACK_FAILED,
        ExecutionStatus.COMPENSATED,
    },
    ExecutionStatus.COMPENSATING: {
        ExecutionStatus.COMPENSATED,
        ExecutionStatus.PARTIAL_ROLLBACK,
        ExecutionStatus.ROLLBACK_FAILED,
        ExecutionStatus.RECOVERING,
        ExecutionStatus.ABORTED,
    },
    ExecutionStatus.RECOVERING: {
        ExecutionStatus.COMPENSATING,
        ExecutionStatus.COMPENSATED,
        ExecutionStatus.PARTIAL_ROLLBACK,
        ExecutionStatus.ROLLBACK_FAILED,
        ExecutionStatus.RUNNING,
        ExecutionStatus.ABORTED,
    },
    ExecutionStatus.COMPLETED: {
        # Manual "Undo Button" triggers compensation on an already completed transaction
        ExecutionStatus.COMPENSATING,
    },
    ExecutionStatus.COMPENSATED: set(),
    ExecutionStatus.PARTIAL_ROLLBACK: {
        ExecutionStatus.RECOVERING,
        ExecutionStatus.COMPENSATING,
    },
    ExecutionStatus.ROLLBACK_FAILED: {
        ExecutionStatus.RECOVERING,
        ExecutionStatus.COMPENSATING,
    },
    ExecutionStatus.ABORTED: set(),
}


def is_valid_transition(current: ExecutionStatus, target: ExecutionStatus) -> bool:
    """Check whether transitioning from `current` to `target` is valid."""
    if current == target:
        return True
    return target in VALID_TRANSACTION_TRANSITIONS.get(current, set())


class RollbackAlert(BaseModel):
    """Structured alert generated when a Saga compensation action fails."""

    type: str = "ROLLBACK_FAILED"
    transaction_id: str
    failed_compensation_step: str
    message: str
    requires_manual_intervention: bool = True


class WorkflowStepSpec(BaseModel):
    """Specification of a single tool invocation step inside a WorkflowPlan."""

    step_id: Optional[str] = None
    tool: str = ""
    tool_name: str = ""
    description: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    execution_status: ExecutionStatus = ExecutionStatus.PENDING
    compensation_available: Optional[bool] = None
    compensation_tool: Optional[str] = None
    irreversible: Optional[bool] = None
    is_reversible: Optional[bool] = None
    approval_required: Optional[bool] = None
    requires_approval: Optional[bool] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_tool_key(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if "tool" in d and not d.get("tool_name"):
            d["tool_name"] = d["tool"]
        elif "tool_name" in d and not d.get("tool"):
            d["tool"] = d["tool_name"]
        return d

    @model_validator(mode="after")
    def _sync_fields(self) -> "WorkflowStepSpec":
        if self.tool and not self.tool_name:
            object.__setattr__(self, "tool_name", self.tool)
        elif self.tool_name and not self.tool:
            object.__setattr__(self, "tool", self.tool_name)

        if self.arguments and not self.inputs:
            object.__setattr__(self, "inputs", dict(self.arguments))
        elif self.inputs and not self.arguments:
            object.__setattr__(self, "arguments", dict(self.inputs))

        if self.irreversible is not None and self.is_reversible is None:
            object.__setattr__(self, "is_reversible", not self.irreversible)
        elif self.is_reversible is not None and self.irreversible is None:
            object.__setattr__(self, "irreversible", not self.is_reversible)

        if self.approval_required is not None and self.requires_approval is None:
            object.__setattr__(self, "requires_approval", self.approval_required)
        elif self.requires_approval is not None and self.approval_required is None:
            object.__setattr__(self, "approval_required", self.requires_approval)

        return self


class WorkflowPlan(BaseModel):
    """Ordered sequence of tool invocations generated by the Agent for a user goal."""

    plan_id: str = Field(default_factory=generate_plan_id)
    goal: str = "Execute transactional workflow"
    steps: list[WorkflowStepSpec] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)
    decision_metadata: dict[str, Any] = Field(default_factory=dict)


class CompensationRecord(BaseModel):
    """Audit record of a single compensation (undo) execution."""

    step_id: str
    step_index: int
    tool_name: str
    compensation_tool: Optional[str] = None
    status: ExecutionStatus
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    timestamp: str = Field(default_factory=utc_now_iso)


class StepExecution(BaseModel):
    """Runtime execution state for an individual workflow step inside a transaction."""

    step_id: str = Field(default_factory=generate_step_id)
    transaction_id: str = ""
    step_index: int = 0
    step_number: int = 1
    tool_name: str
    description: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    result_metadata: dict[str, Any] = Field(default_factory=dict)
    compensation_outputs: dict[str, Any] = Field(default_factory=dict)
    status: ExecutionStatus = ExecutionStatus.PENDING
    execution_status: ExecutionStatus = ExecutionStatus.PENDING
    compensation_available: bool = True
    compensation_tool: Optional[str] = None
    compensation_status: str = "PENDING"
    irreversible: bool = False
    is_reversible: bool = True
    approval_required: bool = False
    requires_approval: bool = False
    approval_id: Optional[str] = None
    error: Optional[str] = None
    compensation_error: Optional[str] = None
    attempt_count: int = 0
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    compensated_at: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _pre_sync(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        # Sync step_index <-> step_number
        if "step_index" in d and "step_number" not in d:
            d["step_number"] = int(d["step_index"]) + 1
        elif "step_number" in d and "step_index" not in d:
            d["step_index"] = max(0, int(d["step_number"]) - 1)

        # Sync arguments <-> inputs
        if "arguments" in d and "inputs" not in d:
            d["inputs"] = dict(d["arguments"])
        elif "inputs" in d and "arguments" not in d:
            d["arguments"] = dict(d["inputs"])

        # Sync outputs <-> result_metadata
        if "outputs" in d and "result_metadata" not in d:
            d["result_metadata"] = dict(d["outputs"])
        elif "result_metadata" in d and "outputs" not in d:
            d["outputs"] = dict(d["result_metadata"])

        # Sync status <-> execution_status
        if "execution_status" in d and "status" not in d:
            d["status"] = d["execution_status"]
        elif "status" in d and "execution_status" not in d:
            d["execution_status"] = d["status"]

        # Sync irreversible <-> is_reversible
        if "irreversible" in d and "is_reversible" not in d:
            d["is_reversible"] = not bool(d["irreversible"])
        elif "is_reversible" in d and "irreversible" not in d:
            d["irreversible"] = not bool(d["is_reversible"])

        # Sync compensation_available
        if "compensation_available" not in d and "is_reversible" in d:
            d["compensation_available"] = bool(d["is_reversible"])

        # Sync approval_required <-> requires_approval
        if "approval_required" in d and "requires_approval" not in d:
            d["requires_approval"] = bool(d["approval_required"])
        elif "requires_approval" in d and "approval_required" not in d:
            d["approval_required"] = bool(d["requires_approval"])

        return d

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name == "status":
            super().__setattr__("execution_status", value)
            super().__setattr__("updated_at", utc_now_iso())
            st_val = value.value if isinstance(value, ExecutionStatus) else str(value)
            if st_val in {"COMPENSATING", "COMPENSATED", "ROLLBACK_FAILED"}:
                super().__setattr__("compensation_status", st_val)
        elif name == "execution_status":
            super().__setattr__("status", value)
            super().__setattr__("updated_at", utc_now_iso())
            st_val = value.value if isinstance(value, ExecutionStatus) else str(value)
            if st_val in {"COMPENSATING", "COMPENSATED", "ROLLBACK_FAILED"}:
                super().__setattr__("compensation_status", st_val)
        elif name == "inputs":
            super().__setattr__("arguments", value)
        elif name == "arguments":
            super().__setattr__("inputs", value)
        elif name == "outputs":
            super().__setattr__("result_metadata", value)
        elif name == "result_metadata":
            super().__setattr__("outputs", value)
        elif name == "step_index":
            super().__setattr__("step_number", int(value) + 1)
        elif name == "step_number":
            super().__setattr__("step_index", max(0, int(value) - 1))
        elif name == "is_reversible":
            super().__setattr__("irreversible", not bool(value))
        elif name == "irreversible":
            super().__setattr__("is_reversible", not bool(value))
        elif name == "requires_approval":
            super().__setattr__("approval_required", bool(value))
        elif name == "approval_required":
            super().__setattr__("requires_approval", bool(value))


class ApprovalRequest(BaseModel):
    """Human-in-the-loop approval request for sensitive or irreversible steps."""

    type: str = "APPROVAL_REQUIRED"
    approval_id: str = Field(default_factory=generate_approval_id)
    transaction_id: str
    step_id: str
    step_index: int
    tool: str = ""
    tool_name: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    reason: Optional[str] = "This action cannot be undone."
    created_at: str = Field(default_factory=utc_now_iso)
    resolved_at: Optional[str] = None

    @model_validator(mode="after")
    def _sync_tool(self) -> "ApprovalRequest":
        if self.tool_name and not self.tool:
            object.__setattr__(self, "tool", self.tool_name)
        elif self.tool and not self.tool_name:
            object.__setattr__(self, "tool_name", self.tool)
        return self

    def to_prompt(self) -> dict[str, Any]:
        """Return the structured Phase 6 approval prompt dictionary."""
        return {
            "type": "APPROVAL_REQUIRED",
            "step_id": self.step_id,
            "tool": self.tool or self.tool_name,
            "reason": self.reason or "This action cannot be undone.",
        }


class DurableLogEntry(BaseModel):
    """Immutable log entry stored in the DurableExecutionLog."""

    sequence: int
    log_id: str = Field(default_factory=generate_log_id)
    transaction_id: str
    step_id: Optional[str] = None
    event_type: LogEventType
    status: ExecutionStatus
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utc_now_iso)


class TransactionState(BaseModel):
    """Complete execution state of a multi-step transactional workflow."""

    transaction_id: str = Field(default_factory=generate_transaction_id)
    plan_id: str
    user_goal: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    steps: list[StepExecution] = Field(default_factory=list)
    compensation_history: list[CompensationRecord] = Field(default_factory=list)
    events: list[DurableLogEntry] = Field(default_factory=list)
    alerts: list[RollbackAlert] = Field(default_factory=list)
    irreversible_side_effects: list[dict[str, Any]] = Field(default_factory=list)
    approval_request: Optional[dict[str, Any]] = None
    decision_metadata: dict[str, Any] = Field(default_factory=dict)
    restoration_status: str = "NOT_APPLICABLE"
    world_restored: bool = False
    error: Optional[str] = None
    world_snapshot_before: dict[str, Any] = Field(default_factory=dict)
    world_snapshot_after: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    completed_at: Optional[str] = None

    def transition_to(self, new_status: ExecutionStatus) -> None:
        """Transition transaction status while validating the state machine."""
        if not is_valid_transition(self.status, new_status):
            raise ValueError(
                f"Invalid transaction state transition from {self.status.value} to {new_status.value}"
            )
        self.status = new_status
        self.updated_at = utc_now_iso()
        if new_status in {
            ExecutionStatus.ROLLBACK_FAILED,
            ExecutionStatus.PARTIAL_ROLLBACK,
        }:
            self.world_restored = False
            self.restoration_status = new_status.value
        if new_status in {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.COMPENSATED,
            ExecutionStatus.PARTIAL_ROLLBACK,
            ExecutionStatus.ROLLBACK_FAILED,
            ExecutionStatus.ABORTED,
        }:
            self.completed_at = self.updated_at

    @property
    def rollback_alert(self) -> Optional[dict[str, Any]]:
        """Return the latest structured rollback alert dictionary if any."""
        if not self.alerts:
            return None
        return self.alerts[-1].model_dump()

    @property
    def initial_world(self) -> dict[str, Any]:
        """Return the world snapshot captured before the transaction started."""
        return self.world_snapshot_before

    @property
    def final_world(self) -> dict[str, Any]:
        """Return the world snapshot captured after execution or rollback."""
        return self.world_snapshot_after

    @property
    def completed_steps(self) -> list[StepExecution]:
        """Return steps that have completed forward execution and have not yet been compensated."""
        return [s for s in self.steps if s.status == ExecutionStatus.COMPLETED]

    @property
    def compensated_steps(self) -> list[StepExecution]:
        """Return steps that have been successfully compensated."""
        return [s for s in self.steps if s.status == ExecutionStatus.COMPENSATED]

    def to_test_result(self, workflow: Optional[str] = None) -> dict[str, Any]:
        """Generate the Phase 4 structured fault-injection test result dictionary."""
        failed_step_number: Optional[int] = None
        for step in self.steps:
            if step.status == ExecutionStatus.FAILED:
                failed_step_number = step.step_index + 1
                break

        execution_result = (
            "FAILED"
            if failed_step_number is not None
            or self.status
            in {
                ExecutionStatus.FAILED,
                ExecutionStatus.COMPENSATED,
                ExecutionStatus.PARTIAL_ROLLBACK,
                ExecutionStatus.ROLLBACK_FAILED,
            }
            else "SUCCESS"
        )

        if self.status == ExecutionStatus.COMPENSATED:
            rollback_result = "SUCCESS"
        elif self.status == ExecutionStatus.PARTIAL_ROLLBACK:
            rollback_result = "PARTIAL_ROLLBACK"
        elif self.status == ExecutionStatus.ROLLBACK_FAILED:
            rollback_result = "FAILED"
        else:
            rollback_result = "NONE"

        compensation_order = [
            rec.compensation_tool or f"compensate_{rec.tool_name}"
            for rec in self.compensation_history
            if rec.status == ExecutionStatus.COMPENSATED
        ]

        return {
            "workflow": workflow or self.user_goal,
            "failure_step": failed_step_number,
            "execution_result": execution_result,
            "rollback_result": rollback_result,
            "world_restored": bool(self.world_restored),
            "compensation_order": compensation_order,
        }

    def to_report(self, world_state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Generate the structured final transaction report for Phase 7."""
        return {
            "transaction_id": self.transaction_id,
            "plan_id": self.plan_id,
            "goal": self.user_goal,
            "status": self.status.value,
            "restoration_status": self.restoration_status,
            "world_restored": bool(self.world_restored),
            "decision_metadata": dict(self.decision_metadata),
            "steps": [
                {
                    "step_id": s.step_id,
                    "step_number": s.step_number,
                    "tool": s.tool_name,
                    "arguments": dict(s.arguments),
                    "status": s.status.value,
                    "outputs": dict(s.outputs),
                    "compensation_tool": s.compensation_tool,
                    "compensation_status": s.compensation_status,
                    "irreversible": s.irreversible,
                    "approval_required": s.approval_required,
                }
                for s in self.steps
            ],
            "compensation_history": [
                {
                    "step_id": c.step_id,
                    "tool": c.tool_name,
                    "compensation_tool": c.compensation_tool,
                    "status": c.status.value,
                }
                for c in self.compensation_history
            ],
            "irreversible_side_effects": list(self.irreversible_side_effects),
            "approval_request": self.approval_request,
            "error": self.error,
            "world_state": (
                world_state
                if world_state is not None
                else (self.world_snapshot_after or self.world_snapshot_before)
            ),
        }


# Alias for convenience
TransactionRecord = TransactionState
