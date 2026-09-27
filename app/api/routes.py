"""REST API endpoints and Demo Dashboard routes for AG02: The Agent With An Undo Button."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.core import (
    Agent,
    ApprovalManager,
    CompensationManager,
    DurableExecutionLog,
    ExecutionStatus,
    FaultInjector,
    FaultTargetPhase,
    MockWorld,
    PlanValidationError,
    RecoveryManager,
    ToolExecutor,
    ToolRegistry,
    TransactionManager,
    TransactionState,
    WorkflowStepSpec,
    create_default_tool_registry,
)


def _snapshot_counts(snapshot: dict[str, Any]) -> dict[str, int]:
    """Extract top-level entity counts (bookings, payments, tickets, emails) from a MockWorld snapshot."""
    if not snapshot:
        return {"bookings": 0, "payments": 0, "tickets": 0, "emails": 0}
    bookings_count = len(snapshot.get("bookings", {})) or (
        len(snapshot.get("flight_bookings", {}))
        + len(snapshot.get("hotel_reservations", {}))
    )
    payments_count = len(snapshot.get("payments", {})) or len(
        snapshot.get("payment_charges", {})
    )
    tickets_count = len(snapshot.get("tickets", {})) or len(
        snapshot.get("tickets_issued", {})
    )
    emails_count = len(snapshot.get("emails", [])) or len(
        snapshot.get("emails_sent", [])
    )
    return {
        "bookings": bookings_count,
        "payments": payments_count,
        "tickets": tickets_count,
        "emails": emails_count,
    }


def compute_world_metrics(
    tx: TransactionState, current_world_snapshot: dict[str, Any]
) -> dict[str, Any]:
    """
    Compute Before, During (peak forward side effects), and After counts for MockWorld
    so the AG02 Demo Dashboard can display exact state transitions.
    """
    before_counts = _snapshot_counts(tx.world_snapshot_before)
    after_counts = _snapshot_counts(
        tx.world_snapshot_after or current_world_snapshot
    )

    during_counts = dict(before_counts)
    for step in tx.steps:
        # Any step that completed forward execution (even if later compensated or rollback_failed)
        if step.status in {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.COMPENSATED,
            ExecutionStatus.ROLLBACK_FAILED,
        }:
            if step.tool_name in {"create_booking", "book_flight", "reserve_hotel"}:
                during_counts["bookings"] += 1
            elif step.tool_name in {"charge_payment", "high_value_wire_transfer"}:
                during_counts["payments"] += 1
            elif step.tool_name in {"create_ticket", "issue_ticket"}:
                during_counts["tickets"] += 1
            elif step.tool_name in {
                "send_email",
                "send_notification",
                "dispatch_irreversible_notification",
            }:
                during_counts["emails"] += 1

    return {
        "before": before_counts,
        "during": during_counts,
        "after": after_counts,
        "world_restored": bool(tx.world_restored),
        "restoration_status": tx.restoration_status,
    }


class AG02Runtime:
    """Dependency container assembling all core AG02 components."""

    def __init__(self) -> None:
        self.world = MockWorld()
        self.registry: ToolRegistry = create_default_tool_registry()
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
        self.recovery_manager = RecoveryManager(
            durable_log=self.durable_log,
            compensation_manager=self.compensation_manager,
        )
        self.agent = Agent(transaction_manager=self.transaction_manager)

    def reset_all(self) -> dict[str, Any]:
        """Reset world state, logs, approvals, and fault rules."""
        snapshot = self.world.reset()
        self.durable_log.clear()
        self.approval_manager.clear()
        self.fault_injector.clear()
        return snapshot


class ExecuteWorkflowRequest(BaseModel):
    goal: str = Field(default="Book flight, reserve hotel, and charge payment")
    steps: Optional[list[WorkflowStepSpec]] = None


class DemoExecuteRequest(BaseModel):
    goal: str = Field(
        default="Book a hotel, charge payment, create a support ticket."
    )
    steps: Optional[list[WorkflowStepSpec]] = None
    fail_at_step: Optional[int] = None
    fail_during_rollback: bool = False
    fail_compensation_at_step: Optional[int] = None


class SimulateCrashRequest(BaseModel):
    goal: str = Field(
        default="Book a hotel, charge payment, create a support ticket."
    )
    crash_after_step: int = 2


class DemoRecoverRequest(BaseModel):
    transaction_id: Optional[str] = None
    strategy: str = "rollback"


class ApprovalDecisionRequest(BaseModel):
    approved: bool
    reason: Optional[str] = None


class ReasonRequest(BaseModel):
    reason: str = "User requested action"


class CreateFaultRuleRequest(BaseModel):
    phase: FaultTargetPhase = FaultTargetPhase.EXECUTE
    tool_name: Optional[str] = None
    step_index: Optional[int] = None
    step_id: Optional[str] = None
    error_message: str = "Simulated fault triggered by FaultInjector"
    fail_once: bool = True


def create_router(runtime: AG02Runtime) -> APIRouter:
    """Create the FastAPI router bound to the given AG02Runtime instance."""
    router = APIRouter()

    @router.get("/")
    def info() -> dict[str, Any]:
        return {
            "name": "AG02 Saga Transactional Engine",
            "version": "1.0.0",
            "status": "ready",
            "registered_tools": len(runtime.registry.list_tools()),
            "transactions_count": len(runtime.transaction_manager.list_transactions()),
        }

    @router.get("/health")
    def health_check() -> dict[str, Any]:
        return {
            "status": "ok",
            "challenge": "AG02",
            "title": "The Agent With An Undo Button",
            "registered_tools": len(runtime.registry.list_tools()),
            "transactions_count": len(runtime.transaction_manager.list_transactions()),
        }

    @router.get("/world")
    def get_world_state() -> dict[str, Any]:
        snap = runtime.world.snapshot()
        return {
            "state": snap,
            "counts": _snapshot_counts(snap),
            "is_clean": runtime.world.is_clean(),
            "operation_history": runtime.world.operation_history,
        }

    @router.post("/world/reset")
    def reset_world_state() -> dict[str, Any]:
        snapshot = runtime.reset_all()
        return {"status": "reset", "state": snapshot}

    @router.get("/tools")
    def list_tools() -> dict[str, Any]:
        return {"tools": runtime.registry.list_metadata()}

    @router.post("/agent/plan")
    def plan_agent_workflow(req: ExecuteWorkflowRequest) -> dict[str, Any]:
        try:
            plan = runtime.agent.create_plan(goal=req.goal, steps=req.steps)
        except PlanValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": exc.code, "message": exc.message, "details": exc.details},
            ) from exc
        return {
            "plan": plan.model_dump(),
        }

    @router.post("/agent/execute")
    def execute_agent_workflow(req: ExecuteWorkflowRequest) -> dict[str, Any]:
        try:
            tx = runtime.agent.execute(goal=req.goal, steps=req.steps)
        except PlanValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": exc.code, "message": exc.message, "details": exc.details},
            ) from exc
        snap = runtime.world.snapshot()
        return {
            "transaction": tx.model_dump(),
            "report": tx.to_report(world_state=snap),
            "world_metrics": compute_world_metrics(tx, snap),
            "world_state": snap,
        }

    @router.post("/demo/execute")
    def demo_execute_workflow(req: DemoExecuteRequest) -> dict[str, Any]:
        runtime.fault_injector.clear()
        comp_step = req.fail_compensation_at_step
        if req.fail_during_rollback and comp_step is None:
            # Default to failing compensation on step 1 (or the step right before fail_at_step)
            comp_step = max(1, (req.fail_at_step or 2) - 1)

        if req.fail_at_step is not None or comp_step is not None:
            runtime.fault_injector.configure(
                fail_at_step=req.fail_at_step,
                fail_compensation_at_step=comp_step,
                error_message=(
                    f"Deterministic fault injected at Step {req.fail_at_step}"
                    if req.fail_at_step
                    else "Deterministic rollback fault injected"
                ),
            )

        try:
            tx = runtime.agent.execute(goal=req.goal, steps=req.steps)
        except PlanValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": exc.code, "message": exc.message, "details": exc.details},
            ) from exc

        snap = runtime.world.snapshot()
        return {
            "transaction": tx.model_dump(),
            "report": tx.to_report(world_state=snap),
            "world_metrics": compute_world_metrics(tx, snap),
            "world_state": snap,
        }

    @router.post("/demo/simulate-crash")
    def demo_simulate_crash(req: SimulateCrashRequest) -> dict[str, Any]:
        runtime.fault_injector.clear()
        step_idx = max(0, req.crash_after_step - 1)
        runtime.fault_injector.simulate_crash_after_step(
            step_index=step_idx,
            error_message=f"Simulated process crash immediately after Step {req.crash_after_step}",
        )
        tx = runtime.agent.execute(goal=req.goal, raise_on_crash=False)
        snap = runtime.world.snapshot()
        return {
            "transaction": tx.model_dump(),
            "report": tx.to_report(world_state=snap),
            "world_metrics": compute_world_metrics(tx, snap),
            "world_state": snap,
        }

    @router.post("/demo/recover")
    def demo_recover_transaction(req: DemoRecoverRequest) -> dict[str, Any]:
        runtime.fault_injector.clear()
        tx_id = req.transaction_id
        if not tx_id:
            incomplete = runtime.recovery_manager.find_recoverable_transactions()
            if not incomplete:
                raise HTTPException(
                    status_code=404,
                    detail="No incomplete transactions found in SQLite durable log.",
                )
            tx_id = incomplete[0].transaction_id

        try:
            tx = runtime.recovery_manager.recover_transaction(
                tx_id, strategy=req.strategy
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        snap = runtime.world.snapshot()
        return {
            "transaction": tx.model_dump(),
            "report": tx.to_report(world_state=snap),
            "world_metrics": compute_world_metrics(tx, snap),
            "world_state": snap,
        }

    @router.get("/transactions")
    def list_transactions() -> dict[str, Any]:
        return {
            "transactions": [
                tx.model_dump()
                for tx in runtime.transaction_manager.list_transactions()
            ]
        }

    @router.get("/transactions/{transaction_id}")
    def get_transaction(transaction_id: str) -> dict[str, Any]:
        try:
            tx = runtime.transaction_manager.get_transaction(transaction_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        snap = runtime.world.snapshot()
        return {
            "transaction": tx.model_dump(),
            "report": tx.to_report(world_state=snap),
            "world_metrics": compute_world_metrics(tx, snap),
            "logs": [
                e.model_dump()
                for e in runtime.durable_log.get_events(transaction_id=transaction_id)
            ],
        }

    @router.post("/transactions/{transaction_id}/undo")
    def undo_transaction(
        transaction_id: str, req: Optional[ReasonRequest] = None
    ) -> dict[str, Any]:
        reason = req.reason if req else "Manual Undo Button triggered by user"
        try:
            tx = runtime.agent.undo(transaction_id, reason=reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        snap = runtime.world.snapshot()
        return {
            "transaction": tx.model_dump(),
            "report": tx.to_report(world_state=snap),
            "world_metrics": compute_world_metrics(tx, snap),
            "world_state": snap,
        }

    @router.post("/transactions/{transaction_id}/abort")
    def abort_transaction(
        transaction_id: str, req: Optional[ReasonRequest] = None
    ) -> dict[str, Any]:
        reason = req.reason if req else "Transaction aborted by user"
        try:
            tx = runtime.transaction_manager.abort_transaction(
                transaction_id, reason=reason
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        snap = runtime.world.snapshot()
        return {
            "transaction": tx.model_dump(),
            "report": tx.to_report(world_state=snap),
            "world_metrics": compute_world_metrics(tx, snap),
            "world_state": snap,
        }

    @router.post("/transactions/{transaction_id}/recover")
    def recover_transaction(transaction_id: str) -> dict[str, Any]:
        try:
            tx = runtime.recovery_manager.recover_transaction(transaction_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        snap = runtime.world.snapshot()
        return {
            "transaction": tx.model_dump(),
            "report": tx.to_report(world_state=snap),
            "world_metrics": compute_world_metrics(tx, snap),
            "world_state": snap,
        }

    @router.post("/recover")
    def recover_all_transactions() -> dict[str, Any]:
        recovered = runtime.recovery_manager.recover_all()
        return {
            "recovered_transactions": [tx.model_dump() for tx in recovered],
            "world_state": runtime.world.snapshot(),
        }

    @router.get("/approvals")
    def list_approvals(transaction_id: Optional[str] = None) -> dict[str, Any]:
        approvals = runtime.approval_manager.list_requests(
            transaction_id=transaction_id
        )
        return {"approvals": [a.model_dump() for a in approvals]}

    @router.post("/approvals/{approval_id}/decide")
    def decide_approval(
        approval_id: str, req: ApprovalDecisionRequest
    ) -> dict[str, Any]:
        try:
            tx = runtime.transaction_manager.resolve_approval(
                approval_id, approved=req.approved, reason=req.reason
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        snap = runtime.world.snapshot()
        return {
            "transaction": tx.model_dump(),
            "report": tx.to_report(world_state=snap),
            "world_metrics": compute_world_metrics(tx, snap),
            "world_state": snap,
        }

    @router.get("/logs")
    def get_durable_logs(transaction_id: Optional[str] = None) -> dict[str, Any]:
        entries = runtime.durable_log.get_events(transaction_id=transaction_id)
        return {"logs": [e.model_dump() for e in entries]}

    @router.get("/faults")
    def list_fault_rules() -> dict[str, Any]:
        return {
            "fault_rules": [
                r.model_dump() for r in runtime.fault_injector.list_rules()
            ]
        }

    @router.post("/faults")
    def add_fault_rule(req: CreateFaultRuleRequest) -> dict[str, Any]:
        rule = runtime.fault_injector.add_rule(
            phase=req.phase,
            tool_name=req.tool_name,
            step_index=req.step_index,
            step_id=req.step_id,
            error_message=req.error_message,
            fail_once=req.fail_once,
        )
        return {"rule": rule.model_dump()}

    @router.delete("/faults")
    def clear_fault_rules() -> dict[str, Any]:
        runtime.fault_injector.clear()
        return {"status": "cleared"}

    return router
