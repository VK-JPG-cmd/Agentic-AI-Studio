"""Deterministic MockWorld state store representing external systems (Hotel Bookings, Payments, Tickets, Emails)."""

from __future__ import annotations

import copy
from typing import Any, Optional


DEFAULT_WORLD_STATE: dict[str, Any] = {
    "bookings": {},
    "payments": {},
    "tickets": {},
    "emails": [],
    "accounts": {
        "user_default": {
            "user_id": "user_default",
            "name": "Alex Rivera",
            "balance": 5000.00,
            "currency": "USD",
        },
        "user_vip": {
            "user_id": "user_vip",
            "name": "Jordan Chen",
            "balance": 25000.00,
            "currency": "USD",
        },
    },
    "flights": {
        "FL-101": {
            "flight_id": "FL-101",
            "origin": "SFO",
            "destination": "JFK",
            "price": 450.00,
            "available_seats": 10,
        },
        "FL-202": {
            "flight_id": "FL-202",
            "origin": "JFK",
            "destination": "LHR",
            "price": 850.00,
            "available_seats": 5,
        },
    },
    "flight_bookings": {},
    "hotels": {
        "HT-GRAND": {
            "hotel_id": "HT-GRAND",
            "name": "Grand Horizon Hotel",
            "city": "New York",
            "nightly_rate": 300.00,
            "available_rooms": 8,
        },
        "HT-PLAZA": {
            "hotel_id": "HT-PLAZA",
            "name": "Mayfair Plaza",
            "city": "London",
            "nightly_rate": 420.00,
            "available_rooms": 4,
        },
    },
    "hotel_reservations": {},
    "payment_charges": {},
    "calendar_events": {},
    "inventory": {
        "ITEM-LAPTOP": {
            "sku": "ITEM-LAPTOP",
            "name": "ThinkPad X1 Carbon",
            "stock": 15,
            "unit_price": 1400.00,
        },
        "ITEM-SERVER": {
            "sku": "ITEM-SERVER",
            "name": "Enterprise Rack Node",
            "stock": 3,
            "unit_price": 4200.00,
        },
    },
    "inventory_holds": {},
    "emails_sent": [],
}


class MockWorld:
    """Deterministic in-memory mocked world for tool execution and Saga compensation."""

    def __init__(self, initial_state: Optional[dict[str, Any]] = None) -> None:
        self._initial_template = copy.deepcopy(initial_state or DEFAULT_WORLD_STATE)
        # Ensure core Phase 3 keys always exist in state
        for key, default_val in (
            ("bookings", {}),
            ("payments", {}),
            ("tickets", {}),
            ("emails", []),
        ):
            if key not in self._initial_template:
                self._initial_template[key] = copy.deepcopy(default_val)
        self.state: dict[str, Any] = copy.deepcopy(self._initial_template)
        self._id_counters: dict[str, int] = {}
        self.operation_history: list[dict[str, Any]] = []

    @property
    def bookings(self) -> dict[str, Any]:
        """Return the active hotel/travel bookings dictionary."""
        return self.state["bookings"]

    @property
    def payments(self) -> dict[str, Any]:
        """Return the active payments dictionary."""
        return self.state["payments"]

    @property
    def tickets(self) -> dict[str, Any]:
        """Return the active support/itinerary tickets dictionary."""
        return self.state["tickets"]

    @property
    def emails(self) -> list[dict[str, Any]]:
        """Return the list of sent emails."""
        return self.state["emails"]

    def reset(self, initial_state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Reset the mocked world back to its deterministic initial state."""
        if initial_state is not None:
            self._initial_template = copy.deepcopy(initial_state)
            for key, default_val in (
                ("bookings", {}),
                ("payments", {}),
                ("tickets", {}),
                ("emails", []),
            ):
                if key not in self._initial_template:
                    self._initial_template[key] = copy.deepcopy(default_val)
        self.state = copy.deepcopy(self._initial_template)
        self._id_counters = {}
        self.operation_history = []
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        """Return a deep copy of the current world state."""
        return copy.deepcopy(self.state)

    def get_state(self) -> dict[str, Any]:
        """Alias for snapshot()."""
        return self.snapshot()

    def is_clean(self, baseline: Optional[dict[str, Any]] = None) -> bool:
        """
        Return True if the current world state matches the initial baseline snapshot
        and contains no lingering uncompensated bookings, payments, tickets, or emails.
        """
        reference = baseline if baseline is not None else self._initial_template
        if self.state != reference:
            return False
        if baseline is None:
            return (
                len(self.state.get("bookings", {})) == 0
                and len(self.state.get("payments", {})) == 0
                and len(self.state.get("tickets", {})) == 0
                and len(self.state.get("emails", [])) == 0
            )
        return True

    def restore_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Restore world state directly from a snapshot (used only for test setup)."""
        self.state = copy.deepcopy(snapshot)

    def equals_snapshot(self, other_snapshot: dict[str, Any]) -> bool:
        """Check whether current world state equals a previously captured snapshot."""
        return self.state == other_snapshot

    def next_deterministic_id(self, prefix: str) -> str:
        """Generate a deterministic sequential identifier for mocked entities."""
        current = self._id_counters.get(prefix, 0) + 1
        self._id_counters[prefix] = current
        return f"{prefix}_{current:04d}"

    def record_operation(
        self, kind: str, tool_name: str, details: dict[str, Any]
    ) -> None:
        """Record an operation in the world's non-state audit history."""
        self.operation_history.append(
            {
                "kind": kind,  # "EXECUTE" or "COMPENSATE"
                "tool_name": tool_name,
                "details": copy.deepcopy(details),
            }
        )

    # =====================================================================
    # Phase 3 Domain Operations on MockWorld
    # =====================================================================

    def create_booking(
        self,
        user: str,
        room: str,
        *,
        booking_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add a hotel room booking to MockWorld.state['bookings']."""
        if not user:
            raise ValueError("user is required for create_booking")
        if not room:
            raise ValueError("room is required for create_booking")

        bid = booking_id or self.next_deterministic_id("booking")
        record = {
            "booking_id": bid,
            "user": user,
            "room": room,
            "status": "CONFIRMED",
        }
        self.state["bookings"][bid] = record
        return copy.deepcopy(record)

    def cancel_booking(self, booking_id: str) -> dict[str, Any]:
        """Remove a hotel booking from MockWorld.state['bookings']."""
        if booking_id not in self.state["bookings"]:
            raise RuntimeError(
                f"Booking '{booking_id}' not found in MockWorld.bookings"
            )
        removed = self.state["bookings"].pop(booking_id)
        return {
            "cancelled_booking_id": booking_id,
            "user": removed.get("user"),
            "room": removed.get("room"),
            "status": "CANCELLED",
        }

    def charge_payment(
        self,
        user: str,
        amount: float,
        *,
        payment_id: Optional[str] = None,
        description: str = "Workflow payment",
    ) -> dict[str, Any]:
        """Add a payment charge to MockWorld.state['payments'] (and update account balance if present)."""
        amount_val = float(amount)
        if amount_val <= 0:
            raise ValueError("Payment amount must be positive")

        accounts = self.state.get("accounts", {})
        if user in accounts:
            account = accounts[user]
            if account["balance"] < amount_val:
                raise RuntimeError(
                    f"Insufficient funds for '{user}': balance {account['balance']}, charge {amount_val}"
                )
            account["balance"] = round(account["balance"] - amount_val, 2)

        pid = payment_id or self.next_deterministic_id("payment")
        record = {
            "payment_id": pid,
            "charge_id": pid,
            "user": user,
            "user_id": user,
            "amount": amount_val,
            "description": description,
            "status": "CHARGED",
        }
        self.state["payments"][pid] = record
        if "payment_charges" in self.state:
            self.state["payment_charges"][pid] = copy.deepcopy(record)
        return copy.deepcopy(record)

    def refund_payment(self, payment_id: str) -> dict[str, Any]:
        """Remove/refund a payment from MockWorld.state['payments'] (and restore account balance if present)."""
        if payment_id not in self.state["payments"]:
            raise RuntimeError(
                f"Payment '{payment_id}' not found in MockWorld.payments"
            )
        removed = self.state["payments"].pop(payment_id)
        if "payment_charges" in self.state and payment_id in self.state["payment_charges"]:
            del self.state["payment_charges"][payment_id]

        user = removed.get("user") or removed.get("user_id")
        amount_val = float(removed["amount"])
        accounts = self.state.get("accounts", {})
        new_balance = None
        if user and user in accounts:
            accounts[user]["balance"] = round(
                accounts[user]["balance"] + amount_val, 2
            )
            new_balance = accounts[user]["balance"]

        return {
            "refunded_payment_id": payment_id,
            "refunded_charge_id": payment_id,
            "user": user,
            "refunded_amount": amount_val,
            "new_balance": new_balance,
            "status": "REFUNDED",
        }

    def create_ticket(
        self,
        user: str,
        issue: str,
        *,
        ticket_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add a ticket to MockWorld.state['tickets']."""
        if not user:
            raise ValueError("user is required for create_ticket")
        if not issue:
            raise ValueError("issue is required for create_ticket")

        tid = ticket_id or self.next_deterministic_id("ticket")
        record = {
            "ticket_id": tid,
            "user": user,
            "holder_name": user,
            "issue": issue,
            "reference": issue,
            "status": "OPEN",
        }
        self.state["tickets"][tid] = record
        return copy.deepcopy(record)

    def delete_ticket(self, ticket_id: str) -> dict[str, Any]:
        """Remove a ticket from MockWorld.state['tickets']."""
        if ticket_id not in self.state["tickets"]:
            raise RuntimeError(
                f"Ticket '{ticket_id}' not found in MockWorld.tickets"
            )
        self.state["tickets"].pop(ticket_id)
        return {
            "deleted_ticket_id": ticket_id,
            "voided_ticket_id": ticket_id,
            "status": "DELETED",
        }

    def send_email(
        self,
        to: str,
        subject: str,
        body: str = "",
        *,
        email_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Append a sent email to MockWorld.state['emails']. Irreversible operation."""
        if not to:
            raise ValueError("Recipient 'to' is required for send_email")
        if not subject:
            raise ValueError("'subject' is required for send_email")

        eid = email_id or self.next_deterministic_id("email")
        record = {
            "email_id": eid,
            "to": to,
            "subject": subject,
            "body": body,
            "status": "SENT",
        }
        self.state["emails"].append(record)
        return copy.deepcopy(record)
