"""AG02 AI Agent translating user goals into deterministic WorkflowPlans and executing transactions."""

from __future__ import annotations

from typing import Any, Optional

from app.core.llm_agent import (
    HuggingFaceLLMClient,
    LLMAgent,
    MockResponderType,
    PlanValidator,
)
from app.core.models import (
    TransactionState,
    WorkflowPlan,
    WorkflowStepSpec,
)
from app.core.transaction_manager import TransactionManager


class Agent:
    """Deterministic and LLM-powered tool-calling Agent backed by the AG02 Transactional Execution Layer."""

    def __init__(
        self,
        transaction_manager: TransactionManager,
        *,
        llm_client: Optional[HuggingFaceLLMClient] = None,
        mock_llm_responder: Optional[MockResponderType] = None,
    ) -> None:
        self.transaction_manager = transaction_manager
        self.validator = PlanValidator(registry=transaction_manager.registry)
        self.llm_client = llm_client or (
            HuggingFaceLLMClient(mock_responder=mock_llm_responder)
            if mock_llm_responder is not None
            else None
        )
        self.llm_agent: Optional[LLMAgent] = (
            LLMAgent(
                transaction_manager=self.transaction_manager,
                llm_client=self.llm_client,
            )
            if self.llm_client is not None
            else None
        )

    def _enrich_plan(self, plan: WorkflowPlan) -> WorkflowPlan:
        """Populate tool reversibility, compensation_tool, and approval metadata on every step spec."""
        enriched_steps: list[WorkflowStepSpec] = []
        total = len(plan.steps)
        for idx, spec in enumerate(plan.steps):
            t_name = spec.tool_name or spec.tool
            if self.transaction_manager.registry.has(t_name):
                t_def = self.transaction_manager.registry.get(t_name)
                is_rev = (
                    spec.is_reversible
                    if spec.is_reversible is not None
                    else t_def.is_reversible
                )
                comp_avail = bool(is_rev and t_def.compensation_available)
                comp_tool = t_def.compensation_tool if comp_avail else None
                req_appr = bool(
                    t_def.requires_approval
                    or (not is_rev and idx < total - 1)
                    or spec.requires_approval
                    or spec.approval_required
                )
                enriched_steps.append(
                    WorkflowStepSpec(
                        step_id=spec.step_id,
                        tool=t_name,
                        tool_name=t_name,
                        description=spec.description or t_def.description,
                        arguments=dict(spec.arguments or spec.inputs),
                        inputs=dict(spec.inputs or spec.arguments),
                        compensation_available=comp_avail,
                        compensation_tool=comp_tool,
                        irreversible=not is_rev,
                        is_reversible=is_rev,
                        approval_required=req_appr,
                        requires_approval=req_appr,
                    )
                )
            else:
                enriched_steps.append(spec)

        decision_meta = plan.decision_metadata or {
            "planner": "AG02StructuredPlanner",
            "goal": plan.goal,
            "selected_tools": [s.tool_name for s in enriched_steps],
            "step_count": len(enriched_steps),
            "has_irreversible_steps": any(bool(s.irreversible) for s in enriched_steps),
            "requires_approval": any(bool(s.requires_approval) for s in enriched_steps),
            "validation_status": "VALIDATED",
        }
        return WorkflowPlan(
            plan_id=plan.plan_id,
            goal=plan.goal,
            steps=enriched_steps,
            created_at=plan.created_at,
            metadata={"decision_metadata": decision_meta},
            decision_metadata=decision_meta,
        )

    def create_plan(
        self,
        goal: str,
        steps: Optional[list[WorkflowStepSpec | dict[str, Any]]] = None,
        *,
        reorder_irreversible_last: bool = True,
    ) -> WorkflowPlan:
        """Build a deterministic or LLM-planned WorkflowPlan from explicit steps or goal intent."""
        if steps is not None:
            parsed_steps = [
                s if isinstance(s, WorkflowStepSpec) else WorkflowStepSpec.model_validate(s)
                for s in steps
            ]
            raw_plan = WorkflowPlan(goal=goal, steps=parsed_steps)
            ordered = (
                self.transaction_manager.optimize_plan(raw_plan)
                if reorder_irreversible_last
                else raw_plan
            )
            return self._enrich_plan(ordered)

        if self.llm_agent is not None:
            return self.llm_agent.create_structured_plan(goal)

        goal_lower = goal.lower()

        if "ticket" in goal_lower and "laptop" not in goal_lower and "inventory" not in goal_lower:
            plan_steps = [
                WorkflowStepSpec(
                    tool_name="create_booking",
                    description="Create Booking",
                    inputs={"user": "Alex Rivera", "room": "Suite-404"},
                ),
                WorkflowStepSpec(
                    tool_name="charge_payment",
                    description="Charge Payment",
                    inputs={"user": "Alex Rivera", "amount": 420.00},
                ),
                WorkflowStepSpec(
                    tool_name="create_ticket",
                    description="Create Ticket",
                    inputs={
                        "user": "Alex Rivera",
                        "issue": "VIP Check-in & Support Ticket",
                    },
                ),
            ]
            if "email" in goal_lower or "notification" in goal_lower:
                plan_steps.append(
                    WorkflowStepSpec(
                        tool_name="send_email",
                        description="Send Email",
                        inputs={
                            "to": "alex@example.com",
                            "subject": "Booking & Ticket Confirmed",
                            "body": "Your Suite-404 reservation and support ticket are confirmed.",
                        },
                    )
                )
            return self._enrich_plan(WorkflowPlan(goal=goal, steps=plan_steps))

        if "email" in goal_lower:
            return self._enrich_plan(
                WorkflowPlan(
                    goal=goal,
                    steps=[
                        WorkflowStepSpec(
                            tool_name="create_booking",
                            description="Create hotel room booking",
                            inputs={"user": "Alex Rivera", "room": "Suite-404"},
                        ),
                        WorkflowStepSpec(
                            tool_name="charge_payment",
                            description="Charge customer payment",
                            inputs={"user": "Alex Rivera", "amount": 420.00},
                        ),
                        WorkflowStepSpec(
                            tool_name="send_email",
                            description="Send irreversible booking confirmation email",
                            inputs={
                                "to": "alex@example.com",
                                "subject": "Booking Confirmed",
                                "body": "Your Suite-404 reservation is confirmed.",
                            },
                        ),
                    ],
                )
            )

        if "wire" in goal_lower or "approval" in goal_lower or "vip" in goal_lower:
            return self._enrich_plan(
                WorkflowPlan(
                    goal=goal,
                    steps=[
                        WorkflowStepSpec(
                            tool_name="book_flight",
                            description="Book international flight FL-202",
                            inputs={
                                "flight_id": "FL-202",
                                "passenger_name": "Jordan Chen",
                                "seats": 1,
                            },
                        ),
                        WorkflowStepSpec(
                            tool_name="high_value_wire_transfer",
                            description="Execute high-value wire transfer (requires approval)",
                            inputs={
                                "user_id": "user_vip",
                                "amount": 3500.00,
                                "description": "VIP Concierge Wire Transfer",
                            },
                        ),
                        WorkflowStepSpec(
                            tool_name="create_calendar_event",
                            description="Add trip to executive calendar",
                            inputs={
                                "title": "London Executive Trip",
                                "date": "2026-10-15",
                                "attendees": ["Jordan Chen"],
                            },
                        ),
                    ],
                )
            )

        if "inventory" in goal_lower or "laptop" in goal_lower or "order" in goal_lower:
            return self._enrich_plan(
                WorkflowPlan(
                    goal=goal,
                    steps=[
                        WorkflowStepSpec(
                            tool_name="reserve_inventory",
                            description="Reserve 2x ThinkPad X1 Carbon in warehouse",
                            inputs={"sku": "ITEM-LAPTOP", "quantity": 2},
                        ),
                        WorkflowStepSpec(
                            tool_name="charge_payment",
                            description="Charge account for hardware order",
                            inputs={
                                "user_id": "user_default",
                                "amount": 2800.00,
                                "description": "2x ITEM-LAPTOP order",
                            },
                        ),
                        WorkflowStepSpec(
                            tool_name="issue_ticket",
                            description="Issue fulfillment dispatch ticket",
                            inputs={
                                "holder_name": "Alex Rivera",
                                "reference": "ORD-LAPTOP-001",
                            },
                        ),
                    ],
                )
            )

        # Default canonical 3-step AG02 Travel Booking Saga:
        # Step 1: book_flight -> Step 2: reserve_hotel -> Step 3: charge_payment
        return self._enrich_plan(
            WorkflowPlan(
                goal=goal,
                steps=[
                    WorkflowStepSpec(
                        tool_name="book_flight",
                        description="Book flight FL-101 from SFO to JFK",
                        inputs={
                            "flight_id": "FL-101",
                            "passenger_name": "Alex Rivera",
                            "seats": 1,
                        },
                    ),
                    WorkflowStepSpec(
                        tool_name="reserve_hotel",
                        description="Reserve room at Grand Horizon Hotel",
                        inputs={
                            "hotel_id": "HT-GRAND",
                            "guest_name": "Alex Rivera",
                            "nights": 2,
                            "rooms": 1,
                        },
                    ),
                    WorkflowStepSpec(
                        tool_name="charge_payment",
                        description="Charge user wallet for flight and hotel package",
                        inputs={
                            "user_id": "user_default",
                            "amount": 1050.00,
                            "description": "Travel package: FL-101 + HT-GRAND",
                        },
                    ),
                ],
            )
        )

    def execute(
        self,
        goal: str,
        steps: Optional[list[WorkflowStepSpec | dict[str, Any]]] = None,
        *,
        raise_on_crash: bool = False,
    ) -> TransactionState:
        """Plan and transactionally execute a multi-step workflow for `goal`."""
        plan = self.create_plan(goal=goal, steps=steps)
        return self.transaction_manager.execute_plan(
            plan, raise_on_crash=raise_on_crash
        )

    def execute_plan(
        self, plan: WorkflowPlan, *, raise_on_crash: bool = False
    ) -> TransactionState:
        """Execute an explicit WorkflowPlan through the TransactionManager."""
        return self.transaction_manager.execute_plan(
            plan, raise_on_crash=raise_on_crash
        )

    def undo(
        self,
        transaction_id: str,
        reason: str = "User triggered Undo Button",
    ) -> TransactionState:
        """Undo a completed transaction in reverse step order."""
        return self.transaction_manager.undo_transaction(
            transaction_id, reason=reason
        )

    def approve(
        self, approval_id: str, reason: Optional[str] = None
    ) -> TransactionState:
        """Approve a pending step approval request and resume execution."""
        return self.transaction_manager.resolve_approval(
            approval_id, approved=True, reason=reason
        )

    def reject(
        self, approval_id: str, reason: Optional[str] = None
    ) -> TransactionState:
        """Reject a pending step approval request and roll back prior steps."""
        return self.transaction_manager.resolve_approval(
            approval_id, approved=False, reason=reason
        )

    def run_and_report(
        self,
        goal: str,
        steps: Optional[list[WorkflowStepSpec | dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Execute a workflow for `goal` and return the final structured transaction report."""
        tx = self.execute(goal=goal, steps=steps)
        return tx.to_report(world_state=self.transaction_manager.world.snapshot())
