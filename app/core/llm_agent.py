"""Phase 7 Hugging Face LLM Agent, Structured Plan Schemas, and Policy/Plan Validator."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Optional, Union

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.models import (
    TransactionState,
    WorkflowPlan,
    WorkflowStepSpec,
    utc_now_iso,
)
from app.core.tool_registry import ToolRegistry
from app.core.transaction_manager import TransactionManager

# Keys that may contain chain-of-thought or internal reasoning and must NEVER be stored or exposed
COT_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "chain_of_thought",
        "cot",
        "thought",
        "thoughts",
        "reasoning",
        "reasoning_content",
        "scratchpad",
        "internal_monologue",
        "rationale",
    }
)

_THINK_TAG_PATTERN = re.compile(
    r"<(?:think|thinking|reasoning|scratchpad)>.*?</(?:think|thinking|reasoning|scratchpad)>",
    flags=re.DOTALL | re.IGNORECASE,
)
_CODE_FENCE_PATTERN = re.compile(
    r"^```(?:json)?\s*(.*?)\s*```$",
    flags=re.DOTALL | re.IGNORECASE,
)


class PlanValidationError(ValueError):
    """Raised when an LLM or workflow plan fails policy or schema validation before execution."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "INVALID_PLAN",
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class LLMPlannedStep(BaseModel):
    """Structured Pydantic model for a single tool call planned by the LLM."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    step_id: Optional[str] = None
    description: str = ""
    compensation_available: Optional[bool] = None
    compensation_tool: Optional[str] = None
    irreversible: Optional[bool] = None
    is_reversible: Optional[bool] = None
    approval_required: Optional[bool] = None
    requires_approval: Optional[bool] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_step(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("Each planned step must be a JSON object.")
        cleaned = strip_chain_of_thought(data)
        if "tool" not in cleaned and "tool_name" in cleaned:
            cleaned["tool"] = cleaned["tool_name"]
        if "arguments" not in cleaned and "inputs" in cleaned:
            cleaned["arguments"] = cleaned["inputs"]
        return cleaned


class LLMWorkflowPlan(BaseModel):
    """Structured Pydantic output produced by the LLM Agent."""

    goal: str
    steps: list[LLMPlannedStep]

    @model_validator(mode="before")
    @classmethod
    def _normalize_plan(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("Workflow plan must be a JSON object.")
        return strip_chain_of_thought(data)


class DecisionMetadata(BaseModel):
    """Concise, non-CoT decision metadata stored for auditability."""

    planner: str = "HuggingFaceLLMAgent"
    model: str = "Qwen/Qwen2.5-72B-Instruct"
    goal: str
    selected_tools: list[str]
    step_count: int
    has_irreversible_steps: bool
    requires_approval: bool
    validation_status: str = "VALIDATED"
    timestamp: str = Field(default_factory=utc_now_iso)


def strip_chain_of_thought(value: Any) -> Any:
    """
    Strip all chain-of-thought tags (<think>...</think>) and forbidden reasoning keys
    from strings, dicts, or lists so internal CoT is never stored or exposed.
    """
    if isinstance(value, str):
        cleaned = _THINK_TAG_PATTERN.sub("", value).strip()
        fence_match = _CODE_FENCE_PATTERN.match(cleaned)
        if fence_match:
            cleaned = fence_match.group(1).strip()
        return cleaned
    if isinstance(value, dict):
        return {
            k: strip_chain_of_thought(v)
            for k, v in value.items()
            if str(k).lower() not in COT_FORBIDDEN_KEYS
        }
    if isinstance(value, list):
        return [strip_chain_of_thought(item) for item in value]
    return value


class PlanValidator:
    """
    Policy & validation gate between the LLM Agent and the TransactionManager.

    Rejects:
    - malformed plans (MALFORMED_PLAN)
    - unknown tools (UNKNOWN_TOOL)
    - invalid arguments (INVALID_ARGUMENTS)
    - missing compensation for a supposedly reversible side effect (MISSING_COMPENSATION)
    - unsafe irreversible ordering (UNSAFE_IRREVERSIBLE_ORDERING)
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    @staticmethod
    def _matches_json_type(value: Any, expected_type: str) -> bool:
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "object":
            return isinstance(value, dict)
        return True

    def validate(
        self,
        raw_plan: Any,
        *,
        allow_irreversible_before_reversible: bool = False,
        model_name: str = "Qwen/Qwen2.5-72B-Instruct",
    ) -> WorkflowPlan:
        """
        Validate a raw LLM response or WorkflowPlan and return a sanitized WorkflowPlan
        with concise DecisionMetadata attached.
        """
        # 1. Parse and sanitize raw input (stripping any chain-of-thought)
        parsed_obj: Any = raw_plan
        if isinstance(raw_plan, str):
            cleaned_text = strip_chain_of_thought(raw_plan)
            if not cleaned_text:
                raise PlanValidationError(
                    "Malformed plan: empty LLM response.",
                    code="MALFORMED_PLAN",
                )
            try:
                parsed_obj = json.loads(cleaned_text)
            except json.JSONDecodeError as exc:
                raise PlanValidationError(
                    f"Malformed plan: invalid JSON ({exc}).",
                    code="MALFORMED_PLAN",
                ) from exc

        if isinstance(parsed_obj, WorkflowPlan):
            goal = parsed_obj.goal
            if not isinstance(goal, str) or not goal.strip():
                raise PlanValidationError(
                    "Malformed plan: 'goal' must be a non-empty string.",
                    code="MALFORMED_PLAN",
                )
            raw_steps: list[Any] = list(parsed_obj.steps)
        elif isinstance(parsed_obj, dict):
            cleaned_dict = strip_chain_of_thought(parsed_obj)
            if "goal" not in cleaned_dict or not isinstance(cleaned_dict["goal"], str) or not cleaned_dict["goal"].strip():
                raise PlanValidationError(
                    "Malformed plan: missing or empty 'goal' field.",
                    code="MALFORMED_PLAN",
                )
            if "steps" not in cleaned_dict or not isinstance(cleaned_dict["steps"], list):
                raise PlanValidationError(
                    "Malformed plan: 'steps' must be a list of tool steps.",
                    code="MALFORMED_PLAN",
                )
            goal = cleaned_dict["goal"].strip()
            raw_steps = cleaned_dict["steps"]
        else:
            raise PlanValidationError(
                f"Malformed plan: expected JSON object or WorkflowPlan, got {type(parsed_obj).__name__}.",
                code="MALFORMED_PLAN",
            )

        if len(raw_steps) == 0:
            raise PlanValidationError(
                "Malformed plan: 'steps' cannot be empty.",
                code="MALFORMED_PLAN",
            )

        validated_specs: list[WorkflowStepSpec] = []
        step_reversibility: list[tuple[int, str, bool]] = []

        for idx, raw_step in enumerate(raw_steps):
            step_num = idx + 1
            if isinstance(raw_step, WorkflowStepSpec):
                step_dict = raw_step.model_dump()
            elif isinstance(raw_step, dict):
                step_dict = strip_chain_of_thought(raw_step)
            else:
                raise PlanValidationError(
                    f"Malformed plan at step {step_num}: step must be an object.",
                    code="MALFORMED_PLAN",
                    details={"step_number": step_num},
                )

            # Extract tool name
            tool_name = step_dict.get("tool") or step_dict.get("tool_name")
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise PlanValidationError(
                    f"Malformed plan at step {step_num}: missing 'tool' name.",
                    code="MALFORMED_PLAN",
                    details={"step_number": step_num},
                )
            tool_name = tool_name.strip()

            # 2. Check unknown tools
            if not self.registry.has(tool_name):
                raise PlanValidationError(
                    f"Unknown tool '{tool_name}' at step {step_num}. Tool is not registered in ToolRegistry.",
                    code="UNKNOWN_TOOL",
                    details={"step_number": step_num, "tool": tool_name},
                )
            tool_def = self.registry.get(tool_name)

            # 3. Validate arguments structure & schema
            if "arguments" in step_dict and step_dict["arguments"] is not None:
                raw_args = step_dict["arguments"]
            elif "inputs" in step_dict and step_dict["inputs"] is not None:
                raw_args = step_dict["inputs"]
            else:
                raw_args = {}

            if not isinstance(raw_args, dict):
                raise PlanValidationError(
                    f"Invalid arguments for tool '{tool_name}' at step {step_num}: 'arguments' must be a dictionary.",
                    code="INVALID_ARGUMENTS",
                    details={"step_number": step_num, "tool": tool_name},
                )

            cleaned_args = strip_chain_of_thought(raw_args)

            # Check required fields via ToolDefinition.validate_inputs
            try:
                tool_def.validate_inputs(cleaned_args)
            except (ValueError, TypeError) as exc:
                raise PlanValidationError(
                    f"Invalid arguments for tool '{tool_name}' at step {step_num}: {exc}",
                    code="INVALID_ARGUMENTS",
                    details={"step_number": step_num, "tool": tool_name, "error": str(exc)},
                ) from exc

            if tool_name == "create_booking" and not (
                ("user" in cleaned_args and "room" in cleaned_args)
                or ("flight_id" in cleaned_args and "passenger_name" in cleaned_args)
            ):
                raise PlanValidationError(
                    f"Invalid arguments for tool 'create_booking' at step {step_num}: requires ('user' and 'room') or ('flight_id' and 'passenger_name').",
                    code="INVALID_ARGUMENTS",
                    details={"step_number": step_num, "tool": tool_name},
                )

            if tool_name == "create_ticket" and not (
                ("user" in cleaned_args and "issue" in cleaned_args)
                or ("holder_name" in cleaned_args and "reference" in cleaned_args)
            ):
                raise PlanValidationError(
                    f"Invalid arguments for tool 'create_ticket' at step {step_num}: requires ('user' and 'issue') or ('holder_name' and 'reference').",
                    code="INVALID_ARGUMENTS",
                    details={"step_number": step_num, "tool": tool_name},
                )

            # Check unknown argument keys and property types against input_schema
            schema = tool_def.input_schema or {}
            properties = schema.get("properties", {})
            if properties:
                unknown_keys = set(cleaned_args.keys()) - set(properties.keys())
                if unknown_keys:
                    raise PlanValidationError(
                        f"Invalid arguments for tool '{tool_name}' at step {step_num}: unexpected argument(s) {sorted(unknown_keys)}.",
                        code="INVALID_ARGUMENTS",
                        details={
                            "step_number": step_num,
                            "tool": tool_name,
                            "unexpected_arguments": sorted(unknown_keys),
                        },
                    )
                for arg_key, arg_val in cleaned_args.items():
                    prop_schema = properties.get(arg_key, {})
                    expected_type = prop_schema.get("type")
                    if expected_type and not self._matches_json_type(arg_val, expected_type):
                        raise PlanValidationError(
                            f"Invalid arguments for tool '{tool_name}' at step {step_num}: argument '{arg_key}' expected type '{expected_type}', got {type(arg_val).__name__}.",
                            code="INVALID_ARGUMENTS",
                            details={
                                "step_number": step_num,
                                "tool": tool_name,
                                "argument": arg_key,
                                "expected_type": expected_type,
                            },
                        )

            # 4. Check compensation rules for supposedly reversible side effects
            explicit_irreversible = step_dict.get("irreversible")
            explicit_is_reversible = step_dict.get("is_reversible")
            explicit_comp_avail = step_dict.get("compensation_available")
            explicit_comp_tool = step_dict.get("compensation_tool")

            claims_reversible = (
                explicit_is_reversible is True
                or explicit_irreversible is False
                or (
                    explicit_is_reversible is None
                    and explicit_irreversible is None
                    and tool_def.is_reversible
                )
            )

            if claims_reversible:
                # Tool itself must be reversible and have a valid compensation action/tool
                if (
                    not tool_def.is_reversible
                    or not tool_def.compensation_available
                    or tool_def.compensate_action is None
                    or not tool_def.compensation_tool
                ):
                    raise PlanValidationError(
                        f"Missing compensation for supposedly reversible tool '{tool_name}' at step {step_num}.",
                        code="MISSING_COMPENSATION",
                        details={"step_number": step_num, "tool": tool_name},
                    )
                # Step cannot disable compensation or set an empty/invalid compensation_tool while claiming reversibility
                if explicit_comp_avail is False:
                    raise PlanValidationError(
                        f"Missing compensation for reversible step '{tool_name}' at step {step_num}: compensation_available cannot be False.",
                        code="MISSING_COMPENSATION",
                        details={"step_number": step_num, "tool": tool_name},
                    )
                if explicit_comp_tool is not None and (
                    not isinstance(explicit_comp_tool, str)
                    or not explicit_comp_tool.strip()
                    or explicit_comp_tool.strip() != tool_def.compensation_tool
                ):
                    raise PlanValidationError(
                        f"Missing or mismatched compensation tool '{explicit_comp_tool}' for reversible step '{tool_name}' at step {step_num} (expected '{tool_def.compensation_tool}').",
                        code="MISSING_COMPENSATION",
                        details={"step_number": step_num, "tool": tool_name},
                    )

            is_step_reversible = bool(tool_def.is_reversible)
            step_reversibility.append((step_num, tool_name, is_step_reversible))

            # If an irreversible step is before the end and explicitly tries to bypass approval, reject!
            if not is_step_reversible and idx < len(raw_steps) - 1:
                if (
                    step_dict.get("approval_required") is False
                    or step_dict.get("requires_approval") is False
                ):
                    raise PlanValidationError(
                        f"Unsafe irreversible ordering at step {step_num} ('{tool_name}'): irreversible step before subsequent steps cannot disable approval.",
                        code="UNSAFE_IRREVERSIBLE_ORDERING",
                        details={"step_number": step_num, "tool": tool_name},
                    )

            requires_approval = bool(
                tool_def.requires_approval
                or (not is_step_reversible and idx < len(raw_steps) - 1)
                or step_dict.get("approval_required")
                or step_dict.get("requires_approval")
            )

            validated_specs.append(
                WorkflowStepSpec(
                    step_id=step_dict.get("step_id"),
                    tool=tool_name,
                    tool_name=tool_name,
                    description=step_dict.get("description") or tool_def.description,
                    arguments=cleaned_args,
                    inputs=cleaned_args,
                    compensation_available=bool(
                        is_step_reversible and tool_def.compensation_available
                    ),
                    compensation_tool=(
                        tool_def.compensation_tool if is_step_reversible else None
                    ),
                    irreversible=not is_step_reversible,
                    is_reversible=is_step_reversible,
                    approval_required=requires_approval,
                    requires_approval=requires_approval,
                )
            )

        # 5. Check unsafe irreversible ordering:
        # Irreversible steps must be placed LAST; if an irreversible step appears before any reversible step, reject!
        if not allow_irreversible_before_reversible:
            seen_irreversible: Optional[tuple[int, str]] = None
            for step_num, t_name, is_rev in step_reversibility:
                if not is_rev and seen_irreversible is None:
                    seen_irreversible = (step_num, t_name)
                elif is_rev and seen_irreversible is not None:
                    irrev_num, irrev_tool = seen_irreversible
                    raise PlanValidationError(
                        f"Unsafe irreversible ordering: irreversible tool '{irrev_tool}' at step {irrev_num} appears before reversible tool '{t_name}' at step {step_num}. Irreversible steps must be placed last.",
                        code="UNSAFE_IRREVERSIBLE_ORDERING",
                        details={
                            "irreversible_step": irrev_num,
                            "irreversible_tool": irrev_tool,
                            "reversible_step": step_num,
                            "reversible_tool": t_name,
                        },
                    )

        decision_meta = DecisionMetadata(
            planner="HuggingFaceLLMAgent",
            model=model_name,
            goal=goal,
            selected_tools=[s.tool_name for s in validated_specs],
            step_count=len(validated_specs),
            has_irreversible_steps=any(s.irreversible for s in validated_specs),
            requires_approval=any(bool(s.requires_approval) for s in validated_specs),
            validation_status="VALIDATED",
        ).model_dump()

        return WorkflowPlan(
            goal=goal,
            steps=validated_specs,
            metadata={"decision_metadata": decision_meta},
            decision_metadata=decision_meta,
        )


MockResponderType = Union[
    Callable[[str, list[dict[str, Any]]], Union[str, dict[str, Any]]],
    dict[str, Any],
    str,
]


class HuggingFaceLLMClient:
    """
    Hugging Face Inference API client for structured AG02 workflow planning.

    IMPORTANT:
    - This client ONLY understands the user's goal and produces a structured JSON plan.
    - It has NO reference to MockWorld or ToolExecutor and can NEVER directly execute tools.
    - Supports `mock_responder` so unit tests never require external API calls.
    """

    DEFAULT_MODEL = "Qwen/Qwen2.5-72B-Instruct"
    DEFAULT_API_URL = "https://router.huggingface.co/v1/chat/completions"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        api_url: Optional[str] = None,
        mock_responder: Optional[MockResponderType] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_key = (
            api_key
            or os.getenv("HF_TOKEN")
            or os.getenv("HUGGINGFACE_API_KEY")
            or os.getenv("HUGGINGFACEHUB_API_TOKEN")
        )
        self.model = model or os.getenv("HF_MODEL") or self.DEFAULT_MODEL
        self.api_url = api_url or os.getenv("HF_API_URL") or self.DEFAULT_API_URL
        self.mock_responder = mock_responder
        self.timeout_seconds = timeout_seconds
        self.call_history: list[dict[str, Any]] = []

    def build_system_prompt(self, tool_schemas: list[dict[str, Any]]) -> str:
        """Build the strict JSON-only system prompt describing available tools and safety rules."""
        tools_summary = [
            {
                "tool": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
                "reversible": t["is_reversible"],
                "compensation_tool": t["compensation_tool"],
                "approval_required": t["requires_approval"],
            }
            for t in tool_schemas
        ]
        return (
            "You are the AG02 Transactional Workflow Planner.\n"
            "Your ONLY responsibility is to convert the user's goal into a structured JSON workflow plan.\n"
            "Rules:\n"
            "1. Select ONLY from the registered tools listed below.\n"
            "2. Provide valid arguments matching each tool's input_schema.\n"
            "3. Place all reversible steps FIRST and any irreversible steps (such as send_email) LAST.\n"
            "4. Do NOT include chain-of-thought or explanations. Respond ONLY with valid JSON matching:\n"
            '{"goal": "<concise user goal>", "steps": [{"tool": "<tool_name>", "arguments": {...}}]}\n\n'
            f"Registered Tools:\n{json.dumps(tools_summary, indent=2)}"
        )

    def generate_plan(
        self,
        user_goal: str,
        tool_schemas: list[dict[str, Any]],
    ) -> Union[str, dict[str, Any]]:
        """
        Request a structured workflow plan from the mocked responder or Hugging Face Inference API.
        Never executes tools directly.
        """
        system_prompt = self.build_system_prompt(tool_schemas)
        self.call_history.append(
            {
                "user_goal": user_goal,
                "model": self.model,
                "tool_count": len(tool_schemas),
                "timestamp": utc_now_iso(),
            }
        )

        if self.mock_responder is not None:
            if callable(self.mock_responder):
                return self.mock_responder(user_goal, tool_schemas)
            return self.mock_responder

        if not self.api_key:
            raise RuntimeError(
                "Hugging Face API token not configured (set HF_TOKEN or pass mock_responder for offline testing)."
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_goal},
            ],
            "temperature": 0.0,
            "max_tokens": 800,
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(self.api_url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices", [])
        if not choices:
            raise PlanValidationError(
                "Malformed plan: Hugging Face API returned no choices.",
                code="MALFORMED_PLAN",
            )
        content = choices[0].get("message", {}).get("content", "")
        return strip_chain_of_thought(content)


class LLMAgent:
    """
    AG02 LLM Agent connecting the Hugging Face LLM planner to the Saga TransactionManager.

    Architecture:
        USER
         ↓
        LLM AGENT (produces structured plan ONLY; never executes tools)
         ↓
        STRUCTURED PLAN
         ↓
        TRANSACTION MANAGER
         ↓
        POLICY/VALIDATION (PlanValidator)
         ↓
        TOOL EXECUTOR
         ↓
        MOCK WORLD
    """

    def __init__(
        self,
        *,
        transaction_manager: TransactionManager,
        llm_client: Optional[HuggingFaceLLMClient] = None,
        mock_responder: Optional[MockResponderType] = None,
    ) -> None:
        self.transaction_manager = transaction_manager
        self.registry = transaction_manager.registry
        self.validator = PlanValidator(registry=self.registry)
        self.llm_client = llm_client or HuggingFaceLLMClient(
            mock_responder=mock_responder
        )

    def create_structured_plan(self, user_request: str) -> WorkflowPlan:
        """
        Ask the LLM to produce a structured plan for `user_request`, strip any chain-of-thought,
        and validate the plan against all AG02 policy rules without executing any tools.
        """
        if not isinstance(user_request, str) or not user_request.strip():
            raise PlanValidationError(
                "Malformed plan: user request cannot be empty.",
                code="MALFORMED_PLAN",
            )

        tool_schemas = self.registry.list_metadata()
        raw_llm_output = self.llm_client.generate_plan(user_request, tool_schemas)
        validated_plan = self.validator.validate(
            raw_llm_output,
            allow_irreversible_before_reversible=False,
            model_name=self.llm_client.model,
        )
        return validated_plan

    def execute_request(
        self,
        user_request: str,
        *,
        transaction_id: Optional[str] = None,
        raise_on_crash: bool = False,
    ) -> TransactionState:
        """
        Plan via the LLM, validate via PlanValidator, and execute via TransactionManager.
        The LLM never directly invokes tools or bypasses TransactionManager controls.
        """
        validated_plan = self.create_structured_plan(user_request)
        return self.transaction_manager.execute_transaction(
            validated_plan,
            transaction_id=transaction_id,
            raise_on_crash=raise_on_crash,
        )

    def run_and_report(
        self,
        user_request: str,
        *,
        transaction_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Full Phase 7 pipeline:
        User request -> LLM plan -> TransactionManager -> tools -> MockWorld -> final transaction report.
        """
        tx = self.execute_request(user_request, transaction_id=transaction_id)
        return tx.to_report(world_state=self.transaction_manager.world.snapshot())
