"""Unit tests for AG02 core models, execution states, ToolRegistry, and MockWorld."""

from __future__ import annotations

import unittest

from app.core import (
    ExecutionStatus,
    MockWorld,
    StepExecution,
    ToolDefinition,
    ToolExecutor,
    ToolRegistry,
    TransactionManager,
    TransactionState,
    WorkflowPlan,
    WorkflowStepSpec,
    create_default_tool_registry,
    is_valid_transition,
)


class TestModelsAndTransactionStates(unittest.TestCase):
    """Test core execution statuses, unique IDs, and state machine transitions."""

    def test_all_required_execution_statuses_present(self) -> None:
        required_statuses = {
            "PENDING",
            "RUNNING",
            "COMPLETED",
            "FAILED",
            "COMPENSATING",
            "COMPENSATED",
            "PARTIAL_ROLLBACK",
            "ROLLBACK_FAILED",
            "WAITING_FOR_APPROVAL",
            "RECOVERING",
            "ABORTED",
        }
        actual_statuses = {status.value for status in ExecutionStatus}
        self.assertTrue(required_statuses.issubset(actual_statuses))
        self.assertEqual(required_statuses, actual_statuses)

    def test_unique_transaction_and_step_ids(self) -> None:
        world = MockWorld()
        registry = create_default_tool_registry()
        tm = TransactionManager(registry=registry, world=world)

        plan = WorkflowPlan(
            goal="Test unique IDs",
            steps=[
                WorkflowStepSpec(
                    tool_name="book_flight",
                    inputs={"flight_id": "FL-101", "passenger_name": "Alex"},
                ),
                WorkflowStepSpec(
                    tool_name="reserve_hotel",
                    inputs={"hotel_id": "HT-GRAND", "guest_name": "Alex"},
                ),
            ],
        )

        tx1 = tm.create_transaction(plan)
        tx2 = tm.create_transaction(plan)

        self.assertNotEqual(tx1.transaction_id, tx2.transaction_id)
        self.assertTrue(tx1.transaction_id.startswith("tx_"))
        self.assertTrue(tx2.transaction_id.startswith("tx_"))

        all_step_ids = [s.step_id for s in tx1.steps] + [s.step_id for s in tx2.steps]
        self.assertEqual(len(all_step_ids), 4)
        self.assertEqual(len(set(all_step_ids)), 4)
        for step_id in all_step_ids:
            self.assertTrue(step_id.startswith("step_"))

    def test_valid_and_invalid_state_transitions(self) -> None:
        tx = TransactionState(plan_id="plan_1", user_goal="Test transitions")
        self.assertEqual(tx.status, ExecutionStatus.PENDING)

        # Valid path: PENDING -> RUNNING -> FAILED -> COMPENSATING -> COMPENSATED
        tx.transition_to(ExecutionStatus.RUNNING)
        self.assertEqual(tx.status, ExecutionStatus.RUNNING)

        tx.transition_to(ExecutionStatus.FAILED)
        self.assertEqual(tx.status, ExecutionStatus.FAILED)

        tx.transition_to(ExecutionStatus.COMPENSATING)
        self.assertEqual(tx.status, ExecutionStatus.COMPENSATING)

        tx.transition_to(ExecutionStatus.COMPENSATED)
        self.assertEqual(tx.status, ExecutionStatus.COMPENSATED)
        self.assertIsNotNone(tx.completed_at)

        # Terminal COMPENSATED cannot transition back to RUNNING
        self.assertFalse(
            is_valid_transition(ExecutionStatus.COMPENSATED, ExecutionStatus.RUNNING)
        )
        with self.assertRaises(ValueError):
            tx.transition_to(ExecutionStatus.RUNNING)

    def test_completed_can_transition_to_compensating_for_manual_undo(self) -> None:
        tx = TransactionState(plan_id="plan_2", user_goal="Test manual undo transition")
        tx.transition_to(ExecutionStatus.RUNNING)
        tx.transition_to(ExecutionStatus.COMPLETED)
        self.assertTrue(
            is_valid_transition(
                ExecutionStatus.COMPLETED, ExecutionStatus.COMPENSATING
            )
        )
        tx.transition_to(ExecutionStatus.COMPENSATING)
        tx.transition_to(ExecutionStatus.COMPENSATED)
        self.assertEqual(tx.status, ExecutionStatus.COMPENSATED)


class TestToolRegistryAndMockWorld(unittest.TestCase):
    """Test ToolDefinition declarations, input validation, and MockWorld reversibility."""

    def test_every_default_tool_declares_required_metadata(self) -> None:
        registry = create_default_tool_registry()
        tools = registry.list_tools()
        self.assertGreaterEqual(len(tools), 6)

        for tool in tools:
            self.assertTrue(tool.name)
            self.assertTrue(tool.description)
            self.assertIsInstance(tool.input_schema, dict)
            self.assertTrue(callable(tool.execute_action))
            self.assertIsInstance(tool.is_reversible, bool)
            self.assertIsInstance(tool.requires_approval, bool)
            if tool.is_reversible:
                self.assertTrue(callable(tool.compensate_action))
            else:
                self.assertIsNone(tool.compensate_action)

    def test_reversible_tool_without_compensate_action_raises(self) -> None:
        registry = ToolRegistry()
        with self.assertRaises(ValueError):
            registry.register_tool(
                name="broken_reversible_tool",
                description="Missing compensation handler",
                input_schema={"type": "object", "required": []},
                execute_action=lambda world, inputs: {},
                is_reversible=True,
                compensate_action=None,
                requires_approval=False,
            )

    def test_tool_input_schema_validation(self) -> None:
        registry = create_default_tool_registry()
        flight_tool = registry.get("book_flight")

        # Missing required passenger_name
        with self.assertRaises(ValueError):
            flight_tool.validate_inputs({"flight_id": "FL-101"})

        # Wrong type for seats (string instead of integer)
        with self.assertRaises(ValueError):
            flight_tool.validate_inputs(
                {"flight_id": "FL-101", "passenger_name": "Alex", "seats": "two"}
            )

        # Bool passed for integer should be rejected
        with self.assertRaises(ValueError):
            flight_tool.validate_inputs(
                {"flight_id": "FL-101", "passenger_name": "Alex", "seats": True}
            )

    def test_direct_tool_execute_and_compensate_restores_mock_world(self) -> None:
        world = MockWorld()
        initial_snapshot = world.snapshot()
        registry = create_default_tool_registry()
        executor = ToolExecutor(registry=registry, world=world)

        out = executor.execute(
            "book_flight",
            {"flight_id": "FL-101", "passenger_name": "Alex Rivera", "seats": 2},
            step_index=0,
            step_id="step_test_1",
        )
        self.assertEqual(world.state["flights"]["FL-101"]["available_seats"], 8)
        self.assertIn(out["booking_id"], world.state["flight_bookings"])

        executor.compensate(
            "book_flight",
            {"flight_id": "FL-101", "passenger_name": "Alex Rivera", "seats": 2},
            out,
            step_index=0,
            step_id="step_test_1",
        )
        self.assertEqual(world.snapshot(), initial_snapshot)


if __name__ == "__main__":
    unittest.main()
