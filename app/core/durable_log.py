"""SQLite-backed DurableExecutionLog for append-only event logging and crash recovery."""

from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from app.core.models import (
    DurableLogEntry,
    ExecutionStatus,
    LogEventType,
    TransactionState,
    utc_now_iso,
)

SECRET_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "private_key",
    "card_number",
    "credit_card",
    "cvv",
    "cvc",
    "ssn",
    "authorization",
    "client_secret",
)


def is_secret_key(key: str) -> bool:
    """Return True if `key` looks like a sensitive credential or secret field."""
    normalized = key.strip().lower()
    return any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS)


def redact_secrets(value: Any) -> Any:
    """Recursively redact sensitive fields so secrets are never stored in plaintext."""
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for k, v in value.items():
            if is_secret_key(str(k)):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = redact_secrets(v)
        return sanitized
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]
    return value


class DurableExecutionLog:
    """
    SQLite-backed durable execution log and transaction state store for AG02.

    Persists:
    - `transactions` table (overall transaction state and snapshots)
    - `transaction_steps` table (transaction_id, step_id, step_number, tool_name,
      status, arguments, result_metadata, compensation_tool, compensation_status,
      created_at, updated_at)
    - `execution_events` table (append-only state transition log)
    """

    def __init__(self, storage_path: Optional[str | Path] = None) -> None:
        self.storage_path: Optional[Path] = (
            Path(storage_path) if storage_path is not None else None
        )
        self._memory_conn: Optional[sqlite3.Connection] = None
        if self.storage_path is None:
            self._memory_conn = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._memory_conn.row_factory = sqlite3.Row
        else:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._memory_conn is not None:
            yield self._memory_conn
            self._memory_conn.commit()
        else:
            assert self.storage_path is not None
            conn = sqlite3.connect(
                str(self.storage_path), check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    transaction_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    user_goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    world_restored INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS transaction_steps (
                    step_id TEXT PRIMARY KEY,
                    transaction_id TEXT NOT NULL,
                    step_number INTEGER NOT NULL,
                    step_index INTEGER NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    result_metadata TEXT NOT NULL,
                    compensation_tool TEXT,
                    compensation_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_steps_tx_id
                    ON transaction_steps(transaction_id, step_number);

                CREATE TABLE IF NOT EXISTS execution_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    log_id TEXT NOT NULL,
                    transaction_id TEXT NOT NULL,
                    step_id TEXT,
                    step_number INTEGER,
                    tool_name TEXT,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    result_metadata TEXT NOT NULL,
                    compensation_tool TEXT,
                    compensation_status TEXT,
                    message TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_tx_id
                    ON execution_events(transaction_id, sequence);
                """
            )

    def save_event(
        self,
        *,
        transaction_id: str,
        event_type: LogEventType | str,
        status: ExecutionStatus | str,
        step_id: Optional[str] = None,
        step_number: Optional[int] = None,
        tool_name: Optional[str] = None,
        arguments: Optional[dict[str, Any]] = None,
        result_metadata: Optional[dict[str, Any]] = None,
        compensation_tool: Optional[str] = None,
        compensation_status: Optional[str] = None,
        message: str = "",
        payload: Optional[dict[str, Any]] = None,
    ) -> DurableLogEntry:
        """Persist a state transition event in SQLite with automatic secret redaction."""
        evt_type = (
            event_type
            if isinstance(event_type, LogEventType)
            else LogEventType(event_type)
        )
        exec_status = (
            status
            if isinstance(status, ExecutionStatus)
            else ExecutionStatus(status)
        )

        safe_payload = redact_secrets(copy.deepcopy(payload or {}))
        safe_args = redact_secrets(
            copy.deepcopy(
                arguments
                if arguments is not None
                else safe_payload.get("arguments", safe_payload.get("inputs", {}))
            )
        )
        safe_result = redact_secrets(
            copy.deepcopy(
                result_metadata
                if result_metadata is not None
                else safe_payload.get(
                    "outputs", safe_payload.get("compensation_outputs", {})
                )
            )
        )

        if step_number is None and "step_index" in safe_payload:
            step_number = int(safe_payload["step_index"]) + 1
        if tool_name is None and "tool_name" in safe_payload:
            tool_name = str(safe_payload["tool_name"])
        if compensation_tool is None and "compensation_tool" in safe_payload:
            compensation_tool = safe_payload["compensation_tool"]
        if compensation_status is None:
            if exec_status in {
                ExecutionStatus.COMPENSATING,
                ExecutionStatus.COMPENSATED,
                ExecutionStatus.ROLLBACK_FAILED,
            }:
                compensation_status = exec_status.value
            else:
                compensation_status = "PENDING"

        now = utc_now_iso()
        entry = DurableLogEntry(
            sequence=0,
            transaction_id=transaction_id,
            step_id=step_id,
            event_type=evt_type,
            status=exec_status,
            message=message,
            payload=safe_payload,
            timestamp=now,
        )

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO execution_events (
                    log_id,
                    transaction_id,
                    step_id,
                    step_number,
                    tool_name,
                    event_type,
                    status,
                    arguments,
                    result_metadata,
                    compensation_tool,
                    compensation_status,
                    message,
                    payload,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.log_id,
                    entry.transaction_id,
                    entry.step_id,
                    step_number,
                    tool_name,
                    entry.event_type.value,
                    entry.status.value,
                    json.dumps(safe_args),
                    json.dumps(safe_result),
                    compensation_tool,
                    compensation_status,
                    entry.message,
                    json.dumps(safe_payload),
                    now,
                    now,
                ),
            )
            entry.sequence = int(cursor.lastrowid or 1)

        return entry

    def append_event(
        self,
        *,
        transaction_id: str,
        event_type: LogEventType,
        status: ExecutionStatus,
        step_id: Optional[str] = None,
        message: str = "",
        payload: Optional[dict[str, Any]] = None,
    ) -> DurableLogEntry:
        """Alias for save_event() preserving Phase 1-4 compatibility."""
        return self.save_event(
            transaction_id=transaction_id,
            event_type=event_type,
            status=status,
            step_id=step_id,
            message=message,
            payload=payload,
        )

    def save_transaction(self, transaction: TransactionState) -> None:
        """Persist TransactionState and all its StepExecution rows into SQLite with secret redaction."""
        tx_copy = copy.deepcopy(transaction)
        now = utc_now_iso()
        tx_copy.updated_at = now

        # Scrub secrets from steps and snapshots before serializing to SQLite
        for step in tx_copy.steps:
            step.arguments = redact_secrets(step.arguments)
            step.inputs = redact_secrets(step.inputs)
            step.outputs = redact_secrets(step.outputs)
            step.result_metadata = redact_secrets(step.result_metadata)
            step.compensation_outputs = redact_secrets(step.compensation_outputs)

        for comp in tx_copy.compensation_history:
            comp.inputs = redact_secrets(comp.inputs)
            comp.outputs = redact_secrets(comp.outputs)

        for evt in tx_copy.events:
            evt.payload = redact_secrets(evt.payload)

        tx_dump = redact_secrets(tx_copy.model_dump())
        state_json = json.dumps(tx_dump)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO transactions (
                    transaction_id,
                    plan_id,
                    user_goal,
                    status,
                    world_restored,
                    error,
                    state_json,
                    created_at,
                    updated_at,
                    completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    status = excluded.status,
                    world_restored = excluded.world_restored,
                    error = excluded.error,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at,
                    completed_at = excluded.completed_at
                """,
                (
                    tx_copy.transaction_id,
                    tx_copy.plan_id,
                    tx_copy.user_goal,
                    tx_copy.status.value,
                    1 if tx_copy.world_restored else 0,
                    tx_copy.error,
                    state_json,
                    tx_copy.created_at,
                    tx_copy.updated_at,
                    tx_copy.completed_at,
                ),
            )

            for step in tx_copy.steps:
                comp_status = step.compensation_status
                if step.status in {
                    ExecutionStatus.COMPENSATING,
                    ExecutionStatus.COMPENSATED,
                    ExecutionStatus.ROLLBACK_FAILED,
                }:
                    comp_status = step.status.value
                elif not step.compensation_available or step.irreversible:
                    comp_status = "NOT_APPLICABLE"

                merged_result_meta = {
                    "outputs": step.outputs,
                    "compensation_outputs": step.compensation_outputs,
                    "error": step.error,
                    "compensation_error": step.compensation_error,
                }
                conn.execute(
                    """
                    INSERT INTO transaction_steps (
                        step_id,
                        transaction_id,
                        step_number,
                        step_index,
                        tool_name,
                        status,
                        arguments,
                        result_metadata,
                        compensation_tool,
                        compensation_status,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(step_id) DO UPDATE SET
                        status = excluded.status,
                        arguments = excluded.arguments,
                        result_metadata = excluded.result_metadata,
                        compensation_tool = excluded.compensation_tool,
                        compensation_status = excluded.compensation_status,
                        updated_at = excluded.updated_at
                    """,
                    (
                        step.step_id,
                        tx_copy.transaction_id,
                        step.step_number,
                        step.step_index,
                        step.tool_name,
                        step.status.value,
                        json.dumps(step.arguments),
                        json.dumps(merged_result_meta),
                        step.compensation_tool,
                        comp_status,
                        step.created_at,
                        step.updated_at,
                    ),
                )

    def load_transaction(self, transaction_id: str) -> TransactionState:
        """Load and reconstruct a TransactionState from SQLite by transaction_id."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM transactions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
            if row is None:
                raise KeyError(
                    f"Transaction '{transaction_id}' not found in DurableExecutionLog"
                )
            tx = TransactionState.model_validate(json.loads(row["state_json"]))

        # Ensure events list reflects all persisted events in SQLite
        persisted_events = self.get_events(transaction_id=transaction_id)
        if persisted_events:
            tx.events = persisted_events
        return tx

    def get_transaction(self, transaction_id: str) -> TransactionState:
        """Alias for load_transaction()."""
        return self.load_transaction(transaction_id)

    def has_transaction(self, transaction_id: str) -> bool:
        """Return True if `transaction_id` exists in SQLite."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM transactions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
            return row is not None

    def list_transactions(self) -> list[TransactionState]:
        """Return all stored transactions ordered by creation."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT transaction_id FROM transactions ORDER BY rowid ASC"
            ).fetchall()
        return [
            self.load_transaction(str(r["transaction_id"])) for r in rows
        ]

    def get_incomplete_transactions(self) -> list[TransactionState]:
        """Return transactions left in an interrupted/in-flight state requiring recovery."""
        interrupted_statuses = (
            ExecutionStatus.RUNNING.value,
            ExecutionStatus.COMPENSATING.value,
            ExecutionStatus.RECOVERING.value,
            ExecutionStatus.FAILED.value,
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT transaction_id
                FROM transactions
                WHERE status IN (?, ?, ?, ?)
                ORDER BY rowid ASC
                """,
                interrupted_statuses,
            ).fetchall()
        return [
            self.load_transaction(str(r["transaction_id"])) for r in rows
        ]

    def get_step_records(self, transaction_id: str) -> list[dict[str, Any]]:
        """Return persisted rows from the `transaction_steps` SQLite table."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    transaction_id,
                    step_id,
                    step_number,
                    step_index,
                    tool_name,
                    status,
                    arguments,
                    result_metadata,
                    compensation_tool,
                    compensation_status,
                    created_at,
                    updated_at
                FROM transaction_steps
                WHERE transaction_id = ?
                ORDER BY step_number ASC
                """,
                (transaction_id,),
            ).fetchall()

        results: list[dict[str, Any]] = []
        for r in rows:
            results.append(
                {
                    "transaction_id": r["transaction_id"],
                    "step_id": r["step_id"],
                    "step_number": r["step_number"],
                    "step_index": r["step_index"],
                    "tool_name": r["tool_name"],
                    "status": r["status"],
                    "arguments": json.loads(r["arguments"]),
                    "result_metadata": json.loads(r["result_metadata"]),
                    "compensation_tool": r["compensation_tool"],
                    "compensation_status": r["compensation_status"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
            )
        return results

    def get_events(
        self, transaction_id: Optional[str] = None
    ) -> list[DurableLogEntry]:
        """Return durable log events, optionally filtered by transaction_id."""
        with self._connect() as conn:
            if transaction_id is None:
                rows = conn.execute(
                    """
                    SELECT sequence, log_id, transaction_id, step_id, event_type,
                           status, message, payload, created_at
                    FROM execution_events
                    ORDER BY sequence ASC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT sequence, log_id, transaction_id, step_id, event_type,
                           status, message, payload, created_at
                    FROM execution_events
                    WHERE transaction_id = ?
                    ORDER BY sequence ASC
                    """,
                    (transaction_id,),
                ).fetchall()

        entries: list[DurableLogEntry] = []
        for r in rows:
            entries.append(
                DurableLogEntry(
                    sequence=int(r["sequence"]),
                    log_id=r["log_id"],
                    transaction_id=r["transaction_id"],
                    step_id=r["step_id"],
                    event_type=LogEventType(r["event_type"]),
                    status=ExecutionStatus(r["status"]),
                    message=r["message"],
                    payload=json.loads(r["payload"]),
                    timestamp=r["created_at"],
                )
            )
        return entries

    def clear(self) -> None:
        """Clear all entries, steps, and transactions from SQLite."""
        with self._connect() as conn:
            conn.executescript(
                """
                DELETE FROM execution_events;
                DELETE FROM transaction_steps;
                DELETE FROM transactions;
                """
            )
