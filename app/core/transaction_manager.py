"""TransactionManager orchestrating forward Saga execution, approval gates, and rollback."""

from __future__ import annotations

from typing import Any, Optional, Union

from app.core.approval_manager import ApprovalManager
from app.core.compensation_manager import CompensationManager
from app.core.durable_log import DurableExecutionLog
from app.core.fault_injector import FaultInjector, SimulatedProcessCrash
from app.core.mock_world import MockWorld
from app.core.models import (
    ExecutionStatus,
    LogEventType,
    StepExecution,
    TransactionState,
    WorkflowPlan,
    WorkflowStepSpec,
    generate_step_id,
    utc_now_iso,
)
from app.core.tool_registry import ToolExecutor, ToolRegistry

PlanInputType = Union[
    WorkflowPlan,
    list[Union[WorkflowStepSpec, StepExecution, dict[str, Any]]],
]


class TransactionManager:
    """Saga-inspired transactional execution manager for multi-step AI agent workflows."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        world: MockWorld,
        durable_log: Optional[DurableExecutionLog] = None,
        approval_manager: Optional[ApprovalManager] = None,
        fault_injector: Optional[FaultInjector] = None,
        compensation_manager: Optional[CompensationManager] = None,
        tool_executor: Optional[ToolExecutor] = None,
    ) -> None:
        self.registry = registry
        self.world = world
        self.durable_log = durable_log or DurableExecutionLog()
        self.approval_manager = approval_manager or ApprovalManager()
        self.fault_injector = fault_injector or FaultInjector()
        self.tool_executor = tool_executor or ToolExecutor(
            registry=self.registry,
            world=self.world,
            fault_injector=self.fault_injector,
        )
        self.compensation_manager = compensation_manager or CompensationManager(
            tool_executor=self.tool_executor,
            durable_log=self.durable_log,
        )

    def _record_event(
        self,
        transaction: TransactionState,
        *,
        event_type: LogEventType,
        status: ExecutionStatus,
        step_id: Optional[str] = None,
        message: str = "",
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        entry = self.durable_log.append_event(
            transaction_id=transaction.transaction_id,
            step_id=step_id,
            event_type=event_type,
            status=status,
            message=message,
            payload=payload,
        )
        transaction.events.append(entry)

    def _coerce_to_workflow_plan(self, plan_input: PlanInputType) -> WorkflowPlan:
        if isinstance(plan_input, WorkflowPlan):
            return plan_input
        if isinstance(plan_input, dict):
            from app.core.llm_agent import PlanValidator

            return PlanValidator(self.registry).validate(
                plan_input, allow_irreversible_before_reversible=False
            )
        if isinstance(plan_input, list):
            specs: list[WorkflowStepSpec] = []
            for item in plan_input:
                if isinstance(item, WorkflowStepSpec):
                    specs.append(item)
                elif isinstance(item, StepExecution):
                    specs.append(
                        WorkflowStepSpec(
                            step_id=item.step_id,
                            tool=item.tool_name,
                            tool_name=item.tool_name,
                            description=item.description,
                            arguments=dict(item.arguments),
                            inputs=dict(item.inputs),
                            execution_status=item.execution_status,
                            compensation_available=item.compensation_available,
                            compensation_tool=item.compensation_tool,
                            irreversible=item.irreversible,
                            is_reversible=item.is_reversible,
                            approval_required=item.approval_required,
                            requires_approval=item.requires_approval,
                        )
                    )
                elif isinstance(item, dict):
                    specs.append(WorkflowStepSpec.model_validate(item))
                else:
                    raise TypeError(f"Unsupported step specification type: {type(item)}")
            return WorkflowPlan(goal="Execute ordered workflow steps", steps=specs)
        raise TypeError(f"Unsupported plan input type: {type(plan_input)}")

    def validate_plan(
        self,
        raw_plan: Any,
        *,
        allow_irreversible_before_reversible: bool = False,
    ) -> WorkflowPlan:
        """Validate a structured plan against ToolRegistry and AG02 safety policies."""
        from app.core.llm_agent import PlanValidator

        return PlanValidator(self.registry).validate(
            raw_plan,
            allow_irreversible_before_reversible=allow_irreversible_before_reversible,
        )

    def _is_step_spec_irreversible(self, spec: WorkflowStepSpec) -> bool:
        """Determine whether a WorkflowStepSpec represents an irreversible tool action."""
        tool_def = self.registry.get(spec.tool_name)
        if spec.irreversible is not None:
            return spec.irreversible
        if spec.is_reversible is not None:
            return not spec.is_reversible
        return not tool_def.is_reversible

    def optimize_plan(self, plan_input: PlanInputType) -> WorkflowPlan:
        """
        Rule 1 & 4: Place reversible steps first and irreversible steps LAST whenever possible,
        preserving the relative order within reversible and irreversible groups.
        """
        plan = self._coerce_to_workflow_plan(plan_input)
        reversible_steps: list[WorkflowStepSpec] = []
        irreversible_steps: list[WorkflowStepSpec] = []
        for spec in plan.steps:
            if self._is_step_spec_irreversible(spec):
                irreversible_steps.append(spec)
            else:
                reversible_steps.append(spec)
        return WorkflowPlan(
            plan_id=plan.plan_id,
            goal=plan.goal,
            steps=reversible_steps + irreversible_steps,
            created_at=plan.created_at,
            metadata=dict(plan.metadata),
            decision_metadata=dict(plan.decision_metadata),
        )

    def create_transaction(
        self,
        plan_input: PlanInputType,
        *,
        transaction_id: Optional[str] = None,
        reorder_irreversible_last: bool = False,
    ) -> TransactionState:
        """Initialize a new TransactionState with unique transaction_id and step_ids."""
        from app.core.llm_agent import PlanValidationError, strip_chain_of_thought

        plan = (
            self.optimize_plan(plan_input)
            if reorder_irreversible_last
            else self._coerce_to_workflow_plan(plan_input)
        )
        tx_kwargs: dict[str, Any] = {
            "plan_id": plan.plan_id,
            "user_goal": plan.goal,
            "status": ExecutionStatus.PENDING,
            "world_snapshot_before": self.world.snapshot(),
        }
        if transaction_id is not None:
            tx_kwargs["transaction_id"] = transaction_id
        tx = TransactionState(**tx_kwargs)

        steps: list[StepExecution] = []
        used_step_ids: set[str] = set()
        total_steps = len(plan.steps)
        for idx, spec in enumerate(plan.steps):
            if not self.registry.has(spec.tool_name):
                raise PlanValidationError(
                    f"Unknown tool '{spec.tool_name}' at step {idx + 1}.",
                    code="UNKNOWN_TOOL",
                    details={"step_number": idx + 1, "tool": spec.tool_name},
                )
            tool_def = self.registry.get(spec.tool_name)

            # Determine reversibility / compensation metadata
            if spec.irreversible is not None:
                is_rev = not spec.irreversible
            elif spec.is_reversible is not None:
                is_rev = spec.is_reversible
            else:
                is_rev = tool_def.is_reversible

            if spec.compensation_available is not None:
                comp_avail = spec.compensation_available
            else:
                comp_avail = bool(is_rev and tool_def.compensation_available)

            # Reject missing compensation for a supposedly reversible side effect
            if is_rev and (
                not tool_def.is_reversible
                or not comp_avail
                or tool_def.compensate_action is None
                or not tool_def.compensation_tool
            ):
                raise PlanValidationError(
                    f"Missing compensation for supposedly reversible step '{spec.tool_name}' at step {idx + 1}.",
                    code="MISSING_COMPENSATION",
                    details={"step_number": idx + 1, "tool": spec.tool_name},
                )

            comp_tool = (
                spec.compensation_tool
                if spec.compensation_tool is not None
                else (tool_def.compensation_tool if comp_avail else None)
            )

            requires_approval = (
                spec.requires_approval
                if spec.requires_approval is not None
                else (
                    spec.approval_required
                    if spec.approval_required is not None
                    else tool_def.requires_approval
                )
            )

            # Rule 2: If an irreversible step appears before other side-effecting steps,
            # require explicit user approval before execution.
            if not is_rev and idx < total_steps - 1:
                requires_approval = True

            step_id = (
                spec.step_id
                if (spec.step_id and spec.step_id not in used_step_ids)
                else generate_step_id()
            )
            used_step_ids.add(step_id)

            step_args = strip_chain_of_thought(dict(spec.inputs or spec.arguments))
            try:
                tool_def.validate_inputs(step_args)
            except (ValueError, TypeError) as exc:
                raise PlanValidationError(
                    f"Invalid arguments for tool '{spec.tool_name}' at step {idx + 1}: {exc}",
                    code="INVALID_ARGUMENTS",
                    details={"step_number": idx + 1, "tool": spec.tool_name},
                ) from exc

            step = StepExecution(
                step_id=step_id,
                transaction_id=tx.transaction_id,
                step_index=idx,
                tool_name=spec.tool_name,
                description=spec.description or tool_def.description,
                arguments=step_args,
                inputs=step_args,
                status=ExecutionStatus.PENDING,
                execution_status=ExecutionStatus.PENDING,
                compensation_available=comp_avail,
                compensation_tool=comp_tool,
                irreversible=not is_rev,
                is_reversible=is_rev,
                approval_required=requires_approval,
                requires_approval=requires_approval,
            )
            steps.append(step)

        tx.steps = steps
        raw_decision_meta = (
            plan.decision_metadata
            or plan.metadata.get("decision_metadata")
            or {
                "planner": "TransactionManager",
                "goal": plan.goal,
                "selected_tools": [s.tool_name for s in steps],
                "step_count": len(steps),
                "has_irreversible_steps": any(s.irreversible for s in steps),
                "requires_approval": any(s.requires_approval for s in steps),
                "validation_status": "VALIDATED",
            }
        )
        tx.decision_metadata = strip_chain_of_thought(raw_decision_meta)

        self._record_event(
            tx,
            event_type=LogEventType.TRANSACTION_CREATED,
            status=ExecutionStatus.PENDING,
            message=f"Transaction created for goal: {plan.goal}",
            payload={
                "plan_id": plan.plan_id,
                "step_count": len(steps),
                "step_ids": [s.step_id for s in steps],
                "decision_metadata": tx.decision_metadata,
            },
        )
        self.durable_log.save_transaction(tx)
        return tx

    def execute_plan(
        self,
        plan: PlanInputType,
        *,
        transaction_id: Optional[str] = None,
        raise_on_crash: bool = False,
        reorder_irreversible_last: bool = False,
    ) -> TransactionState:
        """Create a transaction for `plan` and execute it through the Saga state machine."""
        return self.execute_transaction(
            plan,
            transaction_id=transaction_id,
            raise_on_crash=raise_on_crash,
            reorder_irreversible_last=reorder_irreversible_last,
        )

    def execute_transaction(
        self,
        plan_or_transaction_id: Union[PlanInputType, TransactionState, str],
        *,
        transaction_id: Optional[str] = None,
        raise_on_crash: bool = False,
        reorder_irreversible_last: bool = False,
    ) -> TransactionState:
        """
        Execute an ordered workflow plan or resume an existing transaction by ID.

        - If every step succeeds: transaction status = COMPLETED (emits TRANSACTION_COMPLETED)
        - If any step fails: transaction status = COMPENSATING -> compensates completed steps
          in strict reverse order -> COMPENSATED (or PARTIAL_ROLLBACK / ROLLBACK_FAILED).
        """
        if isinstance(plan_or_transaction_id, str):
            tx = self.durable_log.get_transaction(plan_or_transaction_id)
        elif isinstance(plan_or_transaction_id, TransactionState):
            if not self.durable_log.has_transaction(
                plan_or_transaction_id.transaction_id
            ):
                self.durable_log.save_transaction(plan_or_transaction_id)
            tx = self.durable_log.get_transaction(
                plan_or_transaction_id.transaction_id
            )
        else:
            tx = self.create_transaction(
                plan_or_transaction_id,
                transaction_id=transaction_id,
                reorder_irreversible_last=reorder_irreversible_last,
            )

        if len(tx.steps) == 0:
            tx.transition_to(ExecutionStatus.RUNNING)
            tx.transition_to(ExecutionStatus.COMPLETED)
            tx.world_snapshot_after = self.world.snapshot()
            self._record_event(
                tx,
                event_type=LogEventType.TRANSACTION_COMPLETED,
                status=ExecutionStatus.COMPLETED,
                message="Empty workflow plan completed immediately",
            )
            self.durable_log.save_transaction(tx)
            return tx

        if tx.status != ExecutionStatus.RUNNING:
            tx.transition_to(ExecutionStatus.RUNNING)
            self._record_event(
                tx,
                event_type=LogEventType.TRANSACTION_STATUS_CHANGED,
                status=ExecutionStatus.RUNNING,
                message="Transaction started/resumed forward execution",
            )
            self.durable_log.save_transaction(tx)

        for step in tx.steps:
            if step.status == ExecutionStatus.COMPLETED:
                continue

            # Check if human approval is required for this step
            if step.requires_approval and not self.approval_manager.is_step_approved(
                step.step_id
            ):
                approval_reason = (
                    "This action cannot be undone."
                    if step.irreversible
                    else "Explicit user approval required before execution."
                )
                existing_req = self.approval_manager.get_pending_for_transaction(
                    tx.transaction_id
                )
                if existing_req is None or existing_req.step_id != step.step_id:
                    existing_req = self.approval_manager.create_request(
                        transaction_id=tx.transaction_id,
                        step_id=step.step_id,
                        step_index=step.step_index,
                        tool_name=step.tool_name,
                        inputs=step.inputs,
                        reason=approval_reason,
                    )
                step.approval_id = existing_req.approval_id
                step.status = ExecutionStatus.WAITING_FOR_APPROVAL
                tx.approval_request = existing_req.to_prompt()
                tx.transition_to(ExecutionStatus.WAITING_FOR_APPROVAL)
                self._record_event(
                    tx,
                    event_type=LogEventType.APPROVAL_REQUESTED,
                    status=ExecutionStatus.WAITING_FOR_APPROVAL,
                    step_id=step.step_id,
                    message=f"Step {step.step_index} ({step.tool_name}) is waiting for user approval",
                    payload={
                        "approval_id": existing_req.approval_id,
                        "step_index": step.step_index,
                        "tool_name": step.tool_name,
                        "inputs": step.inputs,
                        "approval_request": tx.approval_request,
                    },
                )
                self.durable_log.save_transaction(tx)
                return tx

            # Clear pending approval prompt once approved and executing
            tx.approval_request = None

            # Execute step
            step.status = ExecutionStatus.RUNNING
            step.attempt_count += 1
            step.started_at = utc_now_iso()
            self._record_event(
                tx,
                event_type=LogEventType.STEP_STARTED,
                status=ExecutionStatus.RUNNING,
                step_id=step.step_id,
                message=f"Executing step {step.step_index} ({step.tool_name})",
                payload={
                    "step_index": step.step_index,
                    "tool_name": step.tool_name,
                    "arguments": step.arguments,
                },
            )
            self.durable_log.save_transaction(tx)

            try:
                outputs = self.tool_executor.execute(
                    step.tool_name,
                    step.inputs,
                    transaction_id=tx.transaction_id,
                    step_index=step.step_index,
                    step_id=step.step_id,
                )
            except SimulatedProcessCrash as crash_exc:
                # Side effect completed before the simulated crash; persist step completion
                step.outputs = crash_exc.execution_outputs
                step.status = ExecutionStatus.COMPLETED
                step.completed_at = utc_now_iso()
                tx.error = f"Process crashed after step {step.step_index} ({step.tool_name}): {crash_exc}"
                self._record_event(
                    tx,
                    event_type=LogEventType.STEP_COMPLETED,
                    status=ExecutionStatus.COMPLETED,
                    step_id=step.step_id,
                    message=f"Step {step.step_index} ({step.tool_name}) completed prior to crash",
                    payload={
                        "step_index": step.step_index,
                        "tool_name": step.tool_name,
                        "outputs": step.outputs,
                    },
                )
                # Transaction remains in RUNNING status in the durable log so RecoveryManager can detect it
                self.durable_log.save_transaction(tx)
                if raise_on_crash:
                    raise
                return tx
            except Exception as exc:
                step.status = ExecutionStatus.FAILED
                step.error = str(exc)
                step.completed_at = utc_now_iso()
                failure_msg = (
                    f"Step {step.step_index} ({step.tool_name}) failed: {exc}"
                )
                tx.error = failure_msg
                tx.transition_to(ExecutionStatus.COMPENSATING)

                self._record_event(
                    tx,
                    event_type=LogEventType.STEP_FAILED,
                    status=ExecutionStatus.FAILED,
                    step_id=step.step_id,
                    message=failure_msg,
                    payload={
                        "step_index": step.step_index,
                        "tool_name": step.tool_name,
                        "error": str(exc),
                    },
                )
                self.durable_log.save_transaction(tx)

                # Trigger reverse-order Saga compensation for all previously completed steps.
                # Note: the failed step itself has status=FAILED and is never compensated.
                try:
                    return self.compensation_manager.compensate_transaction(
                        tx, reason=failure_msg
                    )
                except SimulatedProcessCrash:
                    if raise_on_crash:
                        raise
                    return self.durable_log.load_transaction(tx.transaction_id)

            # Step succeeded
            step.outputs = outputs
            step.status = ExecutionStatus.COMPLETED
            step.completed_at = utc_now_iso()
            self._record_event(
                tx,
                event_type=LogEventType.STEP_COMPLETED,
                status=ExecutionStatus.COMPLETED,
                step_id=step.step_id,
                message=f"Step {step.step_index} ({step.tool_name}) completed successfully",
                payload={
                    "step_index": step.step_index,
                    "tool_name": step.tool_name,
                    "outputs": outputs,
                },
            )
            self.durable_log.save_transaction(tx)

        # All steps succeeded
        tx.transition_to(ExecutionStatus.COMPLETED)
        tx.world_snapshot_after = self.world.snapshot()
        self._record_event(
            tx,
            event_type=LogEventType.TRANSACTION_COMPLETED,
            status=ExecutionStatus.COMPLETED,
            message="All workflow steps completed successfully",
        )
        self.durable_log.save_transaction(tx)
        return tx

    def resolve_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
        reason: Optional[str] = None,
    ) -> TransactionState:
        """Approve or reject a waiting approval gate and resume or abort/compensate the transaction."""
        if approved:
            req = self.approval_manager.approve(approval_id, reason=reason)
        else:
            req = self.approval_manager.reject(approval_id, reason=reason)

        tx = self.durable_log.get_transaction(req.transaction_id)
        tx.approval_request = None
        self._record_event(
            tx,
            event_type=LogEventType.APPROVAL_RESOLVED,
            status=tx.status,
            step_id=req.step_id,
            message=f"Approval {approval_id} resolved as {req.status.value}",
            payload={
                "approval_id": approval_id,
                "approved": approved,
                "reason": req.reason,
            },
        )
        self.durable_log.save_transaction(tx)

        if approved:
            return self.execute_transaction(tx.transaction_id)

        # Rejected: mark waiting step ABORTED, compensate any earlier completed steps, and end ABORTED
        for step in tx.steps:
            if step.step_id == req.step_id:
                step.status = ExecutionStatus.ABORTED
                step.error = req.reason
                step.completed_at = utc_now_iso()
                break

        tx.error = f"Approval rejected for step {req.step_index} ({req.tool_name}): {req.reason}"
        if len(tx.completed_steps) > 0:
            self.durable_log.save_transaction(tx)
            return self.compensation_manager.compensate_transaction(
                tx,
                reason=tx.error,
                final_status_if_clean=ExecutionStatus.ABORTED,
            )

        tx.transition_to(ExecutionStatus.ABORTED)
        tx.world_snapshot_after = self.world.snapshot()
        tx.world_restored = True
        tx.restoration_status = "FULLY_RESTORED"
        self._record_event(
            tx,
            event_type=LogEventType.TRANSACTION_STATUS_CHANGED,
            status=ExecutionStatus.ABORTED,
            message=tx.error,
        )
        self.durable_log.save_transaction(tx)
        return tx

    def undo_transaction(
        self,
        transaction_id: str,
        *,
        reason: str = "Manual Undo Button triggered by user",
    ) -> TransactionState:
        """Undo a COMPLETED transaction by compensating all completed steps in reverse order."""
        tx = self.durable_log.get_transaction(transaction_id)
        if tx.status != ExecutionStatus.COMPLETED:
            raise ValueError(
                f"Cannot undo transaction '{transaction_id}' with status {tx.status.value}; expected COMPLETED"
            )
        return self.compensation_manager.compensate_transaction(tx, reason=reason)

    def abort_transaction(
        self,
        transaction_id: str,
        *,
        reason: str = "Transaction aborted by user",
    ) -> TransactionState:
        """Abort a pending, running, or waiting transaction, rolling back any completed steps."""
        tx = self.durable_log.get_transaction(transaction_id)
        if tx.status in {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.COMPENSATED,
            ExecutionStatus.PARTIAL_ROLLBACK,
            ExecutionStatus.ABORTED,
        }:
            raise ValueError(
                f"Cannot abort transaction '{transaction_id}' in terminal status {tx.status.value}"
            )

        tx.approval_request = None
        for step in tx.steps:
            if step.status in {
                ExecutionStatus.PENDING,
                ExecutionStatus.WAITING_FOR_APPROVAL,
                ExecutionStatus.RUNNING,
            }:
                step.status = ExecutionStatus.ABORTED
                step.error = reason

        tx.error = reason
        if len(tx.completed_steps) > 0:
            self.durable_log.save_transaction(tx)
            return self.compensation_manager.compensate_transaction(
                tx,
                reason=reason,
                final_status_if_clean=ExecutionStatus.ABORTED,
            )

        tx.transition_to(ExecutionStatus.ABORTED)
        tx.world_snapshot_after = self.world.snapshot()
        tx.world_restored = True
        tx.restoration_status = "FULLY_RESTORED"
        self._record_event(
            tx,
            event_type=LogEventType.TRANSACTION_STATUS_CHANGED,
            status=ExecutionStatus.ABORTED,
            message=reason,
        )
        self.durable_log.save_transaction(tx)
        return tx

    def get_transaction(self, transaction_id: str) -> TransactionState:
        """Retrieve a transaction state by ID."""
        return self.durable_log.get_transaction(transaction_id)

    def list_transactions(self) -> list[TransactionState]:
        """List all recorded transactions."""
        return self.durable_log.list_transactions()
