"""RecoveryManager for reconstructing and recovering crashed AG02 transactions from SQLite."""

from __future__ import annotations

from typing import Any, Literal, Optional

from app.core.compensation_manager import CompensationManager
from app.core.durable_log import DurableExecutionLog
from app.core.models import (
    ExecutionStatus,
    LogEventType,
    TransactionState,
    utc_now_iso,
)

RecoveryStrategy = Literal["rollback", "resume"]


class RecoveryManager:
    """
    Recovers interrupted or crashed transactions from the SQLite DurableExecutionLog.

    Supports two recovery strategies:
    - `"rollback"`: Compensate only side effects known to have completed (in reverse order),
      never compensating uncompleted steps and never repeating already-succeeded compensations.
    - `"resume"`: Resume forward execution from the first uncompleted step, never repeating
      any side effect that was already committed before the crash.
    """

    def __init__(
        self,
        durable_log: DurableExecutionLog,
        compensation_manager: CompensationManager,
    ) -> None:
        self.durable_log = durable_log
        self.compensation_manager = compensation_manager

    def find_recoverable_transactions(self) -> list[TransactionState]:
        """Return all transactions in non-terminal or failed states eligible for recovery."""
        return self.durable_log.get_incomplete_transactions()

    def inspect_transaction(self, transaction_id: str) -> dict[str, Any]:
        """
        Inspect SQLite state for `transaction_id` and determine:
        - which steps completed
        - which steps did not complete
        - which compensations already happened
        """
        tx = self.durable_log.load_transaction(transaction_id)
        events = self.durable_log.get_events(transaction_id=transaction_id)

        completed_step_ids_from_events = {
            e.step_id
            for e in events
            if e.event_type == LogEventType.STEP_COMPLETED and e.step_id
        }
        compensated_step_ids_from_events = {
            e.step_id
            for e in events
            if e.event_type
            in {LogEventType.COMPENSATION_COMPLETED, LogEventType.STEP_COMPENSATED}
            and e.step_id
        }
        compensated_step_ids_from_history = {
            rec.step_id
            for rec in tx.compensation_history
            if rec.status == ExecutionStatus.COMPENSATED
        }
        already_compensated = (
            compensated_step_ids_from_events
            | compensated_step_ids_from_history
            | {
                s.step_id
                for s in tx.steps
                if s.status == ExecutionStatus.COMPENSATED
            }
        )

        completed_steps: list[str] = []
        uncompleted_steps: list[str] = []
        compensated_steps: list[str] = []

        for step in tx.steps:
            if step.step_id in already_compensated:
                compensated_steps.append(step.step_id)
                completed_steps.append(step.step_id)
            elif (
                step.status
                in {ExecutionStatus.COMPLETED, ExecutionStatus.COMPENSATING}
                or step.step_id in completed_step_ids_from_events
            ):
                completed_steps.append(step.step_id)
            else:
                uncompleted_steps.append(step.step_id)

        return {
            "transaction_id": tx.transaction_id,
            "status": tx.status.value,
            "completed_steps": completed_steps,
            "uncompleted_steps": uncompleted_steps,
            "compensated_steps": compensated_steps,
        }

    def recover_transaction(
        self,
        transaction_id: str,
        strategy: RecoveryStrategy | str = "rollback",
    ) -> TransactionState:
        """
        Recover an interrupted or failed transaction from SQLite using `strategy`:
        - `"rollback"`: compensate completed steps in reverse order without repeating compensations
        - `"resume"`: continue forward execution without repeating already-committed side effects
        """
        normalized_strategy = strategy.strip().lower()
        if normalized_strategy not in {"rollback", "resume"}:
            raise ValueError(
                f"Invalid recovery strategy '{strategy}'; expected 'resume' or 'rollback'"
            )

        tx = self.durable_log.load_transaction(transaction_id)
        inspection = self.inspect_transaction(transaction_id)
        already_compensated_ids = set(inspection["compensated_steps"])
        completed_step_ids = set(inspection["completed_steps"])

        if (
            normalized_strategy == "rollback"
            and tx.status == ExecutionStatus.COMPENSATED
        ):
            return tx
        if (
            normalized_strategy == "resume"
            and tx.status == ExecutionStatus.COMPLETED
        ):
            return tx

        previous_status = tx.status
        tx.transition_to(ExecutionStatus.RECOVERING)
        start_entry = self.durable_log.save_event(
            transaction_id=tx.transaction_id,
            event_type=LogEventType.RECOVERY_STARTED,
            status=ExecutionStatus.RECOVERING,
            message=f"Starting '{normalized_strategy}' recovery from status {previous_status.value}",
            payload={
                "strategy": normalized_strategy,
                "previous_status": previous_status.value,
                "inspection": inspection,
            },
        )
        tx.events.append(start_entry)

        # Reconcile step statuses with durable log events
        for step in tx.steps:
            if step.step_id in already_compensated_ids:
                step.status = ExecutionStatus.COMPENSATED
            elif step.step_id in completed_step_ids:
                step.status = ExecutionStatus.COMPLETED
                step.compensation_error = None

        if normalized_strategy == "resume":
            return self._recover_by_resuming(tx, completed_step_ids)

        return self._recover_by_rolling_back(tx, previous_status)

    def _recover_by_rolling_back(
        self,
        tx: TransactionState,
        previous_status: ExecutionStatus,
    ) -> TransactionState:
        """Compensate only completed, uncompensated steps in reverse order."""
        for step in tx.steps:
            if step.status == ExecutionStatus.RUNNING:
                # A step still in RUNNING without a STEP_COMPLETED record did not finish
                step.status = ExecutionStatus.FAILED
                step.error = "Interrupted during execution prior to completion"

        self.durable_log.save_transaction(tx)

        steps_to_undo = self.compensation_manager.get_steps_to_compensate(tx)
        if not steps_to_undo:
            tx.transition_to(ExecutionStatus.COMPENSATED)
            tx.world_snapshot_after = (
                self.compensation_manager.tool_executor.world.snapshot()
            )
            tx.world_restored = (
                tx.world_snapshot_after == tx.world_snapshot_before
            )
            comp_evt = self.durable_log.save_event(
                transaction_id=tx.transaction_id,
                event_type=LogEventType.TRANSACTION_COMPENSATED,
                status=tx.status,
                message="Transaction compensated during recovery (no remaining steps to undo)",
                payload={"world_restored": tx.world_restored},
            )
            tx.events.append(comp_evt)
            done_evt = self.durable_log.save_event(
                transaction_id=tx.transaction_id,
                event_type=LogEventType.RECOVERY_COMPLETED,
                status=tx.status,
                message="Recovery completed with no remaining side-effecting steps to undo",
                payload={"strategy": "rollback"},
            )
            tx.events.append(done_evt)
            self.durable_log.save_transaction(tx)
            return tx

        recovered_tx = self.compensation_manager.compensate_transaction(
            tx,
            reason=f"RecoveryManager rollback from interrupted state ({previous_status.value})",
        )
        done_evt = self.durable_log.save_event(
            transaction_id=recovered_tx.transaction_id,
            event_type=LogEventType.RECOVERY_COMPLETED,
            status=recovered_tx.status,
            message=f"Rollback recovery finished with status {recovered_tx.status.value}",
            payload={"strategy": "rollback"},
        )
        recovered_tx.events.append(done_evt)
        self.durable_log.save_transaction(recovered_tx)
        return recovered_tx

    def _recover_by_resuming(
        self,
        tx: TransactionState,
        completed_step_ids: set[str],
    ) -> TransactionState:
        """Resume forward execution from the first uncompleted step without repeating committed steps."""
        tx.transition_to(ExecutionStatus.RUNNING)
        tx.error = None
        self.durable_log.save_transaction(tx)

        executor = self.compensation_manager.tool_executor

        for step in tx.steps:
            # NEVER repeat a side effect that was already successfully committed before the crash
            if (
                step.status == ExecutionStatus.COMPLETED
                or step.step_id in completed_step_ids
            ):
                step.status = ExecutionStatus.COMPLETED
                continue

            step.status = ExecutionStatus.RUNNING
            step.attempt_count += 1
            step.started_at = utc_now_iso()
            step.error = None

            start_evt = self.durable_log.save_event(
                transaction_id=tx.transaction_id,
                step_id=step.step_id,
                step_number=step.step_number,
                tool_name=step.tool_name,
                arguments=step.arguments,
                event_type=LogEventType.STEP_STARTED,
                status=ExecutionStatus.RUNNING,
                message=f"Resuming step {step.step_index} ({step.tool_name})",
                payload={
                    "step_index": step.step_index,
                    "tool_name": step.tool_name,
                    "arguments": step.arguments,
                    "recovered_resume": True,
                },
            )
            tx.events.append(start_evt)
            self.durable_log.save_transaction(tx)

            try:
                outputs = executor.execute(
                    step.tool_name,
                    step.inputs,
                    transaction_id=tx.transaction_id,
                    step_index=step.step_index,
                    step_id=step.step_id,
                )
            except Exception as exc:
                step.status = ExecutionStatus.FAILED
                step.error = str(exc)
                step.completed_at = utc_now_iso()
                failure_msg = f"Step {step.step_index} ({step.tool_name}) failed during resume: {exc}"
                tx.error = failure_msg
                tx.transition_to(ExecutionStatus.COMPENSATING)

                fail_evt = self.durable_log.save_event(
                    transaction_id=tx.transaction_id,
                    step_id=step.step_id,
                    step_number=step.step_number,
                    tool_name=step.tool_name,
                    event_type=LogEventType.STEP_FAILED,
                    status=ExecutionStatus.FAILED,
                    message=failure_msg,
                    payload={
                        "step_index": step.step_index,
                        "tool_name": step.tool_name,
                        "error": str(exc),
                    },
                )
                tx.events.append(fail_evt)
                self.durable_log.save_transaction(tx)

                rolled_back = self.compensation_manager.compensate_transaction(
                    tx, reason=failure_msg
                )
                done_evt = self.durable_log.save_event(
                    transaction_id=rolled_back.transaction_id,
                    event_type=LogEventType.RECOVERY_COMPLETED,
                    status=rolled_back.status,
                    message=f"Resume failed and rolled back to {rolled_back.status.value}",
                    payload={"strategy": "resume"},
                )
                rolled_back.events.append(done_evt)
                self.durable_log.save_transaction(rolled_back)
                return rolled_back

            step.outputs = outputs
            step.status = ExecutionStatus.COMPLETED
            step.completed_at = utc_now_iso()
            comp_evt = self.durable_log.save_event(
                transaction_id=tx.transaction_id,
                step_id=step.step_id,
                step_number=step.step_number,
                tool_name=step.tool_name,
                result_metadata=outputs,
                event_type=LogEventType.STEP_COMPLETED,
                status=ExecutionStatus.COMPLETED,
                message=f"Step {step.step_index} ({step.tool_name}) completed during resume",
                payload={
                    "step_index": step.step_index,
                    "tool_name": step.tool_name,
                    "outputs": outputs,
                },
            )
            tx.events.append(comp_evt)
            self.durable_log.save_transaction(tx)

        tx.transition_to(ExecutionStatus.COMPLETED)
        tx.world_snapshot_after = executor.world.snapshot()
        tx_done_evt = self.durable_log.save_event(
            transaction_id=tx.transaction_id,
            event_type=LogEventType.TRANSACTION_COMPLETED,
            status=ExecutionStatus.COMPLETED,
            message="All workflow steps completed after resume recovery",
        )
        tx.events.append(tx_done_evt)

        rec_done_evt = self.durable_log.save_event(
            transaction_id=tx.transaction_id,
            event_type=LogEventType.RECOVERY_COMPLETED,
            status=ExecutionStatus.COMPLETED,
            message="Resume recovery finished with status COMPLETED",
            payload={"strategy": "resume"},
        )
        tx.events.append(rec_done_evt)
        self.durable_log.save_transaction(tx)
        return tx

    def recover_all(
        self, strategy: RecoveryStrategy | str = "rollback"
    ) -> list[TransactionState]:
        """Recover all incomplete transactions found in the SQLite DurableExecutionLog."""
        recoverable = self.find_recoverable_transactions()
        results: list[TransactionState] = []
        for tx in recoverable:
            results.append(
                self.recover_transaction(tx.transaction_id, strategy=strategy)
            )
        return results
