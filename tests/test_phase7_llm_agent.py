"""Phase 7 unit and integration tests for the Hugging Face LLM Agent, PlanValidator, and end-to-end pipeline."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core import (
     COT_FORBIDDEN_KEYS,
    Agent,
    ApprovalManager,
    CompensationManager,
    DurableExecutionLog,
    ExecutionStatus,
    FaultInjector,
    HuggingFaceLLMClient,
    LLMAgent,
    MockWorld,
    PlanValidationError,
    PlanValidator,
    RecoveryManager,
    ToolExecutor,
    TransactionManager,
    create_default_tool_registry,
)


class TestPhase7LLMAgentAndValidation(unittest.TestCase):
    """Test suite for Phase 7 LLM Agent planning, policy validation, CoT stripping, and integration."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sqlite_path = Path(self.temp_dir.name) / "phase7_transactions.db"
        self.world = MockWorld()
        self.initial_snapshot = self.world.snapshot()
        self.registry = create_default_tool_registry()
        self.fault_injector = FaultInjector()
        self.durable_log = DurableExecutionLog(storage_path=self.sqlite_path)
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
        self.validator = PlanValidator(registry=self.registry)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_llm_only_produces_structured_plan_and_never_executes_tools(self) -> None:
        """
        Verify the LLM's responsibility is ONLY to understand the user's goal, produce a structured
        workflow plan, select registered tools, and provide tool arguments—never executing tools directly.
        """
        mock_llm_json = json.dumps(
            {
                "goal": "Book a hotel and create a support ticket",
                "steps": [
                    {
                        "tool": "create_booking",
                        "arguments": {"user": "Alex Rivera", "room": "Suite-501"},
                    },
                    {
                        "tool": "create_ticket",
                        "arguments": {
                            "user": "Alex Rivera",
                            "issue": "Late check-in request",
                        },
                    },
                ],
            }
        )
        llm_client = HuggingFaceLLMClient(mock_responder=mock_llm_json)
        llm_agent = LLMAgent(transaction_manager=self.tm, llm_client=llm_client)

        plan = llm_agent.create_structured_plan(
            "Please book Suite-501 for Alex Rivera and open a support ticket for late check-in."
        )

        # Verify structured plan matches expected goal, tools, and arguments
        self.assertEqual(plan.goal, "Book a hotel and create a support ticket")
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0].tool, "create_booking")
        self.assertEqual(
            plan.steps[0].arguments, {"user": "Alex Rivera", "room": "Suite-501"}
        )
        self.assertEqual(plan.steps[1].tool, "create_ticket")
        self.assertEqual(
            plan.steps[1].arguments,
            {"user": "Alex Rivera", "issue": "Late check-in request"},
        )

        # Verify NO tools were executed in MockWorld during LLM planning
        self.assertTrue(self.world.is_clean())
        self.assertEqual(len(self.world.operation_history), 0)
        self.assertEqual(self.world.snapshot(), self.initial_snapshot)

    def test_rejects_unknown_tools(self) -> None:
        """Verify PlanValidator and LLMAgent reject plans referencing unknown tools."""
        bad_plan = {
            "goal": "Run an unregistered tool",
            "steps": [
                {
                    "tool": "drop_customer_database",
                    "arguments": {"target": "production"},
                }
            ],
        }
        llm_agent = LLMAgent(
            transaction_manager=self.tm,
            mock_responder=bad_plan,
        )

        with self.assertRaises(PlanValidationError) as ctx:
            llm_agent.execute_request("Drop the database")
        self.assertEqual(ctx.exception.code, "UNKNOWN_TOOL")
        self.assertIn("drop_customer_database", str(ctx.exception))
        self.assertTrue(self.world.is_clean())

    def test_rejects_invalid_arguments(self) -> None:
        """Verify PlanValidator rejects missing required arguments, wrong types, and unexpected arguments."""
        # 1. Missing required argument ('room' missing for create_booking)
        missing_arg_plan = {
            "goal": "Book hotel with missing room",
            "steps": [
                {
                    "tool": "create_booking",
                    "arguments": {"user": "Alex Rivera"},
                }
            ],
        }
        with self.assertRaises(PlanValidationError) as ctx1:
            self.validator.validate(missing_arg_plan)
        self.assertEqual(ctx1.exception.code, "INVALID_ARGUMENTS")

        # 2. Wrong argument type ('amount' is string instead of number)
        wrong_type_plan = {
            "goal": "Charge payment with invalid amount type",
            "steps": [
                {
                    "tool": "charge_payment",
                    "arguments": {"user": "Alex Rivera", "amount": "five hundred"},
                }
            ],
        }
        with self.assertRaises(PlanValidationError) as ctx2:
            self.validator.validate(wrong_type_plan)
        self.assertEqual(ctx2.exception.code, "INVALID_ARGUMENTS")

        # 3. Unexpected unknown argument key
        extra_arg_plan = {
            "goal": "Book hotel with extra unknown argument",
            "steps": [
                {
                    "tool": "create_booking",
                    "arguments": {
                        "user": "Alex Rivera",
                        "room": "Suite-101",
                        "bypass_security": True,
                    },
                }
            ],
        }
        with self.assertRaises(PlanValidationError) as ctx3:
            self.validator.validate(extra_arg_plan)
        self.assertEqual(ctx3.exception.code, "INVALID_ARGUMENTS")

    def test_rejects_missing_compensation_for_supposedly_reversible_side_effect(
        self,
    ) -> None:
        """
        Verify PlanValidator rejects any step that claims to be reversible when no compensation exists,
        or disables compensation on a reversible tool.
        """
        # Case 1: LLM claims send_email is reversible (irreversible=False)
        fake_reversible_email_plan = {
            "goal": "Send email pretending it is reversible",
            "steps": [
                {
                    "tool": "send_email",
                    "arguments": {
                        "to": "alex@example.com",
                        "subject": "Hello",
                        "body": "World",
                    },
                    "irreversible": False,
                }
            ],
        }
        with self.assertRaises(PlanValidationError) as ctx1:
            self.validator.validate(fake_reversible_email_plan)
        self.assertEqual(ctx1.exception.code, "MISSING_COMPENSATION")

        # Case 2: Step claims create_booking is reversible but sets compensation_available=False
        disabled_comp_plan = {
            "goal": "Book room without compensation",
            "steps": [
                {
                    "tool": "create_booking",
                    "arguments": {"user": "Alex Rivera", "room": "Suite-101"},
                    "is_reversible": True,
                    "compensation_available": False,
                }
            ],
        }
        with self.assertRaises(PlanValidationError) as ctx2:
            self.validator.validate(disabled_comp_plan)
        self.assertEqual(ctx2.exception.code, "MISSING_COMPENSATION")

        # Case 3: Step specifies a bogus compensation_tool for create_booking
        invalid_comp_tool_plan = {
            "goal": "Book room with invalid compensation tool",
            "steps": [
                {
                    "tool": "create_booking",
                    "arguments": {"user": "Alex Rivera", "room": "Suite-101"},
                    "is_reversible": True,
                    "compensation_tool": "fake_undo_booking",
                }
            ],
        }
        with self.assertRaises(PlanValidationError) as ctx3:
            self.validator.validate(invalid_comp_tool_plan)
        self.assertEqual(ctx3.exception.code, "MISSING_COMPENSATION")

    def test_rejects_unsafe_irreversible_ordering(self) -> None:
        """
        Verify PlanValidator and LLMAgent reject plans that place an irreversible tool (send_email)
        before reversible side-effecting steps (create_booking, charge_payment).
        """
        unsafe_order_plan = {
            "goal": "Send confirmation email before booking and charging payment",
            "steps": [
                {
                    "tool": "send_email",
                    "arguments": {
                        "to": "alex@example.com",
                        "subject": "Booked!",
                        "body": "Your room is booked.",
                    },
                },
                {
                    "tool": "create_booking",
                    "arguments": {"user": "Alex Rivera", "room": "Suite-200"},
                },
                {
                    "tool": "charge_payment",
                    "arguments": {"user": "Alex Rivera", "amount": 350.0},
                },
            ],
        }
        llm_agent = LLMAgent(
            transaction_manager=self.tm,
            mock_responder=unsafe_order_plan,
        )

        with self.assertRaises(PlanValidationError) as ctx:
            llm_agent.execute_request(
                "Send email, book hotel, and charge payment"
            )
        self.assertEqual(ctx.exception.code, "UNSAFE_IRREVERSIBLE_ORDERING")
        self.assertTrue(self.world.is_clean())

    def test_rejects_malformed_plans(self) -> None:
        """Verify PlanValidator rejects invalid JSON, missing goal, empty steps, and malformed step items."""
        malformed_inputs = [
            "not valid json {{{",
            {},
            {"goal": "", "steps": [{"tool": "create_booking", "arguments": {"user": "A", "room": "1"}}]},
            {"goal": "Valid goal", "steps": []},
            {"goal": "Valid goal", "steps": "not_a_list"},
            {"goal": "Valid goal", "steps": [{"arguments": {"user": "A", "room": "1"}}]},
        ]
        for bad_input in malformed_inputs:
            with self.subTest(bad_input=bad_input):
                with self.assertRaises(PlanValidationError) as ctx:
                    self.validator.validate(bad_input)
                self.assertEqual(ctx.exception.code, "MALFORMED_PLAN")

    def test_does_not_expose_chain_of_thought_and_stores_concise_decision_metadata(
        self,
    ) -> None:
        """
        Verify chain-of-thought (<think> tags and reasoning/chain_of_thought keys) is completely
        stripped and only concise decision metadata is persisted.
        """
        raw_llm_output_with_cot = """
        <think>
        Hidden chain-of-thought: The user wants to book a room and create a ticket.
        Let me reason step by step privately...
        </think>
        ```json
        {
          "goal": "Book hotel and open support ticket",
          "chain_of_thought": "SECRET_COT_REASONING_SHOULD_BE_REMOVED",
          "reasoning": "INTERNAL_STEP_BY_STEP_THOUGHTS",
          "steps": [
            {
              "tool": "create_booking",
              "thought": "First I should call create_booking",
              "arguments": {
                "user": "Alex Rivera",
                "room": "Suite-777",
                "scratchpad": "DO_NOT_EXPOSE"
              }
            },
            {
              "tool": "create_ticket",
              "arguments": {
                "user": "Alex Rivera",
                "issue": "Extra pillows"
              }
            }
          ]
        }
        ```
        """
        llm_agent = LLMAgent(
            transaction_manager=self.tm,
            mock_responder=raw_llm_output_with_cot,
        )

        tx = llm_agent.execute_request("Book Suite-777 and request extra pillows")
        self.assertEqual(tx.status, ExecutionStatus.COMPLETED)

        # Verify concise decision_metadata is present
        self.assertEqual(tx.decision_metadata["planner"], "HuggingFaceLLMAgent")
        self.assertEqual(
            tx.decision_metadata["goal"], "Book hotel and open support ticket"
        )
        self.assertEqual(
            tx.decision_metadata["selected_tools"],
            ["create_booking", "create_ticket"],
        )
        self.assertEqual(tx.decision_metadata["step_count"], 2)
        self.assertEqual(tx.decision_metadata["validation_status"], "VALIDATED")

        # Verify NO chain-of-thought strings or keys appear anywhere in TransactionState, report, or SQLite log
        report_json = json.dumps(tx.to_report(world_state=self.world.snapshot()))
        self.assertNotIn("Hidden chain-of-thought", report_json)
        self.assertNotIn("SECRET_COT_REASONING_SHOULD_BE_REMOVED", report_json)
        self.assertNotIn("INTERNAL_STEP_BY_STEP_THOUGHTS", report_json)
        self.assertNotIn("First I should call create_booking", report_json)
        self.assertNotIn("DO_NOT_EXPOSE", report_json)
        for forbidden_key in COT_FORBIDDEN_KEYS:
            self.assertNotIn(f'"{forbidden_key}"', report_json)

    def test_llm_cannot_bypass_approval_or_transaction_controls(self) -> None:
        """
        Verify that even if the LLM output attempts to set approval_required=False on send_email,
        TransactionManager and PlanValidator still enforce WAITING_FOR_APPROVAL.
        """
        bypass_attempt_plan = {
            "goal": "Book room and send email without approval",
            "steps": [
                {
                    "tool": "create_booking",
                    "arguments": {"user": "Alex Rivera", "room": "Suite-800"},
                },
                {
                    "tool": "send_email",
                    "arguments": {
                        "to": "alex@example.com",
                        "subject": "Bypass Attempt",
                        "body": "Trying to skip approval",
                    },
                    "approval_required": False,
                    "requires_approval": False,
                },
            ],
        }
        llm_agent = LLMAgent(
            transaction_manager=self.tm,
            mock_responder=bypass_attempt_plan,
        )

        tx = llm_agent.execute_request("Book room and send email")
        # Must pause at WAITING_FOR_APPROVAL despite LLM trying to set approval_required=False
        self.assertEqual(tx.status, ExecutionStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(len(self.world.state["emails"]), 0)
        self.assertIsNotNone(tx.approval_request)
        self.assertEqual(tx.approval_request["type"], "APPROVAL_REQUIRED")
        self.assertEqual(tx.approval_request["tool"], "send_email")

    def test_end_to_end_integration_user_request_to_llm_plan_to_final_transaction_report(
        self,
    ) -> None:
        """
        Integration Test:
        User request -> LLM plan -> TransactionManager -> tools -> MockWorld -> final transaction report.
        Verifies both:
        1. Full forward execution and final report.
        2. Step 3 failure triggering reverse-order Saga rollback and final report with world_restored=True.
        """
        mock_llm_plan = {
            "goal": "Book a hotel, charge payment, and create a support ticket",
            "steps": [
                {
                    "tool": "create_booking",
                    "arguments": {"user": "Alex Rivera", "room": "Penthouse-1"},
                },
                {
                    "tool": "charge_payment",
                    "arguments": {"user": "Alex Rivera", "amount": 750.0},
                },
                {
                    "tool": "create_ticket",
                    "arguments": {
                        "user": "Alex Rivera",
                        "issue": "Prepare welcome package",
                    },
                },
            ],
        }
        agent = Agent(
            transaction_manager=self.tm,
            mock_llm_responder=mock_llm_plan,
        )

        # Part A: Happy-path end-to-end execution
        report_success = agent.run_and_report(
            "Book Penthouse-1 for Alex Rivera, charge $750, and open a welcome package ticket."
        )

        self.assertEqual(report_success["status"], "COMPLETED")
        self.assertEqual(
            report_success["goal"],
            "Book a hotel, charge payment, and create a support ticket",
        )
        self.assertEqual(
            report_success["decision_metadata"]["selected_tools"],
            ["create_booking", "charge_payment", "create_ticket"],
        )
        self.assertEqual(len(report_success["steps"]), 3)
        self.assertEqual(len(report_success["world_state"]["bookings"]), 1)
        self.assertEqual(len(report_success["world_state"]["payments"]), 1)
        self.assertEqual(len(report_success["world_state"]["tickets"]), 1)

        # Undo the completed transaction so MockWorld returns to clean baseline
        self.tm.undo_transaction(report_success["transaction_id"])
        self.assertTrue(self.world.is_clean())

        # Part B: End-to-end execution with deterministic fault at Step 3 (create_ticket)
        self.fault_injector.configure(
            fail_at_step=3,
            error_message="Ticket service outage during Step 3",
        )

        report_rollback = agent.run_and_report(
            "Book Penthouse-1 for Alex Rivera, charge $750, and open a welcome package ticket."
        )

        self.assertEqual(report_rollback["status"], "COMPENSATED")
        self.assertEqual(report_rollback["restoration_status"], "FULLY_RESTORED")
        self.assertTrue(report_rollback["world_restored"])
        self.assertEqual(
            [c["compensation_tool"] for c in report_rollback["compensation_history"]],
            ["refund_payment", "cancel_booking"],
        )
        self.assertTrue(self.world.is_clean())
        self.assertEqual(report_rollback["world_state"], self.initial_snapshot)


if __name__ == "__main__":
    unittest.main()
