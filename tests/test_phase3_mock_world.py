"""Unit tests for Phase 3 MockWorld and all mocked tools (Hotel, Payment, Ticket, Email)."""

from __future__ import annotations

import unittest

from app.core import (
    ExecutionStatus,
    FaultInjector,
    MockWorld,
    ToolExecutor,
    TransactionManager,
    WorkflowPlan,
    WorkflowStepSpec,
    cancel_booking,
    charge_payment,
    create_booking,
    create_default_tool_registry,
    create_ticket,
    delete_ticket,
    refund_payment,
    send_email,
)


class TestPhase3MockWorldAndTools(unittest.TestCase):
    """Test suite verifying MockWorld state, snapshot(), is_clean(), and all Phase 3 mocked tools."""

    def setUp(self) -> None:
        self.world = MockWorld()
        self.registry = create_default_tool_registry()
        self.fault_injector = FaultInjector()
        self.executor = ToolExecutor(
            registry=self.registry,
            world=self.world,
            fault_injector=self.fault_injector,
        )
        self.tm = TransactionManager(
            registry=self.registry,
            world=self.world,
            fault_injector=self.fault_injector,
            tool_executor=self.executor,
        )

    def test_initial_world_state_structure_and_is_clean(self) -> None:
        """Verify MockWorld contains bookings, payments, tickets, emails and starts clean."""
        snap = self.world.snapshot()
        self.assertIn("bookings", snap)
        self.assertIn("payments", snap)
        self.assertIn("tickets", snap)
        self.assertIn("emails", snap)

        self.assertEqual(snap["bookings"], {})
        self.assertEqual(snap["payments"], {})
        self.assertEqual(snap["tickets"], {})
        self.assertEqual(snap["emails"], [])
        self.assertTrue(self.world.is_clean())

    def test_hotel_create_booking_and_cancel_booking(self) -> None:
        """Verify create_booking(user, room) adds a booking and cancel_booking(booking_id) removes it."""
        initial_world = self.world.snapshot()
        self.assertTrue(self.world.is_clean())

        booking = create_booking(self.world, user="alice", room="Suite-305")
        booking_id = booking["booking_id"]

        self.assertIn(booking_id, self.world.bookings)
        self.assertEqual(self.world.bookings[booking_id]["user"], "alice")
        self.assertEqual(self.world.bookings[booking_id]["room"], "Suite-305")
        self.assertFalse(self.world.is_clean())

        cancelled = cancel_booking(self.world, booking_id)
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertNotIn(booking_id, self.world.bookings)

        final_world = self.world.snapshot()
        self.assertEqual(initial_world, final_world)
        self.assertTrue(self.world.is_clean())

    def test_payment_charge_payment_and_refund_payment(self) -> None:
        """Verify charge_payment(user, amount) adds a payment and refund_payment(payment_id) removes it."""
        initial_world = self.world.snapshot()
        self.assertTrue(self.world.is_clean())

        payment = charge_payment(self.world, user="user_default", amount=250.0)
        payment_id = payment["payment_id"]

        self.assertIn(payment_id, self.world.payments)
        self.assertEqual(self.world.payments[payment_id]["amount"], 250.0)
        self.assertFalse(self.world.is_clean())

        refund = refund_payment(self.world, payment_id)
        self.assertEqual(refund["status"], "REFUNDED")
        self.assertNotIn(payment_id, self.world.payments)

        final_world = self.world.snapshot()
        self.assertEqual(initial_world, final_world)
        self.assertTrue(self.world.is_clean())

    def test_ticket_create_ticket_and_delete_ticket(self) -> None:
        """Verify create_ticket(user, issue) adds a ticket and delete_ticket(ticket_id) removes it."""
        initial_world = self.world.snapshot()
        self.assertTrue(self.world.is_clean())

        ticket = create_ticket(
            self.world, user="alice", issue="Late check-in request"
        )
        ticket_id = ticket["ticket_id"]

        self.assertIn(ticket_id, self.world.tickets)
        self.assertEqual(self.world.tickets[ticket_id]["user"], "alice")
        self.assertEqual(
            self.world.tickets[ticket_id]["issue"], "Late check-in request"
        )
        self.assertFalse(self.world.is_clean())

        deleted = delete_ticket(self.world, ticket_id)
        self.assertEqual(deleted["status"], "DELETED")
        self.assertNotIn(ticket_id, self.world.tickets)

        final_world = self.world.snapshot()
        self.assertEqual(initial_world, final_world)
        self.assertTrue(self.world.is_clean())

    def test_send_email_is_irreversible_and_requires_approval(self) -> None:
        """Verify send_email(to, subject, body) modifies MockWorld and is marked irreversible=True, approval_required=True."""
        email_tool = self.registry.get("send_email")
        self.assertTrue(email_tool.irreversible)
        self.assertFalse(email_tool.is_reversible)
        self.assertFalse(email_tool.compensation_available)
        self.assertIsNone(email_tool.compensate_action)
        self.assertIsNone(email_tool.compensation_tool)
        self.assertTrue(email_tool.approval_required)
        self.assertTrue(email_tool.requires_approval)

        # Execute send_email and verify it appends to world.emails
        email_record = send_email(
            self.world,
            to="alice@example.com",
            subject="Booking Confirmation",
            body="Your room Suite-305 is confirmed.",
        )
        self.assertEqual(len(self.world.emails), 1)
        self.assertEqual(self.world.emails[0]["email_id"], email_record["email_id"])
        self.assertEqual(self.world.emails[0]["to"], "alice@example.com")
        self.assertFalse(self.world.is_clean())

        # Attempting to compensate send_email via ToolExecutor must raise RuntimeError
        with self.assertRaises(RuntimeError):
            self.executor.compensate(
                "send_email",
                {
                    "to": "alice@example.com",
                    "subject": "Booking Confirmation",
                    "body": "Your room Suite-305 is confirmed.",
                },
                email_record,
            )

    def test_transaction_rollback_restores_initial_world_snapshot_and_is_clean(
        self,
    ) -> None:
        """
        Verify that before a transaction we capture initial_world = world.snapshot(),
        and after Step 3 fails and rollback completes:
        - initial_world == final_world
        - world.is_clean() is True
        """
        initial_world = self.world.snapshot()
        self.assertTrue(self.world.is_clean())

        plan = WorkflowPlan(
            goal="Book room, charge payment, and open support ticket",
            steps=[
                WorkflowStepSpec(
                    step_id="s1_booking",
                    tool_name="create_booking",
                    arguments={"user": "alice", "room": "Deluxe-204"},
                ),
                WorkflowStepSpec(
                    step_id="s2_payment",
                    tool_name="charge_payment",
                    arguments={"user": "user_default", "amount": 350.0},
                ),
                WorkflowStepSpec(
                    step_id="s3_ticket",
                    tool_name="create_ticket",
                    arguments={"user": "alice", "issue": "Airport shuttle"},
                ),
            ],
        )

        # Fail Step 3 (create_ticket)
        self.fault_injector.fail_step(
            step_index=2, error_message="Ticket service offline"
        )

        tx = self.tm.execute_transaction(plan)
        final_world = self.world.snapshot()

        self.assertEqual(tx.status, ExecutionStatus.COMPENSATED)
        self.assertEqual(initial_world, final_world)
        self.assertEqual(tx.initial_world, tx.final_world)
        self.assertTrue(self.world.is_clean())
        self.assertTrue(tx.world_restored)


if __name__ == "__main__":
    unittest.main()
