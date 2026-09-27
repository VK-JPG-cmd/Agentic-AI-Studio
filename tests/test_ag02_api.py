"""Integration tests for the AG02 FastAPI backend endpoints."""

from __future__ import annotations

import copy
import unittest

from fastapi.testclient import TestClient

from app.api.routes import AG02Runtime
from app.main import create_app


class TestAG02FastAPI(unittest.TestCase):
    """Test FastAPI endpoints for AG02 Transactional Execution Layer."""

    def setUp(self) -> None:
        self.runtime = AG02Runtime()
        self.app = create_app(self.runtime)
        self.client = TestClient(self.app)

    def test_health_and_tools_endpoints(self) -> None:
        health_resp = self.client.get("/health")
        self.assertEqual(health_resp.status_code, 200)
        self.assertEqual(health_resp.json()["challenge"], "AG02")

        tools_resp = self.client.get("/tools")
        self.assertEqual(tools_resp.status_code, 200)
        tools = tools_resp.json()["tools"]
        tool_names = {t["name"] for t in tools}
        self.assertIn("book_flight", tool_names)
        self.assertIn("reserve_hotel", tool_names)
        self.assertIn("charge_payment", tool_names)

    def test_api_execute_and_manual_undo_restores_world(self) -> None:
        initial_world = copy.deepcopy(
            self.client.get("/world").json()["state"]
        )

        exec_resp = self.client.post(
            "/agent/execute",
            json={"goal": "Book flight, reserve hotel, and charge payment"},
        )
        self.assertEqual(exec_resp.status_code, 200)
        tx = exec_resp.json()["transaction"]
        self.assertEqual(tx["status"], "COMPLETED")
        tx_id = tx["transaction_id"]

        undo_resp = self.client.post(
            f"/transactions/{tx_id}/undo",
            json={"reason": "Customer cancelled trip"},
        )
        self.assertEqual(undo_resp.status_code, 200)
        undone_tx = undo_resp.json()["transaction"]
        self.assertEqual(undone_tx["status"], "COMPENSATED")
        self.assertEqual(undo_resp.json()["world_state"], initial_world)

    def test_api_fault_injection_and_automatic_reverse_rollback(self) -> None:
        initial_world = copy.deepcopy(
            self.client.get("/world").json()["state"]
        )

        fault_resp = self.client.post(
            "/faults",
            json={
                "phase": "execute",
                "step_index": 2,
                "error_message": "Simulated failure on Step 3 via API",
                "fail_once": True,
            },
        )
        self.assertEqual(fault_resp.status_code, 200)

        exec_resp = self.client.post(
            "/agent/execute",
            json={"goal": "Book flight, reserve hotel, and charge payment"},
        )
        self.assertEqual(exec_resp.status_code, 200)
        data = exec_resp.json()
        tx = data["transaction"]
        self.assertEqual(tx["status"], "COMPENSATED")

        comp_history = tx["compensation_history"]
        self.assertEqual(
            [c["step_index"] for c in comp_history],
            [1, 0],
        )
        self.assertEqual(data["world_state"], initial_world)


if __name__ == "__main__":
    unittest.main()
