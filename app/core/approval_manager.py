"""ApprovalManager for gating high-risk or irreversible tool steps."""

from __future__ import annotations

import copy
from typing import Any, Optional

from app.core.models import ApprovalRequest, ApprovalStatus, utc_now_iso


class ApprovalManager:
    """Manages human-in-the-loop approval requests for AG02 workflow steps."""

    def __init__(self) -> None:
        self._requests: dict[str, ApprovalRequest] = {}

    def create_request(
        self,
        *,
        transaction_id: str,
        step_id: str,
        step_index: int,
        tool_name: str,
        inputs: dict[str, Any],
        reason: str = "This action cannot be undone.",
    ) -> ApprovalRequest:
        """Create and store a PENDING approval request for a step."""
        request = ApprovalRequest(
            transaction_id=transaction_id,
            step_id=step_id,
            step_index=step_index,
            tool=tool_name,
            tool_name=tool_name,
            inputs=copy.deepcopy(inputs),
            status=ApprovalStatus.PENDING,
            reason=reason,
        )
        self._requests[request.approval_id] = request
        return copy.deepcopy(request)

    def get_request(self, approval_id: str) -> ApprovalRequest:
        """Retrieve an ApprovalRequest by ID or raise KeyError."""
        if approval_id not in self._requests:
            raise KeyError(f"Approval request '{approval_id}' not found")
        return copy.deepcopy(self._requests[approval_id])

    def get_pending_for_transaction(
        self, transaction_id: str
    ) -> Optional[ApprovalRequest]:
        """Return the active PENDING approval request for a transaction, if any."""
        for req in self._requests.values():
            if (
                req.transaction_id == transaction_id
                and req.status == ApprovalStatus.PENDING
            ):
                return copy.deepcopy(req)
        return None

    def approve(
        self, approval_id: str, reason: Optional[str] = None
    ) -> ApprovalRequest:
        """Mark a pending approval request as APPROVED."""
        if approval_id not in self._requests:
            raise KeyError(f"Approval request '{approval_id}' not found")
        req = self._requests[approval_id]
        if req.status != ApprovalStatus.PENDING:
            raise ValueError(
                f"Approval request '{approval_id}' is already resolved as {req.status.value}"
            )
        req.status = ApprovalStatus.APPROVED
        req.reason = reason or "Approved by user"
        req.resolved_at = utc_now_iso()
        return copy.deepcopy(req)

    def reject(
        self, approval_id: str, reason: Optional[str] = None
    ) -> ApprovalRequest:
        """Mark a pending approval request as REJECTED."""
        if approval_id not in self._requests:
            raise KeyError(f"Approval request '{approval_id}' not found")
        req = self._requests[approval_id]
        if req.status != ApprovalStatus.PENDING:
            raise ValueError(
                f"Approval request '{approval_id}' is already resolved as {req.status.value}"
            )
        req.status = ApprovalStatus.REJECTED
        req.reason = reason or "Rejected by user"
        req.resolved_at = utc_now_iso()
        return copy.deepcopy(req)

    def is_step_approved(self, step_id: str) -> bool:
        """Check whether a given step_id has an APPROVED request."""
        return any(
            r.step_id == step_id and r.status == ApprovalStatus.APPROVED
            for r in self._requests.values()
        )

    def list_requests(
        self,
        *,
        status: Optional[ApprovalStatus] = None,
        transaction_id: Optional[str] = None,
    ) -> list[ApprovalRequest]:
        """List approval requests with optional status and transaction filters."""
        results = list(self._requests.values())
        if status is not None:
            results = [r for r in results if r.status == status]
        if transaction_id is not None:
            results = [r for r in results if r.transaction_id == transaction_id]
        return [copy.deepcopy(r) for r in results]

    def clear(self) -> None:
        """Clear all stored approval requests."""
        self._requests.clear()
