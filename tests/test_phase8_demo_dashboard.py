"""Phase 8 end-to-end tests for the AG02 Demo Dashboard UI and interactive control plane API."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.api.routes import AG02Runtime
from app.main import create_app


class TestPhase8AG02DemoDashboard(unittest.TestCase):
    """Verify the AG02 Demo Dashboard HTML sections and complete end-to-end control plane flows."""

    def setUp(self) -> None:
        self.runtime = AG02Runtime()
        self.app = create_app(self.runtime)
        self.client = TestClient(self.app)

    def test_engine_info_and_health_endpoints(self) -> None:
        """Verify GET / and GET /health return engine info and active tool counts."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["name"], "AG02 Saga Transactional Engine")
        self.assertEqual(data["status"], "ready")
        self.assertGreaterEqual(data["registered_tools"], 4)

        health_resp = self.client.get("/health")
        self.assertEqual(health_resp.status_code, 200)
        health_data = health_resp.json()
        self.assertEqual(health_data["status"], "ok")

    def test_end_to_end_plan_execute_and_manual_undo_world_state_transitions(
        self,
    ) -> None:
        """
        Verify User Request -> Generated Plan -> Execute -> Undo Button:
        Before: Bookings 0, Payments 0, Tickets 0
        During: Bookings 1, Payments 1, Tickets 1
        After rollback: Bookings 0, Payments 0, Tickets 0
        world_restored = True (WORLD FULLY RESTORED)
        """
        goal = "Book a hotel, charge payment, create a support ticket."

        # 1. Generate Plan
        plan_resp = self.client.post("/agent/plan", json={"goal": goal})
        self.assertEqual(plan_resp.status_code, 200)
        plan = plan_resp.json()["plan"]
        self.assertEqual(
            [s["tool_name"] for s in plan["steps"]],
            ["create_booking", "charge_payment", "create_ticket"],
        )
        self.assertEqual(
            [s["compensation_tool"] for s in plan["steps"]],
            ["cancel_booking", "refund_payment", "delete_ticket"],
        )
        for step in plan["steps"]:
            self.assertTrue(step["is_reversible"])
            self.assertFalse(step["requires_approval"])

        # 2. Execute Workflow
        exec_resp = self.client.post("/demo/execute", json={"goal": goal})
        self.assertEqual(exec_resp.status_code, 200)
        exec_data = exec_resp.json()
        tx = exec_data["transaction"]
        self.assertEqual(tx["status"], "COMPLETED")
        self.assertEqual(
            exec_data["world_metrics"]["before"],
            {"bookings": 0, "payments": 0, "tickets": 0, "emails": 0},
        )
        self.assertEqual(
            exec_data["world_metrics"]["during"],
            {"bookings": 1, "payments": 1, "tickets": 1, "emails": 0},
        )
        self.assertEqual(
            exec_data["world_metrics"]["after"],
            {"bookings": 1, "payments": 1, "tickets": 1, "emails": 0},
        )

        # 3. Trigger Undo Button (compensates Delete Ticket -> Refund Payment -> Cancel Booking)
        undo_resp = self.client.post(
            f"/transactions/{tx['transaction_id']}/undo",
            json={"reason": "Dashboard Undo Button clicked"},
        )
        self.assertEqual(undo_resp.status_code, 200)
        undo_data = undo_resp.json()
        undone_tx = undo_data["transaction"]

        self.assertEqual(undone_tx["status"], "COMPENSATED")
        self.assertTrue(undone_tx["world_restored"])
        self.assertEqual(undone_tx["restoration_status"], "FULLY_RESTORED")
        self.assertEqual(
            [c["compensation_tool"] for c in undone_tx["compensation_history"]],
            ["delete_ticket", "refund_payment", "cancel_booking"],
        )
        self.assertEqual(
            undo_data["world_metrics"]["before"],
            {"bookings": 0, "payments": 0, "tickets": 0, "emails": 0},
        )
        self.assertEqual(
            undo_data["world_metrics"]["during"],
            {"bookings": 1, "payments": 1, "tickets": 1, "emails": 0},
        )
        self.assertEqual(
            undo_data["world_metrics"]["after"],
            {"bookings": 0, "payments": 0, "tickets": 0, "emails": 0},
        )

    def test_end_to_end_failure_injection_panel_and_fail_during_rollback(self) -> None:
        """Verify Failure Injection Panel: Fail at step 3 and Fail during rollback."""
        goal = "Book a hotel, charge payment, create a support ticket."

        # A: Fail at Step 3 -> Create Booking (OK), Charge Payment (OK), Create Ticket (FAILED)
        # Automatic rollback: Refund Payment -> Cancel Booking
        fail3_resp = self.client.post(
            "/demo/execute",
            json={"goal": goal, "fail_at_step": 3, "fail_during_rollback": False},
        )
        self.assertEqual(fail3_resp.status_code, 200)
        fail3_data = fail3_resp.json()
        tx3 = fail3_data["transaction"]

        self.assertEqual(tx3["status"], "COMPENSATED")
        self.assertTrue(tx3["world_restored"])
        self.assertEqual(tx3["steps"][0]["status"], "COMPENSATED")
        self.assertEqual(tx3["steps"][1]["status"], "COMPENSATED")
        self.assertEqual(tx3["steps"][2]["status"], "FAILED")
        self.assertEqual(
            [c["compensation_tool"] for c in tx3["compensation_history"]],
            ["refund_payment", "cancel_booking"],
        )
        self.assertEqual(
            fail3_data["world_metrics"]["after"],
            {"bookings": 0, "payments": 0, "tickets": 0, "emails": 0},
        )

        # B: Fail at Step 3 + Fail during rollback -> ROLLBACK_FAILED
        fail_rb_resp = self.client.post(
            "/demo/execute",
            json={"goal": goal, "fail_at_step": 3, "fail_during_rollback": True},
        )
        self.assertEqual(fail_rb_resp.status_code, 200)
        tx_rb = fail_rb_resp.json()["transaction"]
        self.assertEqual(tx_rb["status"], "ROLLBACK_FAILED")
        self.assertFalse(tx_rb["world_restored"])
        self.assertEqual(tx_rb["restoration_status"], "ROLLBACK_FAILED")

    def test_end_to_end_crash_simulation_and_recovery_after_restart(self) -> None:
        """Verify 'Simulate Crash' button and recovery after restart (both rollback and resume)."""
        goal = "Book a hotel, charge payment, create a support ticket."

        # 1. Simulate Crash after Step 2
        crash_resp = self.client.post(
            "/demo/simulate-crash",
            json={"goal": goal, "crash_after_step": 2},
        )
        self.assertEqual(crash_resp.status_code, 200)
        crashed_tx = crash_resp.json()["transaction"]
        self.assertEqual(crashed_tx["status"], "RUNNING")
        self.assertEqual(crashed_tx["steps"][0]["status"], "COMPLETED")
        self.assertEqual(crashed_tx["steps"][1]["status"], "COMPLETED")
        self.assertEqual(crashed_tx["steps"][2]["status"], "PENDING")

        # 2. Recover via Rollback
        rec_rb_resp = self.client.post(
            "/demo/recover",
            json={"transaction_id": crashed_tx["transaction_id"], "strategy": "rollback"},
        )
        self.assertEqual(rec_rb_resp.status_code, 200)
        rec_rb_data = rec_rb_resp.json()
        self.assertEqual(rec_rb_data["transaction"]["status"], "COMPENSATED")
        self.assertTrue(rec_rb_data["transaction"]["world_restored"])
        self.assertEqual(
            rec_rb_data["world_metrics"]["after"],
            {"bookings": 0, "payments": 0, "tickets": 0, "emails": 0},
        )

        # 3. Simulate another crash after Step 2 and Recover via Resume (no duplicate payment!)
        crash_resp_2 = self.client.post(
            "/demo/simulate-crash",
            json={"goal": goal, "crash_after_step": 2},
        )
        crashed_tx_2 = crash_resp_2.json()["transaction"]
        rec_res_resp = self.client.post(
            "/demo/recover",
            json={"transaction_id": crashed_tx_2["transaction_id"], "strategy": "resume"},
        )
        self.assertEqual(rec_res_resp.status_code, 200)
        rec_res_data = rec_res_resp.json()
        self.assertEqual(rec_res_data["transaction"]["status"], "COMPLETED")
        # Exactly 1 payment was charged (never double-charged during resume)
        self.assertEqual(
            rec_res_data["world_metrics"]["after"],
            {"bookings": 1, "payments": 1, "tickets": 1, "emails": 0},
        )

    def test_end_to_end_irreversible_action_send_email_approval_dialog(self) -> None:
        """Verify Section 9: Irreversible send_email pauses with APPROVAL_REQUIRED and executes upon approval."""
        goal = "Book a hotel, charge payment, create a support ticket, and send confirmation email."

        exec_resp = self.client.post("/demo/execute", json={"goal": goal})
        self.assertEqual(exec_resp.status_code, 200)
        tx = exec_resp.json()["transaction"]

        self.assertEqual(tx["status"], "WAITING_FOR_APPROVAL")
        self.assertEqual(tx["approval_request"]["type"], "APPROVAL_REQUIRED")
        self.assertEqual(tx["approval_request"]["tool"], "send_email")
        self.assertEqual(
            tx["approval_request"]["reason"], "This action cannot be undone."
        )
        self.assertEqual(
            exec_resp.json()["world_metrics"]["after"]["emails"], 0
        )

        approval_id = tx["steps"][3]["approval_id"]
        approve_resp = self.client.post(
            f"/approvals/{approval_id}/decide",
            json={"approved": True, "reason": "Approved via Demo Dashboard"},
        )
        self.assertEqual(approve_resp.status_code, 200)
        completed_data = approve_resp.json()
        self.assertEqual(completed_data["transaction"]["status"], "COMPLETED")
        self.assertEqual(completed_data["world_metrics"]["after"]["emails"], 1)


if __name__ == "__main__":
    unittest.main()
