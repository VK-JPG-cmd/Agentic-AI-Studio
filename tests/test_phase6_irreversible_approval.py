"""Phase 6 unit tests for Irreversible Actions, Approval Gates, and PARTIAL_ROLLBACK semantics."""

from __future__ import annotations

import unittest

from app.core import (
    Agent,
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


class TestPhase6IrreversibleAndApproval(unittest.TestCase):
    """Verify safe handling of irreversible tools, explicit approval gates, and PARTIAL_ROLLBACK."""

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
        self.tm = TransactionManager(
            registry=self.registry,
            world=self.world,
            durable_log=self.durable_log,
            approval_manager=self.approval_manager,
            fault_injector=self.fault_injector,
            compensation_manager=self.compensation_manager,
            tool_executor=self.tool_executor,
        )
        self.agent = Agent(transaction_manager=self.tm)

    def test_send_email_is_irreversible_with_no_compensation_function(self) -> None:
        """Verify send_email has reversible=False and no compensation function."""
        email_tool = self.registry.get("send_email")
        self.assertFalse(email_tool.is_reversible)
        self.assertFalse(email_tool.compensation_available)
        self.assertIsNone(email_tool.compensation_tool)
        self.assertIsNone(email_tool.compensate_action)

    def test_plan_optimization_places_irreversible_steps_last(self) -> None:
        """
        Rules 1 & 4:
        Irreversible steps should be placed LAST whenever possible.
        If a workflow contains create_booking, charge_payment, send_email (even if unordered),
        reversible actions are placed first and the irreversible action is placed last.
        """
        unordered_steps = [
            WorkflowStepSpec(
                step_id="step-email",
                tool_name="send_email",
                arguments={
                    "to": "alex@example.com",
                    "subject": "Booking Confirmation",
                    "body": "Your booking is confirmed.",
                },
            ),
            WorkflowStepSpec(
                step_id="step-booking",
                tool_name="create_booking",
                arguments={"user": "Alex Rivera", "room": "Room-101"},
            ),
            WorkflowStepSpec(
                step_id="step-payment",
                tool_name="charge_payment",
                arguments={"user": "Alex Rivera", "amount": 250.0},
            ),
        ]

        optimized_plan = self.agent.create_plan(
            goal="Book room, charge payment, and send email",
            steps=unordered_steps,
            reorder_irreversible_last=True,
        )
        self.assertEqual(
            [s.tool_name for s in optimized_plan.steps],
            ["create_booking", "charge_payment", "send_email"],
        )

    def test_irreversible_action_at_end_pauses_with_approval_request(self) -> None:
        """
        Test 1: Irreversible action at end.
        Workflow: create_booking -> charge_payment -> send_email
        Reversible actions execute first; when send_email is reached:
        status = WAITING_FOR_APPROVAL and structured APPROVAL_REQUIRED payload is returned.
        """
        plan = [
            {
                "step_id": "step_booking_1",
                "tool_name": "create_booking",
                "arguments": {"user": "Alex Rivera", "room": "Suite-301"},
            },
            {
                "step_id": "step_payment_2",
                "tool_name": "charge_payment",
                "arguments": {"user": "Alex Rivera", "amount": 320.0},
            },
            {
                "step_id": "step_email_3",
                "tool_name": "send_email",
                "arguments": {
                    "to": "alex@example.com",
                    "subject": "Suite-301 Confirmed",
                    "body": "Enjoy your stay!",
                },
            },
        ]

        tx = self.tm.execute_transaction(plan)

        # Verify transaction paused at WAITING_FOR_APPROVAL on step 3
        self.assertEqual(tx.status, ExecutionStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(tx.steps[0].status, ExecutionStatus.COMPLETED)
        self.assertEqual(tx.steps[1].status, ExecutionStatus.COMPLETED)
        self.assertEqual(tx.steps[2].status, ExecutionStatus.WAITING_FOR_APPROVAL)

        # Verify reversible side effects happened, but send_email has NOT executed yet
        self.assertEqual(len(self.world.state["bookings"]), 1)
        self.assertEqual(len(self.world.state["payments"]), 1)
        self.assertEqual(len(self.world.state["emails"]), 0)

        # Verify structured approval request matches specification
        self.assertEqual(
            tx.approval_request,
            {
                "type": "APPROVAL_REQUIRED",
                "step_id": "step_email_3",
                "tool": "send_email",
                "reason": "This action cannot be undone.",
            },
        )

    def test_irreversible_action_in_middle_requires_explicit_approval(self) -> None:
        """
        Test 2: Irreversible action in middle.
        Rule 2: If an irreversible step appears before other side-effecting steps,
        require explicit user approval before execution (even if requires_approval=False on the tool).
        """
        plan = [
            {
                "step_id": "step_booking_1",
                "tool_name": "create_booking",
                "arguments": {"user": "Alex Rivera", "room": "Suite-302"},
            },
            {
                "step_id": "step_irreversible_mid",
                "tool_name": "send_notification",  # irreversible tool with default requires_approval=False
                "arguments": {
                    "recipient": "alex@example.com",
                    "message": "Reservation in progress",
                },
            },
            {
                "step_id": "step_payment_3",
                "tool_name": "charge_payment",
                "arguments": {"user": "Alex Rivera", "amount": 400.0},
            },
        ]

        tx = self.tm.execute_transaction(plan)

        # Because step 2 is irreversible and appears before step 3, it MUST require approval
        self.assertTrue(tx.steps[1].irreversible)
        self.assertTrue(tx.steps[1].requires_approval)
        self.assertEqual(tx.status, ExecutionStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(tx.steps[0].status, ExecutionStatus.COMPLETED)
        self.assertEqual(tx.steps[1].status, ExecutionStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(tx.steps[2].status, ExecutionStatus.PENDING)
        self.assertEqual(
            tx.approval_request,
            {
                "type": "APPROVAL_REQUIRED",
                "step_id": "step_booking_1" if False else "step_irreversible_mid",
                "tool": "send_notification",
                "reason": "This action cannot be undone.",
            },
        )

    def test_approval_granted_executes_irreversible_action_and_completes(self) -> None:
        """
        Test 3: Approval granted.
        Only execute send_email after explicit user approval, then complete transaction.
        """
        plan = [
            {
                "step_id": "step_booking_1",
                "tool_name": "create_booking",
                "arguments": {"user": "Alex Rivera", "room": "Suite-303"},
            },
            {
                "step_id": "step_payment_2",
                "tool_name": "charge_payment",
                "arguments": {"user": "Alex Rivera", "amount": 500.0},
            },
            {
                "step_id": "step_email_3",
                "tool_name": "send_email",
                "arguments": {
                    "to": "alex@example.com",
                    "subject": "Booking Confirmed",
                    "body": "Suite-303 is ready.",
                },
            },
        ]

        tx = self.tm.execute_transaction(plan)
        self.assertEqual(tx.status, ExecutionStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(len(self.world.state["emails"]), 0)

        approval_id = tx.steps[2].approval_id
        self.assertIsNotNone(approval_id)

        completed_tx = self.tm.resolve_approval(
            approval_id,
            approved=True,
            reason="User confirmed irreversible email dispatch",
        )

        self.assertEqual(completed_tx.status, ExecutionStatus.COMPLETED)
        self.assertIsNone(completed_tx.approval_request)
        self.assertEqual(len(self.world.state["bookings"]), 1)
        self.assertEqual(len(self.world.state["payments"]), 1)
        self.assertEqual(len(self.world.state["emails"]), 1)
        self.assertEqual(self.world.state["emails"][0]["to"], "alex@example.com")

    def test_approval_denied_rolls_back_reversible_steps_and_restores_world(self) -> None:
        """
        Test 4: Approval denied.
        Denying approval for send_email aborts step 3, compensates step 2 and step 1 in reverse order,
        never sends the email, and restores MockWorld cleanly.
        """
        plan = [
            {
                "step_id": "step_booking_1",
                "tool_name": "create_booking",
                "arguments": {"user": "Alex Rivera", "room": "Suite-304"},
            },
            {
                "step_id": "step_payment_2",
                "tool_name": "charge_payment",
                "arguments": {"user": "Alex Rivera", "amount": 500.0},
            },
            {
                "step_id": "step_email_3",
                "tool_name": "send_email",
                "arguments": {
                    "to": "alex@example.com",
                    "subject": "Booking Confirmed",
                    "body": "Suite-304 is ready.",
                },
            },
        ]

        tx = self.tm.execute_transaction(plan)
        self.assertEqual(tx.status, ExecutionStatus.WAITING_FOR_APPROVAL)

        approval_id = tx.steps[2].approval_id
        self.assertIsNotNone(approval_id)

        aborted_tx = self.tm.resolve_approval(
            approval_id,
            approved=False,
            reason="User denied sending confirmation email",
        )

        self.assertEqual(aborted_tx.status, ExecutionStatus.ABORTED)
        self.assertEqual(aborted_tx.steps[0].status, ExecutionStatus.COMPENSATED)
        self.assertEqual(aborted_tx.steps[1].status, ExecutionStatus.COMPENSATED)
        self.assertEqual(aborted_tx.steps[2].status, ExecutionStatus.ABORTED)

        # Compensation order: refund_payment (step 2) -> cancel_booking (step 1)
        self.assertEqual(
            [c.compensation_tool for c in aborted_tx.compensation_history],
            ["refund_payment", "cancel_booking"],
        )

        # Email was never sent and world is fully restored
        self.assertEqual(len(self.world.state["emails"]), 0)
        self.assertTrue(aborted_tx.world_restored)
        self.assertEqual(aborted_tx.restoration_status, "FULLY_RESTORED")
        self.assertTrue(self.world.is_clean())
        self.assertEqual(self.world.snapshot(), self.initial_snapshot)

    def test_failure_before_irreversible_action_fully_restores_world(self) -> None:
        """
        Test 5: Failure before irreversible action.
        In create_booking -> charge_payment -> send_email, if charge_payment fails,
        send_email is never reached, create_booking is compensated, and world is FULLY_RESTORED.
        """
        self.fault_injector.configure(
            fail_at_step=2,
            error_message="Card declined before reaching send_email",
        )

        plan = [
            {
                "step_id": "step_booking_1",
                "tool_name": "create_booking",
                "arguments": {"user": "Alex Rivera", "room": "Suite-305"},
            },
            {
                "step_id": "step_payment_2",
                "tool_name": "charge_payment",
                "arguments": {"user": "Alex Rivera", "amount": 550.0},
            },
            {
                "step_id": "step_email_3",
                "tool_name": "send_email",
                "arguments": {
                    "to": "alex@example.com",
                    "subject": "Booking Confirmed",
                    "body": "Suite-305 is ready.",
                },
            },
        ]

        tx = self.tm.execute_transaction(plan)

        self.assertEqual(tx.status, ExecutionStatus.COMPENSATED)
        self.assertEqual(tx.steps[0].status, ExecutionStatus.COMPENSATED)
        self.assertEqual(tx.steps[1].status, ExecutionStatus.FAILED)
        self.assertEqual(tx.steps[2].status, ExecutionStatus.PENDING)
        self.assertEqual(len(self.world.state["emails"]), 0)
        self.assertEqual(len(tx.irreversible_side_effects), 0)
        self.assertTrue(tx.world_restored)
        self.assertEqual(tx.restoration_status, "FULLY_RESTORED")
        self.assertTrue(self.world.is_clean())
        self.assertEqual(self.world.snapshot(), self.initial_snapshot)

    def test_failure_after_irreversible_action_reports_partial_rollback_never_fully_restored(
        self,
    ) -> None:
        """
        Test 6: Failure after irreversible action.
        Workflow: create_booking (Step 1) -> send_email (Step 2) -> charge_payment (Step 3, FAILS)
        When Step 3 fails after send_email was approved and executed:
        - Step 1 (create_booking) is compensated via cancel_booking
        - Step 2 (send_email) is NOT compensated (never pretend an irreversible action can be rolled back)
        - Transaction status is PARTIAL_ROLLBACK
        - Irreversible side effect (send_email) is explicitly identified
        - System NEVER claims FULLY_RESTORED (world_restored=False, restoration_status='PARTIAL_ROLLBACK')
        """
        self.fault_injector.configure(
            fail_at_step=3,
            error_message="Payment gateway error after email already sent",
        )

        plan = [
            {
                "step_id": "step_booking_1",
                "tool_name": "create_booking",
                "arguments": {"user": "Alex Rivera", "room": "Suite-306"},
            },
            {
                "step_id": "step_email_2",
                "tool_name": "send_email",
                "arguments": {
                    "to": "alex@example.com",
                    "subject": "Early Booking Notice",
                    "body": "Your booking request is being processed.",
                },
            },
            {
                "step_id": "step_payment_3",
                "tool_name": "charge_payment",
                "arguments": {"user": "Alex Rivera", "amount": 600.0},
            },
        ]

        # 1. Start execution -> pauses at Step 2 (send_email) for explicit approval
        tx = self.tm.execute_transaction(plan)
        self.assertEqual(tx.status, ExecutionStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(
            tx.approval_request,
            {
                "type": "APPROVAL_REQUIRED",
                "step_id": "step_email_2",
                "tool": "send_email",
                "reason": "This action cannot be undone.",
            },
        )

        # 2. User grants approval -> Step 2 (send_email) executes, then Step 3 (charge_payment) fails
        final_tx = self.tm.resolve_approval(
            tx.steps[1].approval_id,
            approved=True,
            reason="Approve sending early email notice",
        )

        # 3. Verify PARTIAL_ROLLBACK status and that FULLY_RESTORED is NEVER claimed
        self.assertEqual(final_tx.status, ExecutionStatus.PARTIAL_ROLLBACK)
        self.assertEqual(final_tx.restoration_status, "PARTIAL_ROLLBACK")
        self.assertNotEqual(final_tx.restoration_status, "FULLY_RESTORED")
        self.assertFalse(final_tx.world_restored)

        # 4. Verify reversible Step 1 was compensated, while irreversible Step 2 remains COMPLETED
        self.assertEqual(final_tx.steps[0].status, ExecutionStatus.COMPENSATED)
        self.assertEqual(final_tx.steps[1].status, ExecutionStatus.COMPLETED)
        self.assertEqual(final_tx.steps[2].status, ExecutionStatus.FAILED)

        self.assertEqual(len(final_tx.compensation_history), 1)
        self.assertEqual(final_tx.compensation_history[0].tool_name, "create_booking")
        self.assertEqual(
            final_tx.compensation_history[0].compensation_tool, "cancel_booking"
        )

        # 5. Verify irreversible side effect is explicitly identified on the transaction
        self.assertEqual(len(final_tx.irreversible_side_effects), 1)
        irreversible_effect = final_tx.irreversible_side_effects[0]
        self.assertEqual(irreversible_effect["step_id"], "step_email_2")
        self.assertEqual(irreversible_effect["tool_name"], "send_email")
        self.assertIn("cannot be undone", irreversible_effect["reason"])

        # 6. Verify MockWorld reflects reality: booking was rolled back, but email remains sent
        self.assertEqual(len(self.world.state["bookings"]), 0)
        self.assertEqual(len(self.world.state["payments"]), 0)
        self.assertEqual(len(self.world.state["emails"]), 1)
        self.assertFalse(self.world.is_clean())

        # 7. Verify PARTIAL_ROLLBACK event was logged
        event_types = [e.event_type for e in final_tx.events]
        self.assertIn(LogEventType.PARTIAL_ROLLBACK, event_types)


if __name__ == "__main__":
    unittest.main()
