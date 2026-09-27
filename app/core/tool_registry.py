"""ToolRegistry, ToolDefinition, ToolExecutor, and mocked tools for AG02."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.core.fault_injector import FaultInjector, FaultTargetPhase
from app.core.mock_world import MockWorld

ExecuteCallable = Callable[[MockWorld, dict[str, Any]], dict[str, Any]]
CompensateCallable = Callable[
    [MockWorld, dict[str, Any], dict[str, Any]], dict[str, Any]
]

JSON_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


@dataclass
class ToolDefinition:
    """Declarative specification for a side-effecting tool in AG02."""

    name: str
    description: str
    input_schema: dict[str, Any]
    execute_action: ExecuteCallable
    is_reversible: bool = True
    compensate_action: Optional[CompensateCallable] = None
    compensation_tool: Optional[str] = None
    requires_approval: bool = False

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Tool name must be a non-empty string")
        if not self.description or not self.description.strip():
            raise ValueError(f"Tool '{self.name}' must provide a description")
        if not isinstance(self.input_schema, dict):
            raise ValueError(f"Tool '{self.name}' must provide a valid input_schema dict")
        if not callable(self.execute_action):
            raise ValueError(f"Tool '{self.name}' execute_action must be callable")
        if self.is_reversible and not callable(self.compensate_action):
            raise ValueError(
                f"Reversible tool '{self.name}' must declare a callable compensate_action"
            )
        if self.is_reversible and not self.compensation_tool:
            self.compensation_tool = f"compensate_{self.name}"
        elif not self.is_reversible:
            self.compensation_tool = None

    @property
    def reversible(self) -> bool:
        """Alias for is_reversible."""
        return self.is_reversible

    @property
    def irreversible(self) -> bool:
        """True if tool cannot be compensated."""
        return not self.is_reversible

    @property
    def compensation_available(self) -> bool:
        """True if tool is reversible and has a callable compensation handler."""
        return bool(self.is_reversible and callable(self.compensate_action))

    @property
    def compensation_action(self) -> Optional[CompensateCallable]:
        """Alias for compensate_action."""
        return self.compensate_action

    @property
    def approval_required(self) -> bool:
        """Alias for requires_approval."""
        return self.requires_approval

    def validate_inputs(self, inputs: dict[str, Any]) -> None:
        """Validate tool inputs against the declared JSON-style input_schema."""
        if not isinstance(inputs, dict):
            raise ValueError(f"Inputs for tool '{self.name}' must be a dictionary")

        required_fields = self.input_schema.get("required", [])
        for req in required_fields:
            if req not in inputs or inputs[req] is None:
                raise ValueError(
                    f"Missing required input '{req}' for tool '{self.name}'"
                )

        properties = self.input_schema.get("properties", {})
        for key, value in inputs.items():
            if key in properties and value is not None:
                expected_type_name = properties[key].get("type")
                if expected_type_name in JSON_TYPE_MAP:
                    # In Python, bool is a subclass of int; guard against bool passed for number/integer
                    if expected_type_name in {"number", "integer"} and isinstance(
                        value, bool
                    ):
                        raise ValueError(
                            f"Invalid type for '{key}' in tool '{self.name}': expected {expected_type_name}, got bool"
                        )
                    expected_types = JSON_TYPE_MAP[expected_type_name]
                    if not isinstance(value, expected_types):
                        raise ValueError(
                            f"Invalid type for '{key}' in tool '{self.name}': expected {expected_type_name}, got {type(value).__name__}"
                        )

    def to_metadata(self) -> dict[str, Any]:
        """Return serializable metadata describing this tool."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "is_reversible": self.is_reversible,
            "irreversible": self.irreversible,
            "compensation_available": self.compensation_available,
            "compensation_tool": self.compensation_tool,
            "has_compensation": self.compensate_action is not None,
            "requires_approval": self.requires_approval,
            "approval_required": self.requires_approval,
        }


class ToolRegistry:
    """Registry managing all side-effecting tools and their compensation handlers."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> ToolDefinition:
        """Register a ToolDefinition in the registry."""
        self._tools[tool.name] = tool
        return tool

    def register_tool(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        execute_action: ExecuteCallable,
        is_reversible: bool = True,
        compensate_action: Optional[CompensateCallable] = None,
        compensation_tool: Optional[str] = None,
        requires_approval: bool = False,
    ) -> ToolDefinition:
        """Create and register a tool from keyword arguments."""
        tool = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            execute_action=execute_action,
            is_reversible=is_reversible,
            compensate_action=compensate_action,
            compensation_tool=compensation_tool,
            requires_approval=requires_approval,
        )
        return self.register(tool)

    def get(self, name: str) -> ToolDefinition:
        """Retrieve a tool by name or raise KeyError."""
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' is not registered in ToolRegistry")
        return self._tools[name]

    def has(self, name: str) -> bool:
        """Return True if a tool with `name` is registered."""
        return name in self._tools

    def list_tools(self) -> list[ToolDefinition]:
        """Return all registered ToolDefinitions."""
        return list(self._tools.values())

    def list_metadata(self) -> list[dict[str, Any]]:
        """Return serializable metadata for all registered tools."""
        return [tool.to_metadata() for tool in self._tools.values()]


class ToolExecutor:
    """Executes tool forward actions and compensation actions against MockWorld."""

    def __init__(
        self,
        registry: ToolRegistry,
        world: MockWorld,
        fault_injector: Optional[FaultInjector] = None,
    ) -> None:
        self.registry = registry
        self.world = world
        self.fault_injector = fault_injector

    def execute(
        self,
        tool_name: str,
        inputs: dict[str, Any],
        *,
        transaction_id: Optional[str] = None,
        step_index: Optional[int] = None,
        step_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Validate inputs, check FaultInjector, and execute the tool's forward action."""
        tool = self.registry.get(tool_name)
        tool.validate_inputs(inputs)

        if self.fault_injector is not None:
            self.fault_injector.check(
                FaultTargetPhase.EXECUTE,
                transaction_id=transaction_id,
                tool_name=tool_name,
                step_index=step_index,
                step_id=step_id,
            )

        output = tool.execute_action(self.world, inputs)
        self.world.record_operation(
            "EXECUTE",
            tool_name,
            {
                "transaction_id": transaction_id,
                "step_index": step_index,
                "step_id": step_id,
                "inputs": inputs,
                "outputs": output,
            },
        )

        if self.fault_injector is not None:
            try:
                self.fault_injector.check(
                    FaultTargetPhase.CRASH_AFTER_EXECUTE,
                    transaction_id=transaction_id,
                    tool_name=tool_name,
                    step_index=step_index,
                    step_id=step_id,
                )
            except Exception as crash_exc:
                if hasattr(crash_exc, "execution_outputs"):
                    crash_exc.execution_outputs = output
                raise

        return output

    def compensate(
        self,
        tool_name: str,
        inputs: dict[str, Any],
        execution_outputs: dict[str, Any],
        *,
        transaction_id: Optional[str] = None,
        step_index: Optional[int] = None,
        step_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Execute a tool's compensation/undo action against MockWorld."""
        tool = self.registry.get(tool_name)
        if not tool.is_reversible or tool.compensate_action is None:
            raise RuntimeError(
                f"Tool '{tool_name}' is declared irreversible and cannot be compensated"
            )

        if self.fault_injector is not None:
            # Check fault rules targeting either the forward tool name or the compensation_tool name
            self.fault_injector.check(
                FaultTargetPhase.COMPENSATE,
                transaction_id=transaction_id,
                tool_name=tool_name,
                step_index=step_index,
                step_id=step_id,
            )
            if tool.compensation_tool and tool.compensation_tool != tool_name:
                self.fault_injector.check(
                    FaultTargetPhase.COMPENSATE,
                    transaction_id=transaction_id,
                    tool_name=tool.compensation_tool,
                    step_index=step_index,
                    step_id=step_id,
                )

        comp_output = tool.compensate_action(self.world, inputs, execution_outputs)
        self.world.record_operation(
            "COMPENSATE",
            tool_name,
            {
                "transaction_id": transaction_id,
                "compensation_tool": tool.compensation_tool,
                "step_index": step_index,
                "step_id": step_id,
                "inputs": inputs,
                "outputs": comp_output,
            },
        )

        if self.fault_injector is not None:
            try:
                self.fault_injector.check(
                    FaultTargetPhase.CRASH_DURING_COMPENSATE,
                    transaction_id=transaction_id,
                    tool_name=tool_name,
                    step_index=step_index,
                    step_id=step_id,
                )
            except Exception as crash_exc:
                if hasattr(crash_exc, "execution_outputs"):
                    crash_exc.execution_outputs = comp_output
                raise

        return comp_output


# =====================================================================
# Phase 3 Standalone Mocked Tool Functions
# =====================================================================


def create_booking(
    world: MockWorld,
    user: str,
    room: str,
    *,
    booking_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create a hotel room booking in MockWorld."""
    return world.create_booking(user=user, room=room, booking_id=booking_id)


def cancel_booking(world: MockWorld, booking_id: str) -> dict[str, Any]:
    """Cancel a hotel room booking in MockWorld."""
    return world.cancel_booking(booking_id=booking_id)


def charge_payment(
    world: MockWorld,
    user: str,
    amount: float,
    *,
    payment_id: Optional[str] = None,
    description: str = "Workflow payment",
) -> dict[str, Any]:
    """Charge a payment in MockWorld."""
    return world.charge_payment(
        user=user, amount=amount, payment_id=payment_id, description=description
    )


def refund_payment(world: MockWorld, payment_id: str) -> dict[str, Any]:
    """Refund a payment in MockWorld."""
    return world.refund_payment(payment_id=payment_id)


def create_ticket(
    world: MockWorld,
    user: str,
    issue: str,
    *,
    ticket_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create a support/itinerary ticket in MockWorld."""
    return world.create_ticket(user=user, issue=issue, ticket_id=ticket_id)


def delete_ticket(world: MockWorld, ticket_id: str) -> dict[str, Any]:
    """Delete a ticket in MockWorld."""
    return world.delete_ticket(ticket_id=ticket_id)


def send_email(
    world: MockWorld,
    to: str,
    subject: str,
    body: str = "",
    *,
    email_id: Optional[str] = None,
) -> dict[str, Any]:
    """Send an irreversible email in MockWorld."""
    return world.send_email(to=to, subject=subject, body=body, email_id=email_id)


# =====================================================================
# Deterministic ToolRegistry Action Handlers
# =====================================================================


def _execute_book_flight(world: MockWorld, inputs: dict[str, Any]) -> dict[str, Any]:
    flight_id = inputs.get("flight_id", "FL-101")
    passenger_name = inputs.get("passenger_name", "Alex Rivera")
    seats = int(inputs.get("seats", 1))

    flights = world.state["flights"]
    if flight_id not in flights:
        raise ValueError(f"Flight '{flight_id}' does not exist")
    flight = flights[flight_id]
    if flight["available_seats"] < seats:
        raise RuntimeError(
            f"Insufficient seats on flight '{flight_id}': requested {seats}, available {flight['available_seats']}"
        )

    booking_id = inputs.get("booking_id") or world.next_deterministic_id("bk_flight")
    flight["available_seats"] -= seats
    booking_record = {
        "booking_id": booking_id,
        "flight_id": flight_id,
        "passenger_name": passenger_name,
        "seats": seats,
        "total_price": round(flight["price"] * seats, 2),
        "status": "CONFIRMED",
    }
    world.state["flight_bookings"][booking_id] = booking_record
    return booking_record


def _compensate_book_flight(
    world: MockWorld, inputs: dict[str, Any], outputs: dict[str, Any]
) -> dict[str, Any]:
    booking_id = outputs["booking_id"]
    flight_id = outputs.get("flight_id", inputs.get("flight_id", "FL-101"))
    seats = int(outputs.get("seats", inputs.get("seats", 1)))

    if booking_id not in world.state["flight_bookings"]:
        raise RuntimeError(f"Flight booking '{booking_id}' not found for cancellation")

    del world.state["flight_bookings"][booking_id]
    world.state["flights"][flight_id]["available_seats"] += seats
    return {
        "cancelled_booking_id": booking_id,
        "flight_id": flight_id,
        "restored_seats": seats,
        "status": "CANCELLED",
    }


def _execute_create_booking(world: MockWorld, inputs: dict[str, Any]) -> dict[str, Any]:
    # Support both Phase 3 hotel booking (user, room) and flight_id inputs
    if "flight_id" in inputs and "room" not in inputs and "user" not in inputs:
        return _execute_book_flight(world, inputs)

    user = (
        inputs.get("user")
        or inputs.get("guest_name")
        or inputs.get("passenger_name")
        or "user_default"
    )
    room = inputs.get("room") or inputs.get("hotel_id") or "Room-101"
    return world.create_booking(
        user=str(user),
        room=str(room),
        booking_id=inputs.get("booking_id"),
    )


def _compensate_create_booking(
    world: MockWorld, inputs: dict[str, Any], outputs: dict[str, Any]
) -> dict[str, Any]:
    booking_id = outputs.get("booking_id") or inputs.get("booking_id")
    if not booking_id:
        raise RuntimeError("Missing booking_id for cancel_booking")
    if booking_id in world.state.get("bookings", {}):
        return world.cancel_booking(booking_id)
    if booking_id in world.state.get("flight_bookings", {}):
        return _compensate_book_flight(world, inputs, outputs)
    raise RuntimeError(f"Booking '{booking_id}' not found for cancellation")


def _execute_cancel_booking_direct(
    world: MockWorld, inputs: dict[str, Any]
) -> dict[str, Any]:
    booking_id = inputs["booking_id"]
    return world.cancel_booking(booking_id)


def _execute_reserve_hotel(world: MockWorld, inputs: dict[str, Any]) -> dict[str, Any]:
    hotel_id = inputs.get("hotel_id", "HT-GRAND")
    guest_name = inputs.get("guest_name", "Alex Rivera")
    nights = int(inputs.get("nights", 1))
    rooms = int(inputs.get("rooms", 1))

    hotels = world.state["hotels"]
    if hotel_id not in hotels:
        raise ValueError(f"Hotel '{hotel_id}' does not exist")
    hotel = hotels[hotel_id]
    if hotel["available_rooms"] < rooms:
        raise RuntimeError(
            f"Insufficient rooms at hotel '{hotel_id}': requested {rooms}, available {hotel['available_rooms']}"
        )

    reservation_id = inputs.get("reservation_id") or world.next_deterministic_id(
        "res_hotel"
    )
    hotel["available_rooms"] -= rooms
    reservation = {
        "reservation_id": reservation_id,
        "hotel_id": hotel_id,
        "guest_name": guest_name,
        "nights": nights,
        "rooms": rooms,
        "total_cost": round(hotel["nightly_rate"] * nights * rooms, 2),
        "status": "RESERVED",
    }
    world.state["hotel_reservations"][reservation_id] = reservation
    return reservation


def _compensate_reserve_hotel(
    world: MockWorld, inputs: dict[str, Any], outputs: dict[str, Any]
) -> dict[str, Any]:
    reservation_id = outputs["reservation_id"]
    hotel_id = outputs.get("hotel_id", inputs.get("hotel_id", "HT-GRAND"))
    rooms = int(outputs.get("rooms", inputs.get("rooms", 1)))

    if reservation_id not in world.state["hotel_reservations"]:
        raise RuntimeError(
            f"Hotel reservation '{reservation_id}' not found for cancellation"
        )

    del world.state["hotel_reservations"][reservation_id]
    world.state["hotels"][hotel_id]["available_rooms"] += rooms
    return {
        "cancelled_reservation_id": reservation_id,
        "hotel_id": hotel_id,
        "restored_rooms": rooms,
        "status": "CANCELLED",
    }


def _execute_charge_payment(world: MockWorld, inputs: dict[str, Any]) -> dict[str, Any]:
    user = inputs.get("user") or inputs.get("user_id") or "user_default"
    amount = float(inputs["amount"])
    description = inputs.get("description", "Workflow charge")
    payment_id = inputs.get("payment_id") or inputs.get("charge_id")
    return world.charge_payment(
        user=str(user),
        amount=amount,
        payment_id=payment_id,
        description=description,
    )


def _compensate_charge_payment(
    world: MockWorld, inputs: dict[str, Any], outputs: dict[str, Any]
) -> dict[str, Any]:
    payment_id = (
        outputs.get("payment_id")
        or outputs.get("charge_id")
        or inputs.get("payment_id")
        or inputs.get("charge_id")
    )
    if not payment_id:
        raise RuntimeError("Missing payment_id for refund_payment")
    return world.refund_payment(str(payment_id))


def _execute_refund_payment_direct(
    world: MockWorld, inputs: dict[str, Any]
) -> dict[str, Any]:
    payment_id = inputs.get("payment_id") or inputs.get("charge_id")
    if not payment_id:
        raise ValueError("Missing required input 'payment_id' for refund_payment")
    return world.refund_payment(str(payment_id))


def _execute_create_calendar_event(
    world: MockWorld, inputs: dict[str, Any]
) -> dict[str, Any]:
    title = inputs["title"]
    date = inputs["date"]
    attendees = list(inputs.get("attendees", []))

    event_id = inputs.get("event_id") or world.next_deterministic_id("cal_evt")
    event_record = {
        "event_id": event_id,
        "title": title,
        "date": date,
        "attendees": attendees,
        "status": "SCHEDULED",
    }
    world.state["calendar_events"][event_id] = event_record
    return event_record


def _compensate_create_calendar_event(
    world: MockWorld, inputs: dict[str, Any], outputs: dict[str, Any]
) -> dict[str, Any]:
    event_id = outputs["event_id"]
    if event_id not in world.state["calendar_events"]:
        raise RuntimeError(f"Calendar event '{event_id}' not found for deletion")

    del world.state["calendar_events"][event_id]
    return {
        "deleted_event_id": event_id,
        "status": "DELETED",
    }


def _execute_reserve_inventory(
    world: MockWorld, inputs: dict[str, Any]
) -> dict[str, Any]:
    sku = inputs["sku"]
    quantity = int(inputs["quantity"])

    if quantity <= 0:
        raise ValueError("Quantity must be positive")
    inventory = world.state["inventory"]
    if sku not in inventory:
        raise ValueError(f"SKU '{sku}' does not exist in inventory")
    item = inventory[sku]
    if item["stock"] < quantity:
        raise RuntimeError(
            f"Insufficient stock for SKU '{sku}': requested {quantity}, available {item['stock']}"
        )

    hold_id = inputs.get("hold_id") or world.next_deterministic_id("inv_hold")
    item["stock"] -= quantity
    hold_record = {
        "hold_id": hold_id,
        "sku": sku,
        "quantity": quantity,
        "status": "HELD",
    }
    world.state["inventory_holds"][hold_id] = hold_record
    return hold_record


def _compensate_reserve_inventory(
    world: MockWorld, inputs: dict[str, Any], outputs: dict[str, Any]
) -> dict[str, Any]:
    hold_id = outputs["hold_id"]
    sku = outputs.get("sku", inputs["sku"])
    quantity = int(outputs.get("quantity", inputs["quantity"]))

    if hold_id not in world.state["inventory_holds"]:
        raise RuntimeError(f"Inventory hold '{hold_id}' not found for release")

    del world.state["inventory_holds"][hold_id]
    world.state["inventory"][sku]["stock"] += quantity
    return {
        "released_hold_id": hold_id,
        "sku": sku,
        "restored_quantity": quantity,
        "status": "RELEASED",
    }


def _execute_issue_ticket(world: MockWorld, inputs: dict[str, Any]) -> dict[str, Any]:
    user = (
        inputs.get("user")
        or inputs.get("holder_name")
        or "Alex Rivera"
    )
    issue = (
        inputs.get("issue")
        or inputs.get("reference")
        or "General issue"
    )
    return world.create_ticket(
        user=str(user),
        issue=str(issue),
        ticket_id=inputs.get("ticket_id"),
    )


def _compensate_issue_ticket(
    world: MockWorld, inputs: dict[str, Any], outputs: dict[str, Any]
) -> dict[str, Any]:
    ticket_id = outputs.get("ticket_id") or inputs.get("ticket_id")
    if not ticket_id:
        raise RuntimeError("Missing ticket_id for delete_ticket")
    return world.delete_ticket(str(ticket_id))


def _execute_delete_ticket_direct(
    world: MockWorld, inputs: dict[str, Any]
) -> dict[str, Any]:
    ticket_id = inputs["ticket_id"]
    return world.delete_ticket(str(ticket_id))


def _execute_send_email(world: MockWorld, inputs: dict[str, Any]) -> dict[str, Any]:
    """Irreversible side-effecting email tool."""
    to = inputs.get("to") or inputs.get("recipient")
    if not to:
        raise ValueError("Missing required input 'to' for tool 'send_email'")
    subject = inputs["subject"]
    body = inputs.get("body", "")
    return world.send_email(
        to=str(to),
        subject=str(subject),
        body=str(body),
        email_id=inputs.get("email_id"),
    )


def _execute_high_value_wire_transfer(
    world: MockWorld, inputs: dict[str, Any]
) -> dict[str, Any]:
    """Reversible tool that requires explicit human approval before execution."""
    return _execute_charge_payment(world, inputs)


def _compensate_high_value_wire_transfer(
    world: MockWorld, inputs: dict[str, Any], outputs: dict[str, Any]
) -> dict[str, Any]:
    return _compensate_charge_payment(world, inputs, outputs)


def _execute_dispatch_irreversible_notification(
    world: MockWorld, inputs: dict[str, Any]
) -> dict[str, Any]:
    """Irreversible side-effecting tool (external dispatch cannot be unsent)."""
    recipient = inputs["recipient"]
    subject = inputs["subject"]
    body = inputs.get("body", "")

    message_id = inputs.get("message_id") or world.next_deterministic_id("msg")
    record = {
        "message_id": message_id,
        "recipient": recipient,
        "subject": subject,
        "body": body,
        "status": "SENT",
    }
    world.state["emails_sent"].append(record)
    return record


def create_default_tool_registry() -> ToolRegistry:
    """Create a ToolRegistry pre-populated with AG02 deterministic mocked tools."""
    registry = ToolRegistry()

    # HOTEL: create_booking & cancel_booking
    registry.register_tool(
        name="create_booking",
        description="Create a hotel booking(user, room) in MockWorld.",
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "user": {"type": "string"},
                "room": {"type": "string"},
                "flight_id": {"type": "string"},
                "passenger_name": {"type": "string"},
                "seats": {"type": "integer"},
            },
        },
        execute_action=_execute_create_booking,
        is_reversible=True,
        compensate_action=_compensate_create_booking,
        compensation_tool="cancel_booking",
        requires_approval=False,
    )

    registry.register_tool(
        name="cancel_booking",
        description="Cancel a hotel booking(booking_id) and remove it from MockWorld.",
        input_schema={
            "type": "object",
            "required": ["booking_id"],
            "properties": {
                "booking_id": {"type": "string"},
            },
        },
        execute_action=_execute_cancel_booking_direct,
        is_reversible=False,
        compensate_action=None,
        compensation_tool=None,
        requires_approval=False,
    )

    # PAYMENT: charge_payment & refund_payment
    registry.register_tool(
        name="charge_payment",
        description="Charge a payment(user, amount) and add it to MockWorld.payments.",
        input_schema={
            "type": "object",
            "required": ["amount"],
            "properties": {
                "user": {"type": "string"},
                "user_id": {"type": "string"},
                "amount": {"type": "number"},
                "description": {"type": "string"},
            },
        },
        execute_action=_execute_charge_payment,
        is_reversible=True,
        compensate_action=_compensate_charge_payment,
        compensation_tool="refund_payment",
        requires_approval=False,
    )

    registry.register_tool(
        name="refund_payment",
        description="Refund/remove a payment(payment_id) from MockWorld.payments.",
        input_schema={
            "type": "object",
            "required": ["payment_id"],
            "properties": {
                "payment_id": {"type": "string"},
            },
        },
        execute_action=_execute_refund_payment_direct,
        is_reversible=False,
        compensate_action=None,
        compensation_tool=None,
        requires_approval=False,
    )

    # TICKET: create_ticket & delete_ticket
    registry.register_tool(
        name="create_ticket",
        description="Create a ticket(user, issue) in MockWorld.tickets.",
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "user": {"type": "string"},
                "issue": {"type": "string"},
                "holder_name": {"type": "string"},
                "reference": {"type": "string"},
            },
        },
        execute_action=_execute_issue_ticket,
        is_reversible=True,
        compensate_action=_compensate_issue_ticket,
        compensation_tool="delete_ticket",
        requires_approval=False,
    )

    registry.register_tool(
        name="delete_ticket",
        description="Delete a ticket(ticket_id) from MockWorld.tickets.",
        input_schema={
            "type": "object",
            "required": ["ticket_id"],
            "properties": {
                "ticket_id": {"type": "string"},
            },
        },
        execute_action=_execute_delete_ticket_direct,
        is_reversible=False,
        compensate_action=None,
        compensation_tool=None,
        requires_approval=False,
    )

    # EMAIL: send_email (IRREVERSIBLE, approval_required=True)
    registry.register_tool(
        name="send_email",
        description="Send an email(to, subject, body). Irreversible external action requiring approval.",
        input_schema={
            "type": "object",
            "required": ["to", "subject"],
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
        },
        execute_action=_execute_send_email,
        is_reversible=False,
        compensate_action=None,
        compensation_tool=None,
        requires_approval=True,
    )

    # Additional domain tools from Phase 1 & 2
    registry.register_tool(
        name="book_flight",
        description="Book seats on a mocked flight and decrement seat inventory.",
        input_schema={
            "type": "object",
            "required": ["flight_id", "passenger_name"],
            "properties": {
                "flight_id": {"type": "string"},
                "passenger_name": {"type": "string"},
                "seats": {"type": "integer"},
            },
        },
        execute_action=_execute_book_flight,
        is_reversible=True,
        compensate_action=_compensate_book_flight,
        compensation_tool="cancel_flight",
        requires_approval=False,
    )

    registry.register_tool(
        name="reserve_hotel",
        description="Reserve hotel rooms in MockWorld and decrement available room count.",
        input_schema={
            "type": "object",
            "required": ["hotel_id", "guest_name"],
            "properties": {
                "hotel_id": {"type": "string"},
                "guest_name": {"type": "string"},
                "nights": {"type": "integer"},
                "rooms": {"type": "integer"},
            },
        },
        execute_action=_execute_reserve_hotel,
        is_reversible=True,
        compensate_action=_compensate_reserve_hotel,
        compensation_tool="cancel_hotel",
        requires_approval=False,
    )

    registry.register_tool(
        name="create_calendar_event",
        description="Schedule an event on the mocked calendar.",
        input_schema={
            "type": "object",
            "required": ["title", "date"],
            "properties": {
                "title": {"type": "string"},
                "date": {"type": "string"},
                "attendees": {"type": "array"},
            },
        },
        execute_action=_execute_create_calendar_event,
        is_reversible=True,
        compensate_action=_compensate_create_calendar_event,
        compensation_tool="delete_calendar_event",
        requires_approval=False,
    )

    registry.register_tool(
        name="reserve_inventory",
        description="Place a stock reservation hold on a warehouse SKU.",
        input_schema={
            "type": "object",
            "required": ["sku", "quantity"],
            "properties": {
                "sku": {"type": "string"},
                "quantity": {"type": "integer"},
            },
        },
        execute_action=_execute_reserve_inventory,
        is_reversible=True,
        compensate_action=_compensate_reserve_inventory,
        compensation_tool="release_inventory",
        requires_approval=False,
    )

    registry.register_tool(
        name="issue_ticket",
        description="Issue a travel or event ticket in MockWorld.",
        input_schema={
            "type": "object",
            "required": ["holder_name", "reference"],
            "properties": {
                "holder_name": {"type": "string"},
                "reference": {"type": "string"},
            },
        },
        execute_action=_execute_issue_ticket,
        is_reversible=True,
        compensate_action=_compensate_issue_ticket,
        compensation_tool="void_ticket",
        requires_approval=False,
    )

    registry.register_tool(
        name="high_value_wire_transfer",
        description="Execute a high-value wire transfer requiring prior user approval.",
        input_schema={
            "type": "object",
            "required": ["amount"],
            "properties": {
                "user_id": {"type": "string"},
                "amount": {"type": "number"},
                "description": {"type": "string"},
            },
        },
        execute_action=_execute_high_value_wire_transfer,
        is_reversible=True,
        compensate_action=_compensate_high_value_wire_transfer,
        compensation_tool="refund_wire_transfer",
        requires_approval=True,
    )

    registry.register_tool(
        name="dispatch_irreversible_notification",
        description="Send an external notification that cannot be undone once dispatched.",
        input_schema={
            "type": "object",
            "required": ["recipient", "subject"],
            "properties": {
                "recipient": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
        },
        execute_action=_execute_dispatch_irreversible_notification,
        is_reversible=False,
        compensate_action=None,
        compensation_tool=None,
        requires_approval=True,
    )

    registry.register_tool(
        name="send_notification",
        description="Send a workflow notification message.",
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "to": {"type": "string"},
                "recipient": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
        },
        execute_action=lambda world, inputs: world.send_email(
            to=str(inputs.get("to") or inputs.get("recipient") or "user@example.com"),
            subject=str(inputs.get("subject") or "Workflow Notification"),
            body=str(inputs.get("body") or ""),
        ),
        is_reversible=False,
        compensate_action=None,
        compensation_tool=None,
        requires_approval=False,
    )

    return registry

