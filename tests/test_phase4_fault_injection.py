"""Automated test suite for Phase 4 Fault Injection Engine and 4-step workflow test matrix."""

from __future__ import annotations

import unittest
from typing import Any

from app.core import (
    ExecutionStatus,
    FailureType,
    FaultInjector,
    MockWorld,
    ToolExecutor,
    TransactionManager,
    WorkflowPlan,
    WorkflowStepSpec,
    create_default_tool_registry,
)


class TestPhase4FaultInjectionMatrix(unittest.TestCase):
    """Deterministic FaultInjector tests across every step position and compensation phase."""

    WORKFLOW_NAME = "booking -> payment -> ticket -> notification"

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

    def _build_four_step_workflow(self) -> WorkflowPlan:
        """
        Build the 4-step workflow:
        Step 1: booking (create_booking -> cancel_booking)
        Step 2: payment (charge_payment -> refund_payment)
        Step 3: ticket (create_ticket -> delete_ticket)
        Step 4: notification (send_notification)
        """
        return WorkflowPlan(
            goal=self.WORKFLOW_NAME,
            steps=[
                WorkflowStepSpec(
                    step_id="step_1_booking",
                    tool_name="create_booking",
                    arguments={"user": "alice", "room": "Suite-401"},
                    compensation_tool="cancel_booking",
                ),
                WorkflowStepSpec(
                    step_id="step_2_payment",
                    tool_name="charge_payment",
                    arguments={"user": "user_default", "amount": 500.0},
                    compensation_tool="refund_payment",
                ),
                WorkflowStepSpec(
                    step_id="step_3_ticket",
                    tool_name="create_ticket",
                    arguments={"user": "alice", "issue": "VIP Check-in"},
                    compensation_tool="delete_ticket",
                ),
                WorkflowStepSpec(
                    step_id="step_4_notification",
                    tool_name="send_notification",
                    arguments={
                        "to": "alice@example.com",
                        "subject": "Reservation Confirmed",
                        "body": "Your booking, payment, and ticket are ready.",
                    },
                ),
            ],
        )

    def _run_failure_scenario(self, fail_at_step: int) -> dict[str, Any]:
        """
        Execute the 7-step verification protocol for a given 1-based failure position:
        1. capture initial MockWorld
        2. inject failure at `fail_at_step`
        3. execute transaction (which triggers automatic rollback)
        4. verify unexecuted steps were never run
        5. capture final MockWorld
        6. compare initial and final state
        7. assert restored == True and return structured test result
        """
        self.world.reset()
        self.fault_injector.clear()

        # 1. Capture initial MockWorld
        initial_world = self.world.snapshot()
        self.assertTrue(self.world.is_clean())

        # 2. Inject deterministic failure at 1-based step position
        self.fault_injector.fail_at_step = fail_at_step

        # 3. Execute transaction (and automatic rollback)
        plan = self._build_four_step_workflow()
        tx = self.tm.execute_transaction(plan)

        # 4. Verify subsequent steps were never executed
        for idx, step in enumerate(tx.steps):
            step_num = idx + 1
            if step_num < fail_at_step:
                self.assertEqual(
                    step.execution_status, ExecutionStatus.COMPENSATED
                )
            elif step_num == fail_at_step:
                self.assertEqual(step.execution_status, ExecutionStatus.FAILED)
            else:
                self.assertEqual(
                    step.execution_status, ExecutionStatus.PENDING
                )

        # 5. Capture final MockWorld
        final_world = self.world.snapshot()

        # 6. Compare initial and final state
        self.assertEqual(initial_world, final_world)
        self.assertEqual(tx.initial_world, tx.final_world)

        # 7. Assert restored == True
        self.assertTrue(tx.world_restored)
        self.assertTrue(self.world.is_clean())
        self.assertEqual(tx.status, ExecutionStatus.COMPENSATED)

        return tx.to_test_result(workflow=self.WORKFLOW_NAME)

    def test_failure_at_step_1(self) -> None:
        """Failure at step 1 -> expected compensation: none ([])."""
        result = self._run_failure_scenario(fail_at_step=1)
        self.assertEqual(
            result,
            {
                "workflow": self.WORKFLOW_NAME,
                "failure_step": 1,
                "execution_result": "FAILED",
                "rollback_result": "SUCCESS",
                "world_restored": True,
                "compensation_order": [],
            },
        )

    def test_failure_at_step_2(self) -> None:
        """Failure at step 2 -> expected compensation: ['cancel_booking']."""
        result = self._run_failure_scenario(fail_at_step=2)
        self.assertEqual(
            result,
            {
                "workflow": self.WORKFLOW_NAME,
                "failure_step": 2,
                "execution_result": "FAILED",
                "rollback_result": "SUCCESS",
                "world_restored": True,
                "compensation_order": ["cancel_booking"],
            },
        )

    def test_failure_at_step_3(self) -> None:
        """Failure at step 3 -> expected compensation: ['refund_payment', 'cancel_booking']."""
        result = self._run_failure_scenario(fail_at_step=3)
        self.assertEqual(
            result,
            {
                "workflow": self.WORKFLOW_NAME,
                "failure_step": 3,
                "execution_result": "FAILED",
                "rollback_result": "SUCCESS",
                "world_restored": True,
                "compensation_order": ["refund_payment", "cancel_booking"],
            },
        )

    def test_failure_at_step_4(self) -> None:
        """Failure at step 4 -> expected compensation: ['delete_ticket', 'refund_payment', 'cancel_booking']."""
        result = self._run_failure_scenario(fail_at_step=4)
        self.assertEqual(
            result,
            {
                "workflow": self.WORKFLOW_NAME,
                "failure_step": 4,
                "execution_result": "FAILED",
                "rollback_result": "SUCCESS",
                "world_restored": True,
                "compensation_order": [
                    "delete_ticket",
                    "refund_payment",
                    "cancel_booking",
                ],
            },
        )

    def test_complete_four_step_fault_matrix(self) -> None:
        """Run the complete 4-step matrix and assert exact compensation order for every position."""
        expected_matrix = {
            1: [],
            2: ["cancel_booking"],
            3: ["refund_payment", "cancel_booking"],
            4: ["delete_ticket", "refund_payment", "cancel_booking"],
        }

        for step_pos, expected_comp_order in expected_matrix.items():
            with self.subTest(failure_position=step_pos):
                result = self._run_failure_scenario(fail_at_step=step_pos)
                self.assertEqual(result["failure_step"], step_pos)
                self.assertEqual(result["execution_result"], "FAILED")
                self.assertEqual(result["rollback_result"], "SUCCESS")
                self.assertTrue(result["world_restored"])
                self.assertEqual(
                    result["compensation_order"], expected_comp_order
                )

    def test_fail_compensation_at_step_2(self) -> None:
        """
        Test compensation failure:
        fail_at_step = 4
        fail_compensation_at_step = 2 (refund_payment fails during rollback)
        Step 3 (delete_ticket) succeeds compensation, Step 2 (refund_payment) fails compensation.
        """
        self.fault_injector.configure(
            fail_at_step=4,
            fail_compensation_at_step=2,
            failure_type=FailureType.SERVICE_UNAVAILABLE.value,
            error_message="Payment gateway refund failure at step 2",
        )

        plan = self._build_four_step_workflow()
        tx = self.tm.execute_transaction(plan)
        result = tx.to_test_result(workflow=self.WORKFLOW_NAME)

        self.assertEqual(tx.status, ExecutionStatus.ROLLBACK_FAILED)
        self.assertEqual(result["failure_step"], 4)
        self.assertEqual(result["execution_result"], "FAILED")
        self.assertEqual(result["rollback_result"], "FAILED")
        self.assertFalse(result["world_restored"])
        # Step 3 (delete_ticket) was compensated before Step 2 failed
        self.assertEqual(result["compensation_order"], ["delete_ticket"])
        self.assertFalse(self.world.is_clean())
        self.assertIsNotNone(tx.rollback_alert)
        self.assertEqual(
            tx.rollback_alert["failed_compensation_step"], "step_2_payment"
        )

    def test_fault_injector_scoped_by_transaction_id_and_failure_type(
        self,
    ) -> None:
        """Verify FaultInjector rules scoped to a specific transaction_id only affect that transaction."""
        self.fault_injector.inject_step_failure(
            step_number=2,
            transaction_id="tx_target_001",
            failure_type=FailureType.TIMEOUT.value,
            error_message="Step 2 timed out for tx_target_001",
        )

        # Unrelated transaction should succeed all 4 steps
        plan_ok = self._build_four_step_workflow()
        tx_ok = self.tm.execute_transaction(
            plan_ok, transaction_id="tx_other_002"
        )
        self.assertEqual(tx_ok.status, ExecutionStatus.COMPLETED)

        # Reset world for target transaction
        self.world.reset()
        initial_world = self.world.snapshot()

        plan_fail = self._build_four_step_workflow()
        tx_fail = self.tm.execute_transaction(
            plan_fail, transaction_id="tx_target_001"
        )
        self.assertEqual(tx_fail.status, ExecutionStatus.COMPENSATED)
        self.assertEqual(tx_fail.steps[1].status, ExecutionStatus.FAILED)
        self.assertIn("Step 2 timed out", tx_fail.error or "")
        self.assertEqual(self.world.snapshot(), initial_world)


if __name__ == "__main__":
    unittest.main()
