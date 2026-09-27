"""Comprehensive Phase 2 unit tests for the AG02 Saga Transaction Engine."""

from __future__ import annotations

import unittest

from app.core import (
    ApprovalManager,
    CompensationManager,
    DurableExecutionLog,
    ExecutionStatus,
    FaultInjector,
    LogEventType,
    MockWorld,
    ToolExecutor,
    TransactionManager,
    WorkflowPlan,
    WorkflowStepSpec,
    create_default_tool_registry,
)


class TestPhase2SagaTransactionEngine(unittest.TestCase):
    """Test suite for all Phase 2 Saga TransactionManager requirements."""

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

    def _build_three_step_plan(self) -> WorkflowPlan:
        """
        Build the canonical Phase 2 3-step plan:
        Step 1: create_booking (compensation: cancel_booking)
        Step 2: charge_payment (compensation: refund_payment)
        Step 3: create_ticket (compensation: delete_ticket)
        """
        return WorkflowPlan(
            goal="Book flight, charge payment, and issue ticket",
            steps=[
                WorkflowStepSpec(
                    step_id="step_booking_1",
                    tool_name="create_booking",
                    arguments={
                        "flight_id": "FL-101",
                        "passenger_name": "Alex Rivera",
                        "seats": 1,
                    },
                    compensation_available=True,
                    compensation_tool="cancel_booking",
                    irreversible=False,
                    approval_required=False,
                ),
                WorkflowStepSpec(
                    step_id="step_payment_2",
                    tool_name="charge_payment",
                    arguments={
                        "user_id": "user_default",
                        "amount": 450.0,
                        "description": "Booking payment",
                    },
                    compensation_available=True,
                    compensation_tool="refund_payment",
                    irreversible=False,
                    approval_required=False,
                ),
                WorkflowStepSpec(
                    step_id="step_ticket_3",
                    tool_name="create_ticket",
                    arguments={
                        "holder_name": "Alex Rivera",
                        "reference": "TKT-FL-101",
                    },
                    compensation_available=True,
                    compensation_tool="delete_ticket",
                    irreversible=False,
                    approval_required=False,
                ),
            ],
        )

    def test_step_attributes_match_phase2_schema(self) -> None:
        """Verify each step contains all required Phase 2 attributes."""
        plan = self._build_three_step_plan()
        tx = self.transaction_manager.create_transaction(plan)

        self.assertEqual(len(tx.steps), 3)
        s1, s2, s3 = tx.steps

        self.assertEqual(s1.step_id, "step_booking_1")
        self.assertEqual(s1.tool_name, "create_booking")
        self.assertEqual(s1.arguments["flight_id"], "FL-101")
        self.assertEqual(s1.execution_status, ExecutionStatus.PENDING)
        self.assertTrue(s1.compensation_available)
        self.assertEqual(s1.compensation_tool, "cancel_booking")
        self.assertFalse(s1.irreversible)
        self.assertFalse(s1.approval_required)

        self.assertEqual(s2.compensation_tool, "refund_payment")
        self.assertEqual(s3.compensation_tool, "delete_ticket")

    def test_1_all_steps_succeed(self) -> None:
        """Test 1: All steps succeed -> transaction status = COMPLETED and TRANSACTION_COMPLETED emitted."""
        plan = self._build_three_step_plan()
        tx = self.transaction_manager.execute_transaction(plan)

        self.assertEqual(tx.status, ExecutionStatus.COMPLETED)
        for step in tx.steps:
            self.assertEqual(step.execution_status, ExecutionStatus.COMPLETED)

        self.assertEqual(len(tx.compensation_history), 0)
        self.assertEqual(self.world.state["flights"]["FL-101"]["available_seats"], 9)
        self.assertEqual(
            self.world.state["accounts"]["user_default"]["balance"], 4550.00
        )
        self.assertEqual(len(self.world.state["tickets"]), 1)

        event_types = [e.event_type for e in tx.events]
        self.assertIn(LogEventType.STEP_STARTED, event_types)
        self.assertIn(LogEventType.STEP_COMPLETED, event_types)
        self.assertIn(LogEventType.TRANSACTION_COMPLETED, event_types)

    def test_2_first_step_fails(self) -> None:
        """Test 2: First step fails -> never compensate Step 1; Step 2 and Step 3 remain PENDING."""
        self.fault_injector.fail_step(
            step_index=0, error_message="Booking service unavailable"
        )
        plan = self._build_three_step_plan()
        tx = self.transaction_manager.execute_transaction(plan)

        self.assertEqual(tx.status, ExecutionStatus.COMPENSATED)
        self.assertEqual(tx.steps[0].execution_status, ExecutionStatus.FAILED)
        self.assertEqual(tx.steps[1].execution_status, ExecutionStatus.PENDING)
        self.assertEqual(tx.steps[2].execution_status, ExecutionStatus.PENDING)

        # Step 1 failed, so 0 compensations should have run
        self.assertEqual(len(tx.compensation_history), 0)
        self.assertTrue(tx.world_restored)
        self.assertEqual(self.world.snapshot(), self.initial_snapshot)

        event_types = [e.event_type for e in tx.events]
        self.assertIn(LogEventType.STEP_STARTED, event_types)
        self.assertIn(LogEventType.STEP_FAILED, event_types)
        self.assertIn(LogEventType.TRANSACTION_COMPENSATED, event_types)

    def test_3_second_step_fails(self) -> None:
        """Test 3: Second step fails -> only Step 1 is compensated; Step 2 and Step 3 are never compensated."""
        self.fault_injector.fail_step(
            step_index=1, error_message="Payment declined on Step 2"
        )
        plan = self._build_three_step_plan()
        tx = self.transaction_manager.execute_transaction(plan)

        self.assertEqual(tx.status, ExecutionStatus.COMPENSATED)
        self.assertEqual(tx.steps[0].execution_status, ExecutionStatus.COMPENSATED)
        self.assertEqual(tx.steps[1].execution_status, ExecutionStatus.FAILED)
        self.assertEqual(tx.steps[2].execution_status, ExecutionStatus.PENDING)

        self.assertEqual(len(tx.compensation_history), 1)
        self.assertEqual(tx.compensation_history[0].step_id, "step_booking_1")
        self.assertEqual(
            tx.compensation_history[0].compensation_tool, "cancel_booking"
        )
        self.assertTrue(tx.world_restored)
        self.assertEqual(self.world.snapshot(), self.initial_snapshot)

    def test_4_third_step_fails_and_5_compensation_succeeds(self) -> None:
        """
        Test 4 & 5:
        Step 1 (create_booking) -> SUCCESS
        Step 2 (charge_payment) -> SUCCESS
        Step 3 (create_ticket) -> FAILURE
        Automatically compensates Step 2 (refund_payment) then Step 1 (cancel_booking).
        Never compensates Step 3.
        """
        self.fault_injector.fail_step(
            step_index=2, error_message="Ticket service outage on Step 3"
        )
        plan = self._build_three_step_plan()
        tx = self.transaction_manager.execute_transaction(plan)

        self.assertEqual(tx.status, ExecutionStatus.COMPENSATED)
        self.assertTrue(tx.world_restored)
        self.assertEqual(tx.steps[0].execution_status, ExecutionStatus.COMPENSATED)
        self.assertEqual(tx.steps[1].execution_status, ExecutionStatus.COMPENSATED)
        self.assertEqual(tx.steps[2].execution_status, ExecutionStatus.FAILED)

        # Verify Step 3 was never compensated, and Step 2 -> Step 1 were compensated
        self.assertEqual(len(tx.compensation_history), 2)
        self.assertEqual(tx.compensation_history[0].step_id, "step_payment_2")
        self.assertEqual(
            tx.compensation_history[0].compensation_tool, "refund_payment"
        )
        self.assertEqual(tx.compensation_history[1].step_id, "step_booking_1")
        self.assertEqual(
            tx.compensation_history[1].compensation_tool, "cancel_booking"
        )

        # Verify structured events
        event_types = [e.event_type for e in tx.events]
        self.assertIn(LogEventType.STEP_STARTED, event_types)
        self.assertIn(LogEventType.STEP_COMPLETED, event_types)
        self.assertIn(LogEventType.STEP_FAILED, event_types)
        self.assertIn(LogEventType.COMPENSATION_STARTED, event_types)
        self.assertIn(LogEventType.COMPENSATION_COMPLETED, event_types)
        self.assertIn(LogEventType.TRANSACTION_COMPENSATED, event_types)
        self.assertEqual(self.world.snapshot(), self.initial_snapshot)

    def test_6_compensation_fails_generates_structured_alert_and_never_claims_world_restored(
        self,
    ) -> None:
        """
        Test 6: Compensation fails -> transaction status = ROLLBACK_FAILED,
        generates structured alert, emits COMPENSATION_FAILED & ROLLBACK_FAILED,
        and never claims world_restored.
        """
        self.fault_injector.fail_step(
            step_index=2, error_message="Ticket creation failed"
        )
        self.fault_injector.fail_compensation(
            tool_name="refund_payment",
            error_message="Payment refund failed",
        )

        plan = self._build_three_step_plan()
        tx = self.transaction_manager.execute_transaction(plan)

        self.assertEqual(tx.status, ExecutionStatus.ROLLBACK_FAILED)
        # Safety rule: never claim the world was restored if compensation failed
        self.assertFalse(tx.world_restored)

        # Verify structured alert format matches specification
        self.assertEqual(len(tx.alerts), 1)
        alert_dict = tx.rollback_alert
        self.assertEqual(
            alert_dict,
            {
                "type": "ROLLBACK_FAILED",
                "transaction_id": tx.transaction_id,
                "failed_compensation_step": "step_payment_2",
                "message": "Payment refund failed",
                "requires_manual_intervention": True,
            },
        )

        # Verify structured events include COMPENSATION_FAILED and ROLLBACK_FAILED
        event_types = [e.event_type for e in tx.events]
        self.assertIn(LogEventType.COMPENSATION_STARTED, event_types)
        self.assertIn(LogEventType.COMPENSATION_FAILED, event_types)
        self.assertIn(LogEventType.ROLLBACK_FAILED, event_types)
        self.assertNotIn(LogEventType.TRANSACTION_COMPENSATED, event_types)

    def test_7_multiple_compensations_and_8_exact_reverse_execution_order(
        self,
    ) -> None:
        """
        Test 7 & 8: Execute a 5-step workflow where steps 1..4 succeed and step 5 fails.
        Verify all 4 completed steps are compensated in exact reverse order:
        Step 4 -> Step 3 -> Step 2 -> Step 1.
        """
        five_step_plan = WorkflowPlan(
            goal="Five-step enterprise travel & order workflow",
            steps=[
                WorkflowStepSpec(
                    step_id="step_1_flight",
                    tool_name="book_flight",
                    arguments={
                        "flight_id": "FL-101",
                        "passenger_name": "Alex Rivera",
                        "seats": 1,
                    },
                ),
                WorkflowStepSpec(
                    step_id="step_2_hotel",
                    tool_name="reserve_hotel",
                    arguments={
                        "hotel_id": "HT-GRAND",
                        "guest_name": "Alex Rivera",
                        "nights": 2,
                        "rooms": 1,
                    },
                ),
                WorkflowStepSpec(
                    step_id="step_3_inventory",
                    tool_name="reserve_inventory",
                    arguments={"sku": "ITEM-LAPTOP", "quantity": 1},
                ),
                WorkflowStepSpec(
                    step_id="step_4_calendar",
                    tool_name="create_calendar_event",
                    arguments={
                        "title": "NYC Summit",
                        "date": "2026-11-01",
                        "attendees": ["Alex Rivera"],
                    },
                ),
                WorkflowStepSpec(
                    step_id="step_5_payment",
                    tool_name="charge_payment",
                    arguments={
                        "user_id": "user_default",
                        "amount": 2450.0,
                        "description": "Full trip & equipment charge",
                    },
                ),
            ],
        )

        # Fail Step 5 (0-based step_index=4)
        self.fault_injector.fail_step(
            step_index=4, error_message="Card limit exceeded on Step 5"
        )

        tx = self.transaction_manager.execute_transaction(five_step_plan)

        self.assertEqual(tx.status, ExecutionStatus.COMPENSATED)
        self.assertTrue(tx.world_restored)
        self.assertEqual(self.world.snapshot(), self.initial_snapshot)

        # Forward execution order was: [0, 1, 2, 3]
        # Compensation order MUST be strictly reverse: [3, 2, 1, 0]
        comp_step_indices = [rec.step_index for rec in tx.compensation_history]
        self.assertEqual(comp_step_indices, [3, 2, 1, 0])

        comp_step_ids = [rec.step_id for rec in tx.compensation_history]
        self.assertEqual(
            comp_step_ids,
            [
                "step_4_calendar",
                "step_3_inventory",
                "step_2_hotel",
                "step_1_flight",
            ],
        )

        comp_tools = [rec.compensation_tool for rec in tx.compensation_history]
        self.assertEqual(
            comp_tools,
            [
                "delete_calendar_event",
                "release_inventory",
                "cancel_hotel",
                "cancel_flight",
            ],
        )

        # Also verify MockWorld operation history shows exact forward then reverse order
        world_ops = [
            (op["kind"], op["tool_name"]) for op in self.world.operation_history
        ]
        self.assertEqual(
            world_ops,
            [
                ("EXECUTE", "book_flight"),
                ("EXECUTE", "reserve_hotel"),
                ("EXECUTE", "reserve_inventory"),
                ("EXECUTE", "create_calendar_event"),
                ("COMPENSATE", "create_calendar_event"),
                ("COMPENSATE", "reserve_inventory"),
                ("COMPENSATE", "reserve_hotel"),
                ("COMPENSATE", "book_flight"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
