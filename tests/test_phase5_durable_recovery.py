"""Comprehensive Phase 5 unit tests for SQLite DurableExecutionLog and Crash RecoveryManager."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.core import (
    ApprovalManager,
    CompensationManager,
    DurableExecutionLog,
    ExecutionStatus,
    FaultInjector,
    FaultTargetPhase,
    LogEventType,
    MockWorld,
    RecoveryManager,
    ToolExecutor,
    TransactionManager,
    WorkflowPlan,
    WorkflowStepSpec,
    create_default_tool_registry,
)


class TestPhase5SQLiteDurableLogAndRecovery(unittest.TestCase):
    """Test suite for SQLite persistence, secret redaction, and crash recovery (resume & rollback)."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp_dir.name) / "ag02_durable.sqlite3"
        self.world = MockWorld()
        self.initial_world = self.world.snapshot()
        self.registry = create_default_tool_registry()
        self.fault_injector = FaultInjector()
        self.durable_log = DurableExecutionLog(storage_path=self.db_path)
        self.approval_manager = ApprovalManager()
        self.executor = ToolExecutor(
            registry=self.registry,
            world=self.world,
            fault_injector=self.fault_injector,
        )
        self.comp_manager = CompensationManager(
            tool_executor=self.executor,
            durable_log=self.durable_log,
        )
        self.tm = TransactionManager(
            registry=self.registry,
            world=self.world,
            durable_log=self.durable_log,
            approval_manager=self.approval_manager,
            fault_injector=self.fault_injector,
            compensation_manager=self.comp_manager,
            tool_executor=self.executor,
        )

    def tearDown(self) -> None:
        self._tmp_dir.cleanup()

    def _new_restarted_managers(
        self,
    ) -> tuple[DurableExecutionLog, CompensationManager, RecoveryManager]:
        """Simulate an application restart by creating fresh instances attached to the same SQLite DB."""
        reloaded_log = DurableExecutionLog(storage_path=self.db_path)
        reloaded_executor = ToolExecutor(
            registry=self.registry,
            world=self.world,
            fault_injector=self.fault_injector,
        )
        reloaded_comp = CompensationManager(
            tool_executor=reloaded_executor,
            durable_log=reloaded_log,
        )
        recovery_mgr = RecoveryManager(
            durable_log=reloaded_log,
            compensation_manager=reloaded_comp,
        )
        return reloaded_log, reloaded_comp, recovery_mgr

    def _build_four_step_plan(self) -> WorkflowPlan:
        return WorkflowPlan(
            goal="Book room, charge payment, create ticket, send notification",
            steps=[
                WorkflowStepSpec(
                    step_id="step_1_booking",
                    tool_name="create_booking",
                    arguments={"user": "alice", "room": "Room-501"},
                    compensation_tool="cancel_booking",
                ),
                WorkflowStepSpec(
                    step_id="step_2_payment",
                    tool_name="charge_payment",
                    arguments={"user": "user_default", "amount": 400.0},
                    compensation_tool="refund_payment",
                ),
                WorkflowStepSpec(
                    step_id="step_3_ticket",
                    tool_name="create_ticket",
                    arguments={"user": "alice", "issue": "Valet parking"},
                    compensation_tool="delete_ticket",
                ),
                WorkflowStepSpec(
                    step_id="step_4_notify",
                    tool_name="send_notification",
                    arguments={
                        "to": "alice@example.com",
                        "subject": "Welcome",
                        "body": "Itinerary confirmed",
                    },
                ),
            ],
        )

    def test_sqlite_persists_required_step_columns_and_save_load_methods(
        self,
    ) -> None:
        """
        Verify SQLite stores transaction_id, step_id, step_number, tool_name, status,
        arguments, result_metadata, compensation_tool, compensation_status, created_at, updated_at,
        and supports save_event(), load_transaction(), and get_incomplete_transactions().
        """
        plan = self._build_four_step_plan()
        tx = self.tm.execute_transaction(plan)
        self.assertEqual(tx.status, ExecutionStatus.COMPLETED)

        # Explicitly test save_event() and load_transaction() on a fresh DurableExecutionLog
        reloaded_log, _, _ = self._new_restarted_managers()
        custom_evt = reloaded_log.save_event(
            transaction_id=tx.transaction_id,
            step_id="step_1_booking",
            step_number=1,
            tool_name="create_booking",
            event_type=LogEventType.STEP_COMPLETED,
            status=ExecutionStatus.COMPLETED,
            arguments={"user": "alice", "room": "Room-501"},
            result_metadata={"booking_id": "booking_0001"},
            compensation_tool="cancel_booking",
            compensation_status="PENDING",
            message="Verified save_event persistence",
        )
        self.assertGreater(custom_evt.sequence, 0)

        loaded_tx = reloaded_log.load_transaction(tx.transaction_id)
        self.assertEqual(loaded_tx.transaction_id, tx.transaction_id)
        self.assertEqual(loaded_tx.status, ExecutionStatus.COMPLETED)

        step_rows = reloaded_log.get_step_records(tx.transaction_id)
        self.assertEqual(len(step_rows), 4)
        required_keys = {
            "transaction_id",
            "step_id",
            "step_number",
            "tool_name",
            "status",
            "arguments",
            "result_metadata",
            "compensation_tool",
            "compensation_status",
            "created_at",
            "updated_at",
        }
        for idx, row in enumerate(step_rows):
            self.assertTrue(required_keys.issubset(set(row.keys())))
            self.assertEqual(row["step_number"], idx + 1)
            self.assertEqual(row["status"], "COMPLETED")
            self.assertIsNotNone(row["created_at"])
            self.assertIsNotNone(row["updated_at"])

    def test_secrets_are_never_stored_in_plaintext_in_sqlite(self) -> None:
        """Verify secrets (api_key, password, card_number, cvv, token) are redacted before SQLite persistence."""
        raw_secret_api_key = "sk-live-SUPER-SECRET-99999"
        raw_secret_password = "MyPlaintextPassword!123"
        raw_card_number = "4111-2222-3333-4444"

        plan = WorkflowPlan(
            goal="Test secret redaction in SQLite",
            steps=[
                WorkflowStepSpec(
                    step_id="step_secret_test",
                    tool_name="charge_payment",
                    arguments={
                        "user": "user_default",
                        "amount": 100.0,
                        "api_key": raw_secret_api_key,
                        "password": raw_secret_password,
                        "card_number": raw_card_number,
                    },
                )
            ],
        )
        tx = self.tm.execute_transaction(plan)
        self.assertEqual(tx.status, ExecutionStatus.COMPLETED)

        # Inspect raw SQLite database contents directly
        conn = sqlite3.connect(str(self.db_path))
        try:
            dump_text = "\n".join(conn.iterdump())
        finally:
            conn.close()

        self.assertNotIn(raw_secret_api_key, dump_text)
        self.assertNotIn(raw_secret_password, dump_text)
        self.assertNotIn(raw_card_number, dump_text)
        self.assertIn("[REDACTED]", dump_text)

    def test_crash_after_step_1_rollback_and_resume(self) -> None:
        """Simulate crash after Step 1 and verify both rollback and resume strategies."""
        # --- Part A: Rollback after Step 1 crash ---
        self.fault_injector.simulate_crash_after_step(step_index=0)
        crashed_tx = self.tm.execute_transaction(
            self._build_four_step_plan(), raise_on_crash=False
        )
        self.assertEqual(crashed_tx.status, ExecutionStatus.RUNNING)
        self.assertFalse(self.world.is_clean())

        _, _, recovery_mgr = self._new_restarted_managers()
        inspection = recovery_mgr.inspect_transaction(crashed_tx.transaction_id)
        self.assertEqual(inspection["completed_steps"], ["step_1_booking"])
        self.assertEqual(
            inspection["uncompleted_steps"],
            ["step_2_payment", "step_3_ticket", "step_4_notify"],
        )

        rolled_back_tx = recovery_mgr.recover_transaction(
            crashed_tx.transaction_id, strategy="rollback"
        )
        self.assertEqual(rolled_back_tx.status, ExecutionStatus.COMPENSATED)
        self.assertEqual(
            [c.compensation_tool for c in rolled_back_tx.compensation_history],
            ["cancel_booking"],
        )
        self.assertTrue(self.world.is_clean())
        self.assertEqual(self.world.snapshot(), self.initial_world)

        # --- Part B: Resume after Step 1 crash (must NOT duplicate booking) ---
        self.world.reset()
        self.durable_log.clear()
        self.fault_injector.clear()
        self.fault_injector.simulate_crash_after_step(step_index=0)

        crashed_tx_2 = self.tm.execute_transaction(
            self._build_four_step_plan(), raise_on_crash=False
        )
        self.assertEqual(len(self.world.bookings), 1)

        _, _, recovery_mgr_2 = self._new_restarted_managers()
        resumed_tx = recovery_mgr_2.recover_transaction(
            crashed_tx_2.transaction_id, strategy="resume"
        )
        self.assertEqual(resumed_tx.status, ExecutionStatus.COMPLETED)
        # Verify create_booking was NOT executed a second time
        self.assertEqual(len(self.world.bookings), 1)
        self.assertEqual(len(self.world.payments), 1)
        self.assertEqual(len(self.world.tickets), 1)
        self.assertEqual(len(self.world.emails), 1)

    def test_crash_after_step_2_rollback_and_resume_without_double_charging(
        self,
    ) -> None:
        """
        Simulate:
        Step 1 (create_booking) -> SUCCESS
        Step 2 (charge_payment) -> SUCCESS
        CRASH
        Verify:
        1) Rollback compensates Step 2 then Step 1 in reverse order and restores world.
        2) Resume does NOT charge the customer again (balance deducted only once!).
        """
        # --- Part A: Rollback after Step 2 crash ---
        self.fault_injector.simulate_crash_after_step(step_index=1)
        crashed_tx = self.tm.execute_transaction(
            self._build_four_step_plan(), raise_on_crash=False
        )
        self.assertEqual(crashed_tx.status, ExecutionStatus.RUNNING)

        reloaded_log, _, recovery_mgr = self._new_restarted_managers()
        incomplete = reloaded_log.get_incomplete_transactions()
        self.assertEqual(len(incomplete), 1)

        inspection = recovery_mgr.inspect_transaction(crashed_tx.transaction_id)
        self.assertEqual(
            inspection["completed_steps"], ["step_1_booking", "step_2_payment"]
        )
        self.assertEqual(
            inspection["uncompleted_steps"], ["step_3_ticket", "step_4_notify"]
        )

        rolled_back_tx = recovery_mgr.recover_transaction(
            crashed_tx.transaction_id, strategy="rollback"
        )
        self.assertEqual(rolled_back_tx.status, ExecutionStatus.COMPENSATED)
        self.assertEqual(
            [c.compensation_tool for c in rolled_back_tx.compensation_history],
            ["refund_payment", "cancel_booking"],
        )
        self.assertTrue(self.world.is_clean())
        self.assertEqual(self.world.snapshot(), self.initial_world)

        # --- Part B: Resume after Step 2 crash (must NOT double-charge customer) ---
        self.world.reset()
        self.durable_log.clear()
        self.fault_injector.clear()
        self.fault_injector.simulate_crash_after_step(step_index=1)

        crashed_tx_2 = self.tm.execute_transaction(
            self._build_four_step_plan(), raise_on_crash=False
        )
        # Customer was charged 400.0 once before the crash (5000 - 400 = 4600)
        self.assertEqual(
            self.world.state["accounts"]["user_default"]["balance"], 4600.0
        )
        self.assertEqual(len(self.world.payments), 1)

        _, _, recovery_mgr_2 = self._new_restarted_managers()
        resumed_tx = recovery_mgr_2.recover_transaction(
            crashed_tx_2.transaction_id, strategy="resume"
        )
        self.assertEqual(resumed_tx.status, ExecutionStatus.COMPLETED)
        # Customer balance must still be 4600.0 (charged ONCE, never twice!)
        self.assertEqual(
            self.world.state["accounts"]["user_default"]["balance"], 4600.0
        )
        self.assertEqual(len(self.world.payments), 1)
        self.assertEqual(len(self.world.bookings), 1)
        self.assertEqual(len(self.world.tickets), 1)
        self.assertEqual(len(self.world.emails), 1)

        # Verify operation history shows charge_payment was only executed ONCE
        charge_ops = [
            op
            for op in self.world.operation_history
            if op["kind"] == "EXECUTE" and op["tool_name"] == "charge_payment"
        ]
        self.assertEqual(len(charge_ops), 1)

    def test_crash_after_step_3_rollback_and_resume(self) -> None:
        """Simulate crash after Step 3 and verify both rollback and resume strategies."""
        # --- Part A: Rollback after Step 3 crash ---
        self.fault_injector.simulate_crash_after_step(step_index=2)
        crashed_tx = self.tm.execute_transaction(
            self._build_four_step_plan(), raise_on_crash=False
        )
        self.assertEqual(crashed_tx.status, ExecutionStatus.RUNNING)

        _, _, recovery_mgr = self._new_restarted_managers()
        inspection = recovery_mgr.inspect_transaction(crashed_tx.transaction_id)
        self.assertEqual(
            inspection["completed_steps"],
            ["step_1_booking", "step_2_payment", "step_3_ticket"],
        )
        self.assertEqual(inspection["uncompleted_steps"], ["step_4_notify"])

        rolled_back_tx = recovery_mgr.recover_transaction(
            crashed_tx.transaction_id, strategy="rollback"
        )
        self.assertEqual(rolled_back_tx.status, ExecutionStatus.COMPENSATED)
        self.assertEqual(
            [c.compensation_tool for c in rolled_back_tx.compensation_history],
            ["delete_ticket", "refund_payment", "cancel_booking"],
        )
        self.assertTrue(self.world.is_clean())
        self.assertEqual(self.world.snapshot(), self.initial_world)

        # --- Part B: Resume after Step 3 crash ---
        self.world.reset()
        self.durable_log.clear()
        self.fault_injector.clear()
        self.fault_injector.simulate_crash_after_step(step_index=2)

        crashed_tx_2 = self.tm.execute_transaction(
            self._build_four_step_plan(), raise_on_crash=False
        )
        _, _, recovery_mgr_2 = self._new_restarted_managers()
        resumed_tx = recovery_mgr_2.recover_transaction(
            crashed_tx_2.transaction_id, strategy="resume"
        )
        self.assertEqual(resumed_tx.status, ExecutionStatus.COMPLETED)
        # Only 1 booking, 1 payment, 1 ticket, 1 email created
        self.assertEqual(len(self.world.bookings), 1)
        self.assertEqual(len(self.world.payments), 1)
        self.assertEqual(len(self.world.tickets), 1)
        self.assertEqual(len(self.world.emails), 1)

    def test_never_execute_compensation_twice_if_log_says_it_already_succeeded(
        self,
    ) -> None:
        """
        Simulate a crash mid-rollback:
        - Step 1 (create_booking) -> SUCCESS
        - Step 2 (charge_payment) -> SUCCESS
        - Step 3 (create_ticket) -> FAILS
        - Compensation of Step 2 (refund_payment) -> SUCCEEDS, then CRASH occurs!
        On restart, RecoveryManager must see that Step 2 was ALREADY compensated
        and only compensate Step 1 (cancel_booking), never refunding twice!
        """
        self.fault_injector.fail_step(
            step_index=2, error_message="Step 3 ticket failed"
        )
        # Crash right after Step 2 (step_index=1) finishes its compensation!
        self.fault_injector.add_rule(
            phase=FaultTargetPhase.CRASH_DURING_COMPENSATE,
            step_index=1,
            error_message="Crash right after refund_payment completed",
            fail_once=True,
        )

        tx = self.tm.execute_transaction(
            self._build_four_step_plan(), raise_on_crash=False
        )
        self.assertEqual(tx.status, ExecutionStatus.COMPENSATING)
        self.assertEqual(tx.steps[1].status, ExecutionStatus.COMPENSATED)
        self.assertEqual(tx.steps[0].status, ExecutionStatus.COMPLETED)

        _, _, recovery_mgr = self._new_restarted_managers()
        inspection = recovery_mgr.inspect_transaction(tx.transaction_id)
        self.assertEqual(inspection["compensated_steps"], ["step_2_payment"])

        recovered_tx = recovery_mgr.recover_transaction(
            tx.transaction_id, strategy="rollback"
        )
        self.assertEqual(recovered_tx.status, ExecutionStatus.COMPENSATED)
        self.assertTrue(self.world.is_clean())
        self.assertEqual(self.world.snapshot(), self.initial_world)

        # Verify refund_payment was only called ONCE (before crash), never a second time during recovery
        refund_ops = [
            op
            for op in self.world.operation_history
            if op["kind"] == "COMPENSATE" and op["tool_name"] == "charge_payment"
        ]
        self.assertEqual(len(refund_ops), 1)

    def test_mid_compensation_crash_does_not_repeat_completed_compensation(
        self,
    ) -> None:
        """Verify mid-compensation crash recovery from persisted SQLite state never repeats an already-succeeded compensation."""

        # Wait: CRASH_DURING_COMPENSATE in ToolExecutor happens AFTER tool.compensate_action(world) ran!
        # What if the crash happens right after Step 2 (step_index=1) is persisted as COMPENSATED?
        # Let's test persisting Step 2 as COMPENSATED and Step 1 as COMPENSATING in SQLite!
        plan = self._build_four_step_plan()
        tx = self.tm.create_transaction(plan)

        # Execute Step 1 and Step 2 on world and persist to SQLite
        out1 = self.executor.execute(
            "create_booking",
            tx.steps[0].inputs,
            transaction_id=tx.transaction_id,
            step_index=0,
            step_id=tx.steps[0].step_id,
        )
        tx.steps[0].outputs = out1
        tx.steps[0].status = ExecutionStatus.COMPLETED

        out2 = self.executor.execute(
            "charge_payment",
            tx.steps[1].inputs,
            transaction_id=tx.transaction_id,
            step_index=1,
            step_id=tx.steps[1].step_id,
        )
        tx.steps[1].outputs = out2
        tx.steps[1].status = ExecutionStatus.COMPLETED

        # Step 3 failed, and Step 2 (charge_payment) was already compensated before the crash!
        tx.steps[2].status = ExecutionStatus.FAILED
        comp2 = self.executor.compensate(
            "charge_payment",
            tx.steps[1].inputs,
            tx.steps[1].outputs,
            transaction_id=tx.transaction_id,
            step_index=1,
            step_id=tx.steps[1].step_id,
        )
        tx.steps[1].status = ExecutionStatus.COMPENSATED
        tx.steps[1].compensation_outputs = comp2
        tx.status = ExecutionStatus.COMPENSATING
        self.durable_log.save_event(
            transaction_id=tx.transaction_id,
            step_id=tx.steps[1].step_id,
            step_number=2,
            tool_name="charge_payment",
            event_type=LogEventType.COMPENSATION_COMPLETED,
            status=ExecutionStatus.COMPENSATED,
            message="Step 2 already compensated before crash",
        )
        self.durable_log.save_transaction(tx)

        # Clear any fault rules and simulate restart
        self.fault_injector.clear()
        _, _, recovery_mgr = self._new_restarted_managers()

        inspection = recovery_mgr.inspect_transaction(tx.transaction_id)
        self.assertIn("step_2_payment", inspection["compensated_steps"])

        recovered_tx = recovery_mgr.recover_transaction(
            tx.transaction_id, strategy="rollback"
        )
        self.assertEqual(recovered_tx.status, ExecutionStatus.COMPENSATED)
        self.assertTrue(self.world.is_clean())
        self.assertEqual(self.world.snapshot(), self.initial_world)

        # Verify refund_payment was only executed ONCE in total, never repeated during recovery!
        refund_ops = [
            op
            for op in self.world.operation_history
            if op["kind"] == "COMPENSATE" and op["tool_name"] == "charge_payment"
        ]
        self.assertEqual(len(refund_ops), 1)


if __name__ == "__main__":
    unittest.main()
