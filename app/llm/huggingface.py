"""Hugging Face Inference API LLM client implementation.

Communicates with Hugging Face's OpenAI-compatible inference router.
Encapsulates token handling, timeout policies, response formatting, and error shielding.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Union
import uuid
import httpx

from app.config import Settings, get_settings
from app.models.schemas import ToolCall
from app.llm.base import (
    LLMAuthenticationError,
    LLMClient,
    LLMError,
    LLMMessage,
    LLMProviderError,
    LLMResponse,
    LLMTimeoutError,
)

logger = logging.getLogger(__name__)


class HuggingFaceLLMClient(LLMClient):
    """Hugging Face Inference API client for chat and completions."""

    def __init__(
        self,
        api_token: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        cfg = settings or get_settings()
        self._api_token = api_token if api_token is not None else cfg.get_hf_token_value()
        self.model = model or cfg.hf_model
        self.base_url = (base_url or cfg.hf_base_url).rstrip("/")
        self.timeout = timeout if timeout is not None else cfg.llm_timeout
        self._is_testing = cfg.is_testing
        self.fallback_to_mock = cfg.llm_fallback_to_mock and not self._is_testing

    def __repr__(self) -> str:
        """Safely represent client instance without exposing internal credentials."""
        return (
            f"HuggingFaceLLMClient(model='{self.model}', "
            f"base_url='{self.base_url}', timeout={self.timeout})"
        )

    def generate(
        self,
        messages: List[Union[LLMMessage, Dict[str, str]]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send chat messages to Hugging Face Inference API and return the response."""
        # 1. Enforce authentication prerequisite
        if not self._api_token or not self._api_token.strip():
            raise LLMAuthenticationError(
                "Hugging Face API token (HF_TOKEN) is not configured. "
                "Please configure HF_TOKEN in your environment or .env file."
            )

        if not messages:
            raise ValueError("Messages list cannot be empty.")

        # 2. Normalize messages into standard list of dictionaries
        normalized_messages: List[Dict[str, str]] = []
        for m in messages:
            if isinstance(m, LLMMessage):
                normalized_messages.append(m.to_dict())
            elif isinstance(m, dict):
                if "role" not in m or "content" not in m:
                    raise ValueError("Each message dictionary must contain 'role' and 'content'.")
                normalized_messages.append(
                    {"role": str(m["role"]), "content": str(m["content"])}
                )
            else:
                raise TypeError(f"Unsupported message item type: {type(m).__name__}")

        endpoint = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": normalized_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs,
        }
        if tools:
            payload["tools"] = tools

        # 3. Execute HTTP request with strict error translation
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            logger.error("Hugging Face API request timed out after %.1f seconds", self.timeout)
            raise LLMTimeoutError(
                f"Hugging Face request timed out after {self.timeout}s."
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            error_detail = "Unknown error"
            try:
                err_json = exc.response.json()
                if isinstance(err_json, dict):
                    error_detail = err_json.get("error", err_json.get("message", exc.response.text))
                    if isinstance(error_detail, (list, dict)):
                        error_detail = str(error_detail)
                else:
                    error_detail = str(err_json)
            except Exception:
                error_detail = exc.response.text[:200]

            # Redact any accidental credential echo in upstream response
            if self._api_token and self._api_token in error_detail:
                error_detail = error_detail.replace(self._api_token, "[REDACTED]")

            logger.error("Hugging Face API returned HTTP %s: %s", status_code, error_detail)

            if self.fallback_to_mock and status_code in (400, 402, 404, 429, 500, 502, 503):
                logger.warning(
                    "Hugging Face API returned HTTP %s (%s). Falling back to local MockLLMClient.",
                    status_code,
                    error_detail,
                )
                from app.llm.mock import MockLLMClient
                return MockLLMClient(model=f"mock-fallback-{self.model}").generate(
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )

            if status_code in (401, 403):
                raise LLMAuthenticationError(
                    f"Hugging Face authentication failed (HTTP {status_code}). Please verify your HF_TOKEN."
                ) from exc
            elif status_code == 429:
                raise LLMProviderError(
                    "Hugging Face API rate limit reached. Please retry after a brief delay.",
                    status_code=429,
                ) from exc
            elif status_code == 503:
                raise LLMProviderError(
                    "Hugging Face model is currently loading or unavailable. Please retry shortly.",
                    status_code=503,
                ) from exc
            else:
                raise LLMProviderError(
                    f"Hugging Face API returned error (HTTP {status_code}): {error_detail}",
                    status_code=status_code,
                ) from exc
        except httpx.RequestError as exc:
            logger.error("Network error while connecting to Hugging Face: %s", exc)
            if self.fallback_to_mock:
                logger.warning(
                    "Network error connecting to Hugging Face (%s). Falling back to local MockLLMClient.",
                    exc,
                )
                from app.llm.mock import MockLLMClient
                return MockLLMClient(model=f"mock-fallback-{self.model}").generate(
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
            raise LLMProviderError(
                f"Network error connecting to Hugging Face API: {type(exc).__name__}"
            ) from exc

        # 4. Parse response payload safely
        try:
            choices = data.get("choices", [])
            if not choices:
                raise ValueError("No completion choices returned by the model.")

            choice = choices[0]
            message_obj = choice.get("message", {})
            content = message_obj.get("content") or ""
            role = message_obj.get("role", "assistant")
            finish_reason = choice.get("finish_reason")
            usage = data.get("usage")

            # Parse tool calls if provided natively by provider
            parsed_tool_calls: Optional[List[ToolCall]] = None
            raw_tool_calls = message_obj.get("tool_calls")
            if raw_tool_calls and isinstance(raw_tool_calls, list):
                parsed_tool_calls = []
                for tc in raw_tool_calls:
                    func = tc.get("function", {})
                    fn_name = func.get("name", "")
                    fn_args = func.get("arguments", {})
                    if isinstance(fn_args, str):
                        try:
                            fn_args = json.loads(fn_args)
                        except Exception:
                            fn_args = {"raw": fn_args}
                    parsed_tool_calls.append(
                        ToolCall(
                            call_id=tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                            tool_name=fn_name,
                            arguments=fn_args or {},
                        )
                    )

            # Fallback: parse function tags emitted in content by models like Llama 3.1
            if not parsed_tool_calls and content:
                text_calls: List[ToolCall] = []
                # Pattern: <function=tool_name>{...}(</function>)?
                for m in re.finditer(r"<function=([a-zA-Z0-9_\-]+)>\s*(\{[\s\S]*?\})\s*(?:</function>)?", content):
                    t_name = m.group(1).strip()
                    try:
                        t_args = json.loads(m.group(2))
                    except Exception:
                        t_args = {"raw": m.group(2)}
                    if not isinstance(t_args, dict):
                        t_args = {"raw": t_args}
                    text_calls.append(
                        ToolCall(
                            call_id=f"call_{uuid.uuid4().hex[:8]}",
                            tool_name=t_name,
                            arguments=t_args,
                        )
                    )
                # Pattern: <tool_call>{...}</tool_call>
                if not text_calls:
                    for m in re.finditer(r"<tool_call>\s*(\{[\s\S]*?\})\s*</tool_call>", content):
                        try:
                            data = json.loads(m.group(1))
                            t_name = data.get("name") or data.get("tool")
                            t_args = data.get("arguments") or data.get("parameters") or {}
                            if t_name:
                                if not isinstance(t_args, dict):
                                    t_args = {"raw": t_args}
                                text_calls.append(
                                    ToolCall(
                                        call_id=f"call_{uuid.uuid4().hex[:8]}",
                                        tool_name=t_name.strip(),
                                        arguments=t_args,
                                    )
                                )
                        except Exception:
                            pass

                if text_calls:
                    parsed_tool_calls = text_calls

            return LLMResponse(
                content=content,
                model=data.get("model", self.model),
                role=role,
                finish_reason=finish_reason,
                usage=usage,
                tool_calls=parsed_tool_calls,
            )
        except Exception as exc:
            logger.error("Failed to parse Hugging Face response payload: %s", exc)
            raise LLMProviderError(
                f"Failed to parse Hugging Face response payload: {str(exc)}"
            ) from exc
