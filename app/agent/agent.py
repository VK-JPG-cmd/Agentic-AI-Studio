"""Agent orchestrator implementing multi-step planning, state tracking, and tool limits.

Phase 4 requirements:
- Explicit AgentState tracking (user_goal, current_step, tool_calls, tool_results, final_answer, status)
- Unique run ID per execution and unique call ID per tool call
- Full timestamp tracking (ISO-8601 UTC)
- Explicit categorization: THINK/DECISION, TOOL_CALL, TOOL_RESULT, FINAL
- Concise decision metadata (no hidden chain-of-thought exposure)
- Guardrails: Maximum 8 tool calls per run, maximum iterations, timeout protection
- Tool error recovery and multi-step task execution
"""

from datetime import datetime, timezone
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Union
import uuid

from app.config import Settings, get_settings
from app.db.database import save_log, save_session
from app.llm import LLMClient, LLMMessage, LLMResponse, get_llm_client
from app.models.schemas import (
    AgentResponse,
    AgentState,
    AgentStep,
    StepType,
    ToolCall,
    ToolResult,
    UserRequest,
)
from app.tools.registry import ToolRegistry, get_default_registry

logger = logging.getLogger(__name__)


class Agent:
    """Multi-step Agent orchestrator with explicit planning and state tracking."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        tool_registry: Optional[ToolRegistry] = None,
        llm_client: Optional[LLMClient] = None,
        max_iterations: int = 10,
        max_tool_calls: int = 8,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.settings = settings or get_settings()
        self.tool_registry = tool_registry or get_default_registry()
        self.llm_client = llm_client or get_llm_client(self.settings)
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.timeout_seconds = timeout_seconds

    def _build_system_prompt(self) -> str:
        """Construct system prompt providing tool descriptions and interaction format."""
        tools_description = self.tool_registry.get_descriptions_prompt()
        return (
            "You are an intelligent, autonomous Agentic AI assistant capable of multi-step planning and tool execution.\n\n"
            "AVAILABLE TOOLS:\n"
            f"{tools_description}\n\n"
            "RULES AND WORKFLOW:\n"
            "1. Break complex user goals into logical sequential steps.\n"
            "2. When the user asks to open, browse, visit, or navigate to a URL or website, you MUST call the 'browser' tool with {\"url\": \"<target_url>\"}.\n"
            "3. DO NOT call 'echo' when the user wants to open a URL or execute an action. 'echo' is only for echoing plain text.\n"
            "4. When you need information or to execute a tool, output a tool call using standard tool-calling OR a JSON action block:\n"
            '   ```json\n'
            '   {"tool": "<tool_name>", "arguments": {<key_value_arguments>}}\n'
            '   ```\n'
            "5. You may call tools sequentially across multiple turns (e.g. fetch data first, then compute, then conclude).\n"
            f"6. Maximum {self.max_tool_calls} tool calls allowed per run. Plan efficiently.\n"
            "7. After each tool result, evaluate if you have gathered enough information to fulfill the user's goal.\n"
            "8. When the goal is satisfied, formulate your direct final answer to the user in plain text without calling any tool. DO NOT output function call tags or code blocks for your final answer.\n"
            "9. Never call the same tool repeatedly with identical parameters.\n"
            "10. If a tool returns an error, evaluate the error message and recover with alternative inputs or tools.\n"
            "11. If the user's request does not require tools or cannot be fulfilled with the available tools, respond directly in plain text without calling any tool."
        )

    def _derive_concise_decision(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Generate concise decision metadata without exposing private chain-of-thought."""
        if tool_name == "weather":
            city = arguments.get("city", "specified location")
            return f"weather information required for {city}"
        elif tool_name == "calculator":
            expr = arguments.get("expression", "mathematical calculation")
            return f"calculation required: {expr}"
        elif tool_name == "search":
            query = arguments.get("query", "topic")
            return f"knowledge search required for '{query}'"
        elif tool_name == "echo":
            return f"echo message verification required"
        elif tool_name in ("browser", "open_tab"):
            url = arguments.get("url", "blank tab")
            return f"open browser tab for '{url}'"
        return f"execution of tool '{tool_name}' required"

    def _extract_tool_calls(self, response: LLMResponse) -> List[ToolCall]:
        """Extract tool calls from LLMResponse (native or structured content)."""
        # 1. Native tool calls from LLM provider
        if response.tool_calls:
            return response.tool_calls

        # 2. Extract from JSON content or Markdown code blocks
        content = (response.content or "").strip()
        if not content:
            return []

        if content.lower().startswith("final answer:"):
            return []

        # 2a. Extract from <function=tool_name>{args}</function> or <function=tool_name>{args}
        fn_matches = list(re.finditer(r"<function=([a-zA-Z0-9_\-]+)>\s*(\{[\s\S]*?\})\s*(?:</function>)?", content))
        if fn_matches:
            fn_calls = []
            for m in fn_matches:
                fn_name = m.group(1).strip()
                raw_args = m.group(2).strip()
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {"raw": raw_args}
                if not isinstance(args, dict):
                    args = {"raw": args}
                fn_calls.append(
                    ToolCall(
                        call_id=f"call_{uuid.uuid4().hex[:8]}",
                        tool_name=fn_name,
                        arguments=args,
                    )
                )
            if fn_calls:
                return fn_calls

        # 2b. Extract from <tool_call>{...}</tool_call>
        tc_matches = list(re.finditer(r"<tool_call>\s*(\{[\s\S]*?\})\s*</tool_call>", content))
        if tc_matches:
            tc_calls = []
            for m in tc_matches:
                try:
                    data = json.loads(m.group(1))
                    tool_name = data.get("name") or data.get("tool")
                    arguments = data.get("arguments") or data.get("parameters") or {}
                    if tool_name and isinstance(tool_name, str):
                        if not isinstance(arguments, dict):
                            arguments = {"raw": arguments}
                        tc_calls.append(
                            ToolCall(
                                call_id=f"call_{uuid.uuid4().hex[:8]}",
                                tool_name=tool_name.strip(),
                                arguments=arguments,
                            )
                        )
                except Exception:
                    pass
            if tc_calls:
                return tc_calls

        # 2c. Extract from [TOOL_CALLS] [...]
        tc_tag = re.search(r"\[TOOL_CALLS\]\s*(\[[\s\S]*?\])", content)
        if tc_tag:
            try:
                data = json.loads(tc_tag.group(1))
                if isinstance(data, list):
                    tag_calls = []
                    for item in data:
                        tool_name = item.get("name") or item.get("tool")
                        arguments = item.get("arguments") or item.get("parameters") or {}
                        if tool_name and isinstance(tool_name, str):
                            if not isinstance(arguments, dict):
                                arguments = {"raw": arguments}
                            tag_calls.append(
                                ToolCall(
                                    call_id=f"call_{uuid.uuid4().hex[:8]}",
                                    tool_name=tool_name.strip(),
                                    arguments=arguments,
                                )
                            )
                    if tag_calls:
                        return tag_calls
            except Exception:
                pass

        # 3. Look for code block ```json ... ```
        json_matches = re.findall(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", content)
        candidate_strings = json_matches if json_matches else []

        if not candidate_strings:
            brace_match = re.search(r"(\{[\s\S]*\})", content)
            if brace_match:
                candidate_strings.append(brace_match.group(1))

        for cand in candidate_strings:
            try:
                data = json.loads(cand)
                if isinstance(data, dict):
                    tool_name = data.get("tool") or data.get("action") or data.get("name")
                    arguments = data.get("arguments") or data.get("action_input") or data.get("parameters") or {}
                    
                    if tool_name and isinstance(tool_name, str):
                        if not isinstance(arguments, dict):
                            arguments = {"raw": arguments}
                        return [
                            ToolCall(
                                call_id=f"call_{uuid.uuid4().hex[:8]}",
                                tool_name=tool_name.strip(),
                                arguments=arguments,
                            )
                        ]
            except Exception:
                continue

        # 4. Check for plain text "Action: ... Action Input: ..." format
        action_match = re.search(r"Action:\s*([a-zA-Z0-9_\-]+)", content, re.IGNORECASE)
        if action_match:
            tool_name = action_match.group(1).strip()
            args: Dict[str, Any] = {}
            input_match = re.search(r"Action Input:\s*(\{[\s\S]*\}|[^\n]+)", content, re.IGNORECASE)
            if input_match:
                raw_in = input_match.group(1).strip()
                try:
                    args = json.loads(raw_in)
                    if not isinstance(args, dict):
                        args = {"query": raw_in}
                except Exception:
                    args = {"input": raw_in}
            return [
                ToolCall(
                    call_id=f"call_{uuid.uuid4().hex[:8]}",
                    tool_name=tool_name,
                    arguments=args,
                )
            ]

        return []

    def run(self, request: UserRequest) -> AgentResponse:
        """Execute multi-step goal with explicit planning and state tracking."""
        session_id = request.session_id or f"sess_{uuid.uuid4().hex[:8]}"
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        start_time = time.monotonic()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Initialize structured execution state (Requirements: user_goal, current_step, tool_calls, tool_results, final_answer, status)
        state = AgentState(
            run_id=run_id,
            session_id=session_id,
            user_goal=request.query,
            current_step=0,
            max_steps=self.max_iterations,
            max_tool_calls=self.max_tool_calls,
            is_finished=False,
            status="running",
            created_at=now_iso,
            updated_at=now_iso,
            metadata={
                "tools_available": self.tool_registry.list_tool_names(),
                "context": request.context,
            },
        )

        # 1. Initialize persistent session in SQLite
        save_session(
            session_id=session_id,
            user_goal=request.query,
            status="running",
            db_path=self.settings.database_path,
        )

        # 2. Record incoming user request in audit log
        save_log(
            session_id=session_id,
            step_index=0,
            role="user",
            content=request.query,
            metadata={"run_id": run_id, "context": request.context},
            db_path=self.settings.database_path,
        )

        # 3. Setup conversation messages
        system_prompt = self._build_system_prompt()
        messages: List[Union[LLMMessage, Dict[str, str]]] = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=request.query),
        ]
        tool_schemas = self.tool_registry.get_schemas()

        all_tool_calls: List[ToolCall] = []
        all_tool_results: List[ToolResult] = []

        step = 0
        try:
            while step < self.max_iterations:
                state.current_step = step + 1
                state.updated_at = datetime.now(timezone.utc).isoformat()

                # Timeout protection check
                if (time.monotonic() - start_time) > self.timeout_seconds:
                    logger.warning("Run %s timed out after %.2fs", run_id, self.timeout_seconds)
                    timeout_msg = f"Execution timed out after {self.timeout_seconds} seconds."
                    state.is_finished = True
                    state.status = "timeout"
                    state.error = timeout_msg
                    state.final_answer = timeout_msg
                    save_session(session_id, request.query, status="timeout", db_path=self.settings.database_path)
                    return AgentResponse(
                        run_id=run_id,
                        response=timeout_msg,
                        session_id=session_id,
                        status="timeout",
                        steps=state.steps,
                        tool_calls=all_tool_calls,
                        tool_results=all_tool_results,
                        iterations=step + 1,
                    )

                # Step A: LLM Decision
                llm_response = self.llm_client.generate(
                    messages=messages,
                    tools=tool_schemas,
                )

                # Step B: Check for requested tool calls
                tool_calls = self._extract_tool_calls(llm_response)

                # Case 1: Final Answer reached
                if not tool_calls:
                    clean_response = (llm_response.content or "").strip()
                    if clean_response.lower().startswith("final answer:"):
                        clean_response = clean_response[len("final answer:"):].strip()

                    # Strip any leftover raw function tags from final answer
                    clean_response = re.sub(r"</?function(?:=[^>]+)?>", "", clean_response).strip()
                    clean_response = re.sub(r"</?tool_call>", "", clean_response).strip()

                    # If response is empty but tools were executed, generate informative confirmation
                    if not clean_response and all_tool_results:
                        last_res = all_tool_results[-1]
                        if last_res.tool_name in ("browser", "open_tab") and last_res.success:
                            opened_url = (last_res.output or {}).get("url", "the requested page")
                            clean_response = f"Successfully opened {opened_url} in your browser."
                        elif last_res.success:
                            clean_response = f"Successfully executed {last_res.tool_name}."
                        else:
                            clean_response = f"Tool execution failed: {last_res.error}"

                    state.is_finished = True
                    state.status = "completed"
                    state.final_answer = clean_response

                    # Record typed step: FINAL
                    final_step = AgentStep(
                        step_type=StepType.FINAL.value,
                        content=clean_response,
                        metadata={"run_id": run_id, "iterations": step + 1, "model": llm_response.model},
                    )
                    state.steps.append(final_step)

                    save_log(
                        session_id=session_id,
                        step_index=len(state.steps),
                        role=StepType.FINAL.value,
                        content=clean_response,
                        metadata={"run_id": run_id, "iterations": step + 1, "model": llm_response.model},
                        db_path=self.settings.database_path,
                    )

                    save_session(
                        session_id=session_id,
                        user_goal=request.query,
                        status="completed",
                        db_path=self.settings.database_path,
                    )

                    return AgentResponse(
                        run_id=run_id,
                        response=clean_response,
                        session_id=session_id,
                        status="completed",
                        steps=state.steps,
                        tool_calls=all_tool_calls,
                        tool_results=all_tool_results,
                        iterations=step + 1,
                    )

                # Case 2: Process Tool Calls with Maximum 8 Tool Calls Limit
                for tc in tool_calls:
                    # Smart repair for LLM misclassification:
                    # If model called 'echo' with a URL when user asked to open/navigate/visit:
                    if tc.tool_name == "echo" and isinstance(tc.arguments, dict):
                        msg_val = str(tc.arguments.get("message") or tc.arguments.get("raw") or "").strip()
                        if (msg_val.startswith("http://") or msg_val.startswith("https://") or "huggingface.co" in msg_val or "github.com" in msg_val or "google.com" in msg_val) and any(w in request.query.lower() for w in ("open", "browse", "visit", "go to", "launch", "tab")):
                            tc.tool_name = "browser"
                            tc.arguments = {"url": msg_val}
                    # Enforce Maximum 8 tool calls per run
                    if len(all_tool_calls) >= self.max_tool_calls:
                        limit_msg = (
                            f"Tool call limit reached (maximum {self.max_tool_calls} tool calls per run). "
                            "You must now synthesize your final answer using the information gathered."
                        )
                        logger.warning("Session %s run %s reached tool limit of %d", session_id, run_id, self.max_tool_calls)
                        messages.append(LLMMessage(role="user", content=limit_msg))
                        break

                    # Check for repetitive duplicate tool calls (loop prevention)
                    is_repetitive = bool(
                        all_tool_calls
                        and all_tool_calls[-1].tool_name == tc.tool_name
                        and all_tool_calls[-1].arguments == tc.arguments
                    )
                    if is_repetitive:
                        logger.warning("Repetitive tool call detected for %s(%s)", tc.tool_name, tc.arguments)

                    # 1. Record THINK/DECISION step (Concise decision metadata, no hidden chain-of-thought)
                    decision_text = self._derive_concise_decision(tc.tool_name, tc.arguments)
                    decision_step = AgentStep(
                        step_type=StepType.DECISION.value,
                        content=decision_text,
                        metadata={"run_id": run_id, "tool_name": tc.tool_name, "call_id": tc.call_id},
                    )
                    state.steps.append(decision_step)
                    save_log(
                        session_id=session_id,
                        step_index=len(state.steps),
                        role=StepType.DECISION.value,
                        content=decision_text,
                        metadata={"run_id": run_id, "call_id": tc.call_id},
                        db_path=self.settings.database_path,
                    )

                    # 2. Record TOOL_CALL step
                    all_tool_calls.append(tc)
                    state.tool_calls.append(tc)
                    tool_call_step = AgentStep(
                        step_type=StepType.TOOL_CALL.value,
                        content=f"Calling {tc.tool_name} with {json.dumps(tc.arguments)}",
                        metadata={
                            "run_id": run_id,
                            "call_id": tc.call_id,
                            "tool_name": tc.tool_name,
                            "arguments": tc.arguments,
                        },
                    )
                    state.steps.append(tool_call_step)
                    save_log(
                        session_id=session_id,
                        step_index=len(state.steps),
                        role=StepType.TOOL_CALL.value,
                        content=json.dumps(tc.arguments),
                        metadata={"run_id": run_id, "call_id": tc.call_id, "tool_name": tc.tool_name},
                        db_path=self.settings.database_path,
                    )

                    # 3. Execute tool safely with Pydantic validation
                    tool_result = self.tool_registry.execute(
                        tool_name=tc.tool_name,
                        arguments=tc.arguments,
                        call_id=tc.call_id,
                    )

                    all_tool_results.append(tool_result)
                    state.tool_results.append(tool_result)

                    # 4. Record TOOL_RESULT step
                    res_repr = json.dumps(tool_result.output) if isinstance(tool_result.output, (dict, list)) else str(tool_result.output if tool_result.success else tool_result.error)
                    tool_result_step = AgentStep(
                        step_type=StepType.TOOL_RESULT.value,
                        content=res_repr,
                        metadata={
                            "run_id": run_id,
                            "call_id": tc.call_id,
                            "tool_name": tc.tool_name,
                            "success": tool_result.success,
                            "error": tool_result.error,
                        },
                    )
                    state.steps.append(tool_result_step)
                    save_log(
                        session_id=session_id,
                        step_index=len(state.steps),
                        role=StepType.TOOL_RESULT.value,
                        content=res_repr,
                        metadata={"run_id": run_id, "call_id": tc.call_id, "success": tool_result.success},
                        db_path=self.settings.database_path,
                    )

                    # 5. Return tool result as observation to conversation messages
                    obs_payload = tool_result.output if tool_result.success else f"Error: {tool_result.error}"
                    obs_str = json.dumps(obs_payload) if isinstance(obs_payload, (dict, list)) else str(obs_payload)
                    rep_hint = (
                        "\nWARNING: You already called this tool with identical arguments. Do NOT repeat it. "
                        "Synthesize your final answer now if ready."
                        if is_repetitive
                        else ""
                    )
                    obs_message = (
                        f"OBSERVATION from tool '{tc.tool_name}': {obs_str}{rep_hint}\n"
                        "Evaluate this result. If the user's goal has been fulfilled, formulate your final answer in plain text now without calling any other tool."
                    )
                    messages.append(
                        LLMMessage(
                            role="assistant",
                            content=f"Executed tool '{tc.tool_name}' ({tc.call_id}).",
                        )
                    )
                    messages.append(LLMMessage(role="user", content=obs_message))

                step += 1

            # Case 3: Loop iteration limit reached
            max_iter_msg = (
                f"Agent reached maximum allowable iterations ({self.max_iterations}) "
                "without producing a final answer."
            )
            logger.warning("Session %s run %s: %s", session_id, run_id, max_iter_msg)
            state.is_finished = True
            state.status = "max_iterations_reached"
            state.error = max_iter_msg
            state.final_answer = max_iter_msg

            save_session(session_id, request.query, status="max_iterations_reached", db_path=self.settings.database_path)
            save_log(
                session_id=session_id,
                step_index=len(state.steps) + 1,
                role="system",
                content=max_iter_msg,
                metadata={"run_id": run_id, "max_iterations": self.max_iterations},
                db_path=self.settings.database_path,
            )

            return AgentResponse(
                run_id=run_id,
                response=max_iter_msg,
                session_id=session_id,
                status="max_iterations_reached",
                steps=state.steps,
                tool_calls=all_tool_calls,
                tool_results=all_tool_results,
                iterations=step,
            )

        except Exception as exc:
            logger.error("Agent execution failed for run %s: %s", run_id, exc, exc_info=True)
            state.is_finished = True
            state.status = "failed"
            state.error = str(exc)
            save_session(session_id, request.query, status="failed", db_path=self.settings.database_path)
            save_log(
                session_id=session_id,
                step_index=len(state.steps) + 1,
                role="system",
                content=f"Execution error: {type(exc).__name__}",
                metadata={"run_id": run_id, "error_detail": str(exc)},
                db_path=self.settings.database_path,
            )
            raise
