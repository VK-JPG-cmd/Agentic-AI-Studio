"""Unit tests for AG02 Saga orchestration, reverse-order compensation, approval gates, and recovery."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.core import (
    Agent,
    ApprovalManager,
    CompensationManager,
    DurableExecutionLog,
    ExecutionStatus,
    FaultInjector,
    LogEventType,
    MockWorld,
    RecoveryManager,
    ToolExecutor,
    TransactionManager,
    WorkflowPlan,
    WorkflowStepSpec,
    create_default_tool_registry,
)


class TestSagaTransactions(unittest.TestCase):
    """Test suite verifying AG02 transactional workflows and reverse-order undo."""

    def setUp(self) -> None:
        self.world = MockWorld()
        self.initial_snapshot = self.world.snapshot()
        self.registry = create_default_tool_registry()
        self.fault_injector = FaultInjector()
        self.durable_log = DurableExecutionLog()
        self.approval_manager = ApprovalManager()
        self.tool_executor = ToolExecutor(
            registry=self.registry,
            world=self.world,
            fault_injector=self.fault_injector,
        )
        self.compensation_manager = CompensationManager(
            tool_executor=self.tool_executor,
            durable_log=self.durable_log,
        )
        self.transaction_manager = TransactionManager(
            registry=self.registry,
            world=self.world,
            durable_log=self.durable_log,
            approval_manager=self.approval_manager,
            fault_injector=self.fault_injector,
            compensation_manager=self.compensation_manager,
            tool_executor=self.tool_executor,
        )
        self.recovery_manager = RecoveryManager(
            durable_log=self.durable_log,
            compensation_manager=self.compensation_manager,
        )
        self.agent = Agent(transaction_manager=self.transaction_manager)

    def test_step3_failure_triggers_reverse_order_compensation_and_restores_world(
        self,
    ) -> None:
        """
        Core AG02 Requirement:
        Step 1 (book_flight) -> succeeds
        Step 2 (reserve_hotel) -> succeeds
        Step 3 (charge_payment) -> fails
        Compensation must run in REVERSE order:
        Step 2 (reserve_hotel) -> compensate
        Step 1 (book_flight) -> compensate
        MockWorld must be fully restored.
        """
        # Inject deterministic failure on Step 3 (0-based step_index=2)
        self.fault_injector.fail_step(
            step_index=2, error_message="Payment gateway declined card on Step 3"
        )

        tx = self.agent.execute("Book travel package with flight, hotel, and payment")

        # Verify final transaction status is COMPENSATED
        self.assertEqual(tx.status, ExecutionStatus.COMPENSATED)
        self.assertIn("Payment gateway declined card on Step 3", tx.error or "")

        # Verify individual step statuses
        self.assertEqual(len(tx.steps), 3)
        self.assertEqual(tx.steps[0].tool_name, "book_flight")
        self.assertEqual(tx.steps[0].status, ExecutionStatus.COMPENSATED)
        self.assertEqual(tx.steps[1].tool_name, "reserve_hotel")
        self.assertEqual(tx.steps[1].status, ExecutionStatus.COMPENSATED)
        self.assertEqual(tx.steps[2].tool_name, "charge_payment")
        self.assertEqual(tx.steps[2].status, ExecutionStatus.FAILED)

        # Verify compensation happened in strict REVERSE order: Step 2 (index 1) then Step 1 (index 0)
        self.assertEqual(len(tx.compensation_history), 2)
        self.assertEqual(tx.compensation_history[0].step_index, 1)
        self.assertEqual(tx.compensation_history[0].tool_name, "reserve_hotel")
        self.assertEqual(tx.compensation_history[0].status, ExecutionStatus.COMPENSATED)

        self.assertEqual(tx.compensation_history[1].step_index, 0)
        self.assertEqual(tx.compensation_history[1].tool_name, "book_flight")
        self.assertEqual(tx.compensation_history[1].status, ExecutionStatus.COMPENSATED)

        # Verify MockWorld operation sequence
        ops = [
            (entry["kind"], entry["tool_name"])
            for entry in self.world.operation_history
        ]
        self.assertEqual(
            ops,
            [
                ("EXECUTE", "book_flight"),
                ("EXECUTE", "reserve_hotel"),
                ("COMPENSATE", "reserve_hotel"),
                ("COMPENSATE", "book_flight"),
            ],
        )

        # Verify final MockWorld is 100% restored to initial state
        self.assertEqual(self.world.snapshot(), self.initial_snapshot)
        self.assertEqual(tx.world_snapshot_after, self.initial_snapshot)

    def test_successful_multistep_workflow_and_manual_undo_button(self) -> None:
        """Verify happy path completion followed by manual Undo Button reversing all 3 steps."""
        tx = self.agent.execute("Book travel package")
        self.assertEqual(tx.status, ExecutionStatus.COMPLETED)
        self.assertEqual(self.world.state["flights"]["FL-101"]["available_seats"], 9)
        self.assertEqual(
            self.world.state["hotels"]["HT-GRAND"]["available_rooms"], 7
        )
        self.assertEqual(
            self.world.state["accounts"]["user_default"]["balance"], 3950.00
        )

        # Trigger "The Undo Button" on the completed transaction
        undone_tx = self.agent.undo(tx.transaction_id)
        self.assertEqual(undone_tx.status, ExecutionStatus.COMPENSATED)

        # All 3 steps must be compensated in reverse order: Step 3 (index 2) -> Step 2 (index 1) -> Step 1 (index 0)
        comp_indices = [c.step_index for c in undone_tx.compensation_history]
        self.assertEqual(comp_indices, [2, 1, 0])
        comp_tools = [c.tool_name for c in undone_tx.compensation_history]
        self.assertEqual(
            comp_tools, ["charge_payment", "reserve_hotel", "book_flight"]
        )

        # World must be completely restored
        self.assertEqual(self.world.snapshot(), self.initial_snapshot)

    def test_compensation_failure_sets_rollback_failed_status(self) -> None:
        """Verify that if a compensation action itself fails, the transaction transitions to ROLLBACK_FAILED."""
        self.fault_injector.fail_step(
            step_index=2, error_message="Step 3 payment failed"
        )
        self.fault_injector.fail_compensation(
            tool_name="book_flight",
            error_message="Airline cancellation API timeout during rollback",
        )

        tx = self.agent.execute("Book travel package")
        self.assertEqual(tx.status, ExecutionStatus.ROLLBACK_FAILED)
        # Step 2 (reserve_hotel) was compensated first, then Step 1 (book_flight) failed compensation
        self.assertEqual(tx.steps[1].status, ExecutionStatus.COMPENSATED)
        self.assertEqual(tx.steps[0].status, ExecutionStatus.ROLLBACK_FAILED)
        self.assertIn(
            "Airline cancellation API timeout", tx.steps[0].compensation_error or ""
        )

    def test_approval_gate_pause_and_approve_resumes_to_completion(self) -> None:
        """Verify WAITING_FOR_APPROVAL pauses execution and approving completes the workflow."""
        tx = self.agent.execute("Execute VIP wire transfer package")
        self.assertEqual(tx.status, ExecutionStatus.WAITING_FOR_APPROVAL)
        # Step 0 (book_flight) completed, Step 1 (high_value_wire_transfer) is waiting for approval
        self.assertEqual(tx.steps[0].status, ExecutionStatus.COMPLETED)
        self.assertEqual(tx.steps[1].status, ExecutionStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(tx.steps[2].status, ExecutionStatus.PENDING)
        self.assertIsNotNone(tx.steps[1].approval_id)

        resumed_tx = self.agent.approve(
            tx.steps[1].approval_id, reason="CFO approved wire transfer"
        )
        self.assertEqual(resumed_tx.status, ExecutionStatus.COMPLETED)
        for step in resumed_tx.steps:
            self.assertEqual(step.status, ExecutionStatus.COMPLETED)

    def test_approval_gate_rejection_compensates_previous_steps_and_aborts(
        self,
    ) -> None:
        """Verify rejecting an approval gate rolls back previously completed steps and transitions to ABORTED."""
        tx = self.agent.execute("Execute VIP wire transfer package")
        self.assertEqual(tx.status, ExecutionStatus.WAITING_FOR_APPROVAL)
        # Flight FL-202 seat was decremented by Step 0
        self.assertEqual(self.world.state["flights"]["FL-202"]["available_seats"], 4)

        rejected_tx = self.agent.reject(
            tx.steps[1].approval_id, reason="User rejected wire transfer"
        )
        self.assertEqual(rejected_tx.status, ExecutionStatus.ABORTED)
        self.assertEqual(rejected_tx.steps[0].status, ExecutionStatus.COMPENSATED)
        self.assertEqual(rejected_tx.steps[1].status, ExecutionStatus.ABORTED)
        self.assertEqual(rejected_tx.steps[2].status, ExecutionStatus.PENDING)
        # World is restored to initial snapshot
        self.assertEqual(self.world.snapshot(), self.initial_snapshot)

    def test_crash_recovery_manager_rolls_back_interrupted_transaction(self) -> None:
        """Verify RecoveryManager detects a crashed RUNNING transaction from DurableExecutionLog and compensates it."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_file = Path(tmp_dir) / "durable_log.json"
            disk_log = DurableExecutionLog(storage_path=log_file)
            comp_mgr = CompensationManager(
                tool_executor=self.tool_executor,
                durable_log=disk_log,
            )
            tm = TransactionManager(
                registry=self.registry,
                world=self.world,
                durable_log=disk_log,
                approval_manager=self.approval_manager,
                fault_injector=self.fault_injector,
                compensation_manager=comp_mgr,
                tool_executor=self.tool_executor,
            )

            # Simulate process crash right after Step 2 (index 1) completes
            self.fault_injector.simulate_crash_after_step(
                step_index=1, error_message="Power loss after hotel reservation"
            )

            plan = self.agent.create_plan("Book travel package")
            crashed_tx = tm.execute_plan(plan, raise_on_crash=False)

            # Transaction is left in RUNNING state with Step 0 and Step 1 COMPLETED and Step 2 PENDING
            self.assertEqual(crashed_tx.status, ExecutionStatus.RUNNING)
            self.assertEqual(crashed_tx.steps[0].status, ExecutionStatus.COMPLETED)
            self.assertEqual(crashed_tx.steps[1].status, ExecutionStatus.COMPLETED)
            self.assertEqual(crashed_tx.steps[2].status, ExecutionStatus.PENDING)
            self.assertNotEqual(self.world.snapshot(), self.initial_snapshot)

            # Simulate restart by loading DurableExecutionLog from disk
            reloaded_log = DurableExecutionLog(storage_path=log_file)
            reloaded_comp_mgr = CompensationManager(
                tool_executor=self.tool_executor,
                durable_log=reloaded_log,
            )
            recovery_mgr = RecoveryManager(
                durable_log=reloaded_log,
                compensation_manager=reloaded_comp_mgr,
            )

            incomplete = recovery_mgr.find_recoverable_transactions()
            self.assertEqual(len(incomplete), 1)
            self.assertEqual(incomplete[0].transaction_id, crashed_tx.transaction_id)

            recovered_list = recovery_mgr.recover_all()
            self.assertEqual(len(recovered_list), 1)
            recovered_tx = recovered_list[0]

            self.assertEqual(recovered_tx.status, ExecutionStatus.COMPENSATED)
            self.assertEqual(
                [c.step_index for c in recovered_tx.compensation_history], [1, 0]
            )
            self.assertEqual(self.world.snapshot(), self.initial_snapshot)

            events = reloaded_log.get_events(transaction_id=crashed_tx.transaction_id)
            event_types = [e.event_type for e in events]
            self.assertIn(LogEventType.RECOVERY_STARTED, event_types)
            self.assertIn(LogEventType.RECOVERY_COMPLETED, event_types)


if __name__ == "__main__":
    unittest.main()
