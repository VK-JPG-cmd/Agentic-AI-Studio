"""CompensationManager implementing reverse-order Saga rollback and PARTIAL_ROLLBACK reporting for AG02."""

from __future__ import annotations

from app.core.durable_log import DurableExecutionLog
from app.core.fault_injector import SimulatedProcessCrash
from app.core.models import (
    CompensationRecord,
    ExecutionStatus,
    LogEventType,
    RollbackAlert,
    StepExecution,
    TransactionState,
    utc_now_iso,
)
from app.core.tool_registry import ToolExecutor


class CompensationManager:
    """Executes reverse-order Saga compensation (undo) for completed workflow steps."""

    def __init__(
        self,
        tool_executor: ToolExecutor,
        durable_log: DurableExecutionLog,
    ) -> None:
        self.tool_executor = tool_executor
        self.durable_log = durable_log

    def _record_event(
        self,
        transaction: TransactionState,
        *,
        event_type: LogEventType,
        status: ExecutionStatus,
        step_id: str | None = None,
        message: str = "",
        payload: dict | None = None,
    ) -> None:
        entry = self.durable_log.append_event(
            transaction_id=transaction.transaction_id,
            step_id=step_id,
            event_type=event_type,
            status=status,
            message=message,
            payload=payload,
        )
        transaction.events.append(entry)

    def get_steps_to_compensate(
        self, transaction: TransactionState
    ) -> list[StepExecution]:
        """
        Return steps that require compensation in strict REVERSE execution order.
        Never includes steps that failed, never completed, or were already compensated.
        """
        already_compensated_ids = {
            rec.step_id
            for rec in transaction.compensation_history
            if rec.status == ExecutionStatus.COMPENSATED
        }
        eligible = [
            step
            for step in transaction.steps
            if step.status
            in {ExecutionStatus.COMPLETED, ExecutionStatus.COMPENSATING}
            and step.step_id not in already_compensated_ids
        ]
        eligible.sort(key=lambda s: s.step_index, reverse=True)
        return eligible

    def compensate_transaction(
        self,
        transaction: TransactionState,
        *,
        reason: str = "Automatic Saga rollback after step failure",
        final_status_if_clean: ExecutionStatus = ExecutionStatus.COMPENSATED,
    ) -> TransactionState:
        """
        Compensate all completed reversible steps of `transaction` in strict reverse order.

        - If any completed step is irreversible (e.g., `send_email`), never pretend it can
          be undone: compensate all remaining reversible steps and report `PARTIAL_ROLLBACK`
          identifying the irreversible side effect(s).
        - If a reversible step's compensation function fails, report `ROLLBACK_FAILED`.
        """
        steps_to_undo = self.get_steps_to_compensate(transaction)

        if transaction.status != ExecutionStatus.COMPENSATING:
            transaction.transition_to(ExecutionStatus.COMPENSATING)

        transaction.world_restored = False
        self.durable_log.save_transaction(transaction)

        for step in steps_to_undo:
            # Rule 3: Never pretend an irreversible action can be rolled back.
            if step.irreversible or not step.compensation_available:
                step.compensation_status = "IRREVERSIBLE"
                step.compensation_error = (
                    f"Irreversible action '{step.tool_name}' cannot be undone."
                )
                irreversible_info = {
                    "step_id": step.step_id,
                    "step_number": step.step_number,
                    "step_index": step.step_index,
                    "tool": step.tool_name,
                    "tool_name": step.tool_name,
                    "arguments": dict(step.arguments),
                    "outputs": dict(step.outputs),
                    "reason": "This action cannot be undone.",
                }
                if not any(
                    item["step_id"] == step.step_id
                    for item in transaction.irreversible_side_effects
                ):
                    transaction.irreversible_side_effects.append(irreversible_info)

                self._record_event(
                    transaction,
                    event_type=LogEventType.PARTIAL_ROLLBACK,
                    status=ExecutionStatus.PARTIAL_ROLLBACK,
                    step_id=step.step_id,
                    message=f"Irreversible step {step.step_index} ({step.tool_name}) cannot be undone; continuing partial rollback",
                    payload=irreversible_info,
                )
                self.durable_log.save_transaction(transaction)
                continue

            step.status = ExecutionStatus.COMPENSATING
            comp_tool_name = step.compensation_tool or f"compensate_{step.tool_name}"
            self._record_event(
                transaction,
                event_type=LogEventType.COMPENSATION_STARTED,
                status=ExecutionStatus.COMPENSATING,
                step_id=step.step_id,
                message=f"Starting compensation '{comp_tool_name}' for step {step.step_index} ({step.tool_name})",
                payload={
                    "step_index": step.step_index,
                    "tool_name": step.tool_name,
                    "compensation_tool": step.compensation_tool,
                    "reason": reason,
                },
            )
            self.durable_log.save_transaction(transaction)

            try:
                comp_output = self.tool_executor.compensate(
                    step.tool_name,
                    step.inputs,
                    step.outputs,
                    transaction_id=transaction.transaction_id,
                    step_index=step.step_index,
                    step_id=step.step_id,
                )
            except SimulatedProcessCrash as crash_exc:
                # Compensation side effect succeeded before the crash; record it so recovery never repeats it
                step.status = ExecutionStatus.COMPENSATED
                step.compensation_outputs = crash_exc.execution_outputs
                step.compensated_at = utc_now_iso()
                transaction.compensation_history.append(
                    CompensationRecord(
                        step_id=step.step_id,
                        step_index=step.step_index,
                        tool_name=step.tool_name,
                        compensation_tool=step.compensation_tool,
                        status=ExecutionStatus.COMPENSATED,
                        inputs=step.inputs,
                        outputs=crash_exc.execution_outputs,
                    )
                )
                self._record_event(
                    transaction,
                    event_type=LogEventType.COMPENSATION_COMPLETED,
                    status=ExecutionStatus.COMPENSATED,
                    step_id=step.step_id,
                    message=f"Compensated step {step.step_index} ({step.tool_name}) prior to crash",
                    payload={
                        "step_index": step.step_index,
                        "tool_name": step.tool_name,
                        "compensation_tool": step.compensation_tool,
                        "compensation_outputs": crash_exc.execution_outputs,
                    },
                )
                self.durable_log.save_transaction(transaction)
                raise
            except Exception as exc:
                raw_msg = str(exc)
                error_msg = (
                    f"Compensation failed for step {step.step_index} ({step.tool_name}): {raw_msg}"
                )
                step.status = ExecutionStatus.ROLLBACK_FAILED
                step.compensation_error = raw_msg
                transaction.error = (
                    f"{transaction.error} | {error_msg}"
                    if transaction.error
                    else error_msg
                )
                transaction.world_restored = False
                transaction.restoration_status = "ROLLBACK_FAILED"

                alert = RollbackAlert(
                    type="ROLLBACK_FAILED",
                    transaction_id=transaction.transaction_id,
                    failed_compensation_step=step.step_id,
                    message=raw_msg,
                    requires_manual_intervention=True,
                )
                transaction.alerts.append(alert)

                transaction.compensation_history.append(
                    CompensationRecord(
                        step_id=step.step_id,
                        step_index=step.step_index,
                        tool_name=step.tool_name,
                        compensation_tool=step.compensation_tool,
                        status=ExecutionStatus.ROLLBACK_FAILED,
                        inputs=step.inputs,
                        error=raw_msg,
                    )
                )

                self._record_event(
                    transaction,
                    event_type=LogEventType.COMPENSATION_FAILED,
                    status=ExecutionStatus.ROLLBACK_FAILED,
                    step_id=step.step_id,
                    message=error_msg,
                    payload={
                        "error": raw_msg,
                        "step_index": step.step_index,
                        "tool_name": step.tool_name,
                        "compensation_tool": step.compensation_tool,
                    },
                )

                transaction.transition_to(ExecutionStatus.ROLLBACK_FAILED)
                transaction.world_snapshot_after = (
                    self.tool_executor.world.snapshot()
                )

                self._record_event(
                    transaction,
                    event_type=LogEventType.ROLLBACK_FAILED,
                    status=ExecutionStatus.ROLLBACK_FAILED,
                    step_id=step.step_id,
                    message=raw_msg,
                    payload={"alert": alert.model_dump()},
                )
                self.durable_log.save_transaction(transaction)
                return transaction

            # Compensation succeeded for this step
            step.status = ExecutionStatus.COMPENSATED
            step.compensation_outputs = comp_output
            step.compensated_at = utc_now_iso()
            transaction.compensation_history.append(
                CompensationRecord(
                    step_id=step.step_id,
                    step_index=step.step_index,
                    tool_name=step.tool_name,
                    compensation_tool=step.compensation_tool,
                    status=ExecutionStatus.COMPENSATED,
                    inputs=step.inputs,
                    outputs=comp_output,
                )
            )
            self._record_event(
                transaction,
                event_type=LogEventType.COMPENSATION_COMPLETED,
                status=ExecutionStatus.COMPENSATED,
                step_id=step.step_id,
                message=f"Compensated step {step.step_index} ({step.tool_name}) via {comp_tool_name}",
                payload={
                    "step_index": step.step_index,
                    "tool_name": step.tool_name,
                    "compensation_tool": step.compensation_tool,
                    "compensation_outputs": comp_output,
                },
            )
            self.durable_log.save_transaction(transaction)

        # Check if any irreversible action had already executed and could not be undone
        if len(transaction.irreversible_side_effects) > 0:
            transaction.transition_to(ExecutionStatus.PARTIAL_ROLLBACK)
            transaction.world_snapshot_after = (
                self.tool_executor.world.snapshot()
            )
            transaction.world_restored = False
            transaction.restoration_status = "PARTIAL_ROLLBACK"

            irrev_step = transaction.irreversible_side_effects[0]
            partial_alert = RollbackAlert(
                type="PARTIAL_ROLLBACK",
                transaction_id=transaction.transaction_id,
                failed_compensation_step=str(irrev_step["step_id"]),
                message=(
                    f"Partial rollback: irreversible side effect '{irrev_step['tool']}' "
                    f"(step_id={irrev_step['step_id']}) cannot be undone."
                ),
                requires_manual_intervention=True,
            )
            transaction.alerts.append(partial_alert)

            self._record_event(
                transaction,
                event_type=LogEventType.PARTIAL_ROLLBACK,
                status=ExecutionStatus.PARTIAL_ROLLBACK,
                step_id=str(irrev_step["step_id"]),
                message=partial_alert.message,
                payload={
                    "restoration_status": "PARTIAL_ROLLBACK",
                    "world_restored": False,
                    "irreversible_side_effects": transaction.irreversible_side_effects,
                    "compensated_step_ids": [
                        rec.step_id for rec in transaction.compensation_history
                    ],
                },
            )
            self.durable_log.save_transaction(transaction)
            return transaction

        transaction.transition_to(final_status_if_clean)
        transaction.world_snapshot_after = self.tool_executor.world.snapshot()
        transaction.world_restored = (
            transaction.world_snapshot_after == transaction.world_snapshot_before
        )
        transaction.restoration_status = (
            "FULLY_RESTORED" if transaction.world_restored else "PARTIAL_ROLLBACK"
        )
        self._record_event(
            transaction,
            event_type=LogEventType.TRANSACTION_COMPENSATED,
            status=transaction.status,
            message="Transaction compensated in reverse order",
            payload={
                "compensated_step_ids": [
                    rec.step_id for rec in transaction.compensation_history
                ],
                "world_restored": transaction.world_restored,
                "restoration_status": transaction.restoration_status,
            },
        )
        self.durable_log.save_transaction(transaction)
        return transaction
