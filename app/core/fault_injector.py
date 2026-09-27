"""Deterministic FaultInjector for simulating step failures, compensation errors, and crashes."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class FaultTargetPhase(str, Enum):
    """Execution phase where a fault should be injected."""

    EXECUTE = "execute"
    COMPENSATE = "compensate"
    CRASH_AFTER_EXECUTE = "crash_after_execute"
    CRASH_DURING_COMPENSATE = "crash_during_compensate"


class FailureType(str, Enum):
    """Classification of deterministic injected failures."""

    STEP_FAILURE = "STEP_FAILURE"
    COMPENSATION_FAILURE = "COMPENSATION_FAILURE"
    TIMEOUT = "TIMEOUT"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PROCESS_CRASH = "PROCESS_CRASH"


class SimulatedToolFailure(RuntimeError):
    """Raised when FaultInjector triggers a simulated tool or compensation failure."""

    def __init__(
        self,
        message: str,
        *,
        failure_type: str = FailureType.STEP_FAILURE.value,
        step_number: Optional[int] = None,
        transaction_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.failure_type = failure_type
        self.step_number = step_number
        self.transaction_id = transaction_id


class SimulatedProcessCrash(RuntimeError):
    """Raised when FaultInjector simulates an unexpected process crash mid-workflow."""

    def __init__(
        self, message: str, execution_outputs: Optional[dict[str, Any]] = None
    ) -> None:
        super().__init__(message)
        self.execution_outputs: dict[str, Any] = execution_outputs or {}


class FaultRule(BaseModel):
    """Rule specifying when and how to inject a deterministic fault."""

    rule_id: str = Field(default_factory=lambda: f"fault_{uuid.uuid4().hex[:8]}")
    phase: FaultTargetPhase = FaultTargetPhase.EXECUTE
    failure_type: str = FailureType.STEP_FAILURE.value
    transaction_id: Optional[str] = None
    tool_name: Optional[str] = None
    step_index: Optional[int] = None  # 0-based index
    step_number: Optional[int] = None  # 1-based step number
    step_id: Optional[str] = None
    error_message: str = "Simulated fault triggered by FaultInjector"
    fail_once: bool = True
    triggered_count: int = 0
    active: bool = True


class FaultInjector:
    """
    Deterministic fault injection controller for testing AG02 Saga recovery and rollback.

    Supports targeting by:
    - transaction_id
    - step_number (1-based: 1, 2, 3, 4...) or step_index (0-based: 0, 1, 2, 3...)
    - tool_name or step_id
    - failure_type and phase (execute vs compensate)
    """

    def __init__(
        self,
        *,
        fail_at_step: Optional[int] = None,
        fail_compensation_at_step: Optional[int] = None,
        transaction_id: Optional[str] = None,
        failure_type: str = FailureType.STEP_FAILURE.value,
    ) -> None:
        self._rules: list[FaultRule] = []
        self._fail_at_step: Optional[int] = None
        self._fail_compensation_at_step: Optional[int] = None

        if fail_at_step is not None:
            self.set_fail_at_step(
                fail_at_step,
                transaction_id=transaction_id,
                failure_type=failure_type,
            )
        if fail_compensation_at_step is not None:
            self.set_fail_compensation_at_step(
                fail_compensation_at_step,
                transaction_id=transaction_id,
                failure_type=FailureType.COMPENSATION_FAILURE.value,
            )

    @property
    def fail_at_step(self) -> Optional[int]:
        """Return the configured 1-based step number to fail during forward execution."""
        return self._fail_at_step

    @fail_at_step.setter
    def fail_at_step(self, step_number: Optional[int]) -> None:
        """Set or clear the 1-based step number to fail during forward execution."""
        self._rules = [
            r
            for r in self._rules
            if not (r.phase == FaultTargetPhase.EXECUTE and r.step_number is not None)
        ]
        self._fail_at_step = step_number
        if step_number is not None:
            self.inject_step_failure(step_number)

    @property
    def fail_compensation_at_step(self) -> Optional[int]:
        """Return the configured 1-based step number to fail during compensation."""
        return self._fail_compensation_at_step

    @fail_compensation_at_step.setter
    def fail_compensation_at_step(self, step_number: Optional[int]) -> None:
        """Set or clear the 1-based step number to fail during compensation."""
        self._rules = [
            r
            for r in self._rules
            if not (
                r.phase == FaultTargetPhase.COMPENSATE and r.step_number is not None
            )
        ]
        self._fail_compensation_at_step = step_number
        if step_number is not None:
            self.inject_compensation_failure(step_number)

    def configure(
        self,
        *,
        transaction_id: Optional[str] = None,
        fail_at_step: Optional[int] = None,
        fail_compensation_at_step: Optional[int] = None,
        failure_type: str = FailureType.STEP_FAILURE.value,
        error_message: Optional[str] = None,
        fail_once: bool = True,
    ) -> None:
        """Configure deterministic step and/or compensation failure rules."""
        self.clear()
        if fail_at_step is not None:
            self.set_fail_at_step(
                fail_at_step,
                transaction_id=transaction_id,
                failure_type=failure_type,
                error_message=error_message,
                fail_once=fail_once,
            )
        if fail_compensation_at_step is not None:
            self.set_fail_compensation_at_step(
                fail_compensation_at_step,
                transaction_id=transaction_id,
                failure_type=FailureType.COMPENSATION_FAILURE.value,
                error_message=error_message,
                fail_once=fail_once,
            )

    def set_fail_at_step(
        self,
        step_number: int,
        *,
        transaction_id: Optional[str] = None,
        failure_type: str = FailureType.STEP_FAILURE.value,
        error_message: Optional[str] = None,
        fail_once: bool = True,
    ) -> FaultRule:
        """Configure a 1-based step number failure during forward execution."""
        self._fail_at_step = step_number
        return self.inject_step_failure(
            step_number,
            transaction_id=transaction_id,
            failure_type=failure_type,
            error_message=error_message,
            fail_once=fail_once,
        )

    def set_fail_compensation_at_step(
        self,
        step_number: int,
        *,
        transaction_id: Optional[str] = None,
        failure_type: str = FailureType.COMPENSATION_FAILURE.value,
        error_message: Optional[str] = None,
        fail_once: bool = True,
    ) -> FaultRule:
        """Configure a 1-based step number failure during compensation."""
        self._fail_compensation_at_step = step_number
        return self.inject_compensation_failure(
            step_number,
            transaction_id=transaction_id,
            failure_type=failure_type,
            error_message=error_message,
            fail_once=fail_once,
        )

    def inject_step_failure(
        self,
        step_number: int,
        *,
        transaction_id: Optional[str] = None,
        failure_type: str = FailureType.STEP_FAILURE.value,
        error_message: Optional[str] = None,
        fail_once: bool = True,
    ) -> FaultRule:
        """Inject a deterministic failure at 1-based `step_number` (e.g., 1, 2, 3, 4)."""
        if step_number < 1:
            raise ValueError("step_number must be >= 1 (1-based)")
        msg = (
            error_message
            or f"Injected {failure_type} at step {step_number}"
        )
        return self.add_rule(
            phase=FaultTargetPhase.EXECUTE,
            failure_type=failure_type,
            transaction_id=transaction_id,
            step_index=step_number - 1,
            step_number=step_number,
            error_message=msg,
            fail_once=fail_once,
        )

    def inject_compensation_failure(
        self,
        step_number: int,
        *,
        transaction_id: Optional[str] = None,
        failure_type: str = FailureType.COMPENSATION_FAILURE.value,
        error_message: Optional[str] = None,
        fail_once: bool = True,
    ) -> FaultRule:
        """Inject a deterministic compensation failure at 1-based `step_number`."""
        if step_number < 1:
            raise ValueError("step_number must be >= 1 (1-based)")
        msg = (
            error_message
            or f"Injected {failure_type} during compensation at step {step_number}"
        )
        return self.add_rule(
            phase=FaultTargetPhase.COMPENSATE,
            failure_type=failure_type,
            transaction_id=transaction_id,
            step_index=step_number - 1,
            step_number=step_number,
            error_message=msg,
            fail_once=fail_once,
        )

    def add_rule(
        self,
        *,
        phase: FaultTargetPhase | str = FaultTargetPhase.EXECUTE,
        failure_type: str = FailureType.STEP_FAILURE.value,
        transaction_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        step_index: Optional[int] = None,
        step_number: Optional[int] = None,
        step_id: Optional[str] = None,
        error_message: str = "Simulated fault triggered by FaultInjector",
        fail_once: bool = True,
    ) -> FaultRule:
        """Register a new fault injection rule."""
        target_phase = (
            phase if isinstance(phase, FaultTargetPhase) else FaultTargetPhase(phase)
        )
        resolved_index = step_index
        resolved_number = step_number
        if resolved_number is not None and resolved_index is None:
            resolved_index = resolved_number - 1
        elif resolved_index is not None and resolved_number is None:
            resolved_number = resolved_index + 1

        rule = FaultRule(
            phase=target_phase,
            failure_type=failure_type,
            transaction_id=transaction_id,
            tool_name=tool_name,
            step_index=resolved_index,
            step_number=resolved_number,
            step_id=step_id,
            error_message=error_message,
            fail_once=fail_once,
        )
        self._rules.append(rule)
        return rule

    def fail_step(
        self,
        step_index: int,
        error_message: str = "Simulated step failure",
        fail_once: bool = True,
        *,
        transaction_id: Optional[str] = None,
        failure_type: str = FailureType.STEP_FAILURE.value,
    ) -> FaultRule:
        """Convenience helper to fail a specific 0-based step_index during forward execution."""
        return self.add_rule(
            phase=FaultTargetPhase.EXECUTE,
            failure_type=failure_type,
            transaction_id=transaction_id,
            step_index=step_index,
            error_message=error_message,
            fail_once=fail_once,
        )

    def fail_tool(
        self,
        tool_name: str,
        error_message: str = "Simulated tool execution failure",
        fail_once: bool = True,
        *,
        transaction_id: Optional[str] = None,
        failure_type: str = FailureType.STEP_FAILURE.value,
    ) -> FaultRule:
        """Convenience helper to fail a specific tool by name during forward execution."""
        return self.add_rule(
            phase=FaultTargetPhase.EXECUTE,
            failure_type=failure_type,
            transaction_id=transaction_id,
            tool_name=tool_name,
            error_message=error_message,
            fail_once=fail_once,
        )

    def fail_compensation(
        self,
        *,
        tool_name: Optional[str] = None,
        step_index: Optional[int] = None,
        step_number: Optional[int] = None,
        transaction_id: Optional[str] = None,
        failure_type: str = FailureType.COMPENSATION_FAILURE.value,
        error_message: str = "Simulated compensation failure",
        fail_once: bool = True,
    ) -> FaultRule:
        """Convenience helper to fail a compensation action and trigger ROLLBACK_FAILED."""
        return self.add_rule(
            phase=FaultTargetPhase.COMPENSATE,
            failure_type=failure_type,
            transaction_id=transaction_id,
            tool_name=tool_name,
            step_index=step_index,
            step_number=step_number,
            error_message=error_message,
            fail_once=fail_once,
        )

    def simulate_crash_after_step(
        self,
        step_index: int,
        error_message: str = "Simulated process crash after step execution",
        fail_once: bool = True,
        *,
        transaction_id: Optional[str] = None,
    ) -> FaultRule:
        """Convenience helper to simulate an abrupt process crash right after a step completes."""
        return self.add_rule(
            phase=FaultTargetPhase.CRASH_AFTER_EXECUTE,
            failure_type=FailureType.PROCESS_CRASH.value,
            transaction_id=transaction_id,
            step_index=step_index,
            error_message=error_message,
            fail_once=fail_once,
        )

    def check(
        self,
        phase: FaultTargetPhase | str,
        *,
        transaction_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        step_index: Optional[int] = None,
        step_id: Optional[str] = None,
    ) -> None:
        """Check whether any active rule matches the current execution context and raise if so."""
        target_phase = (
            phase if isinstance(phase, FaultTargetPhase) else FaultTargetPhase(phase)
        )
        for rule in self._rules:
            if not rule.active or rule.phase != target_phase:
                continue
            if (
                rule.transaction_id is not None
                and rule.transaction_id != transaction_id
            ):
                continue
            if rule.tool_name is not None and rule.tool_name != tool_name:
                continue
            if rule.step_index is not None and rule.step_index != step_index:
                continue
            if rule.step_id is not None and rule.step_id != step_id:
                continue

            # Rule matched!
            rule.triggered_count += 1
            if rule.fail_once:
                rule.active = False

            if target_phase in {
                FaultTargetPhase.CRASH_AFTER_EXECUTE,
                FaultTargetPhase.CRASH_DURING_COMPENSATE,
            }:
                raise SimulatedProcessCrash(rule.error_message)
            raise SimulatedToolFailure(
                rule.error_message,
                failure_type=rule.failure_type,
                step_number=rule.step_number,
                transaction_id=transaction_id,
            )

    def list_rules(self) -> list[FaultRule]:
        """Return all registered fault rules."""
        return list(self._rules)

    def clear(self) -> None:
        """Remove all fault rules."""
        self._rules.clear()
        self._fail_at_step = None
        self._fail_compensation_at_step = None
