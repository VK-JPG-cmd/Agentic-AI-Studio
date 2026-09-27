"""Base abstractions for LLM providers.

Defines the contract for LLM interaction, standardized message structures,
and provider-agnostic exceptions.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class LLMError(Exception):
    """Base exception for all LLM client failures."""
    pass


class LLMAuthenticationError(LLMError):
    """Raised when authentication credentials (e.g., HF_TOKEN) are missing, invalid, or unauthorized."""
    pass


class LLMTimeoutError(LLMError):
    """Raised when an LLM provider request exceeds the configured timeout."""
    pass


class LLMProviderError(LLMError):
    """Raised when the LLM provider returns an HTTP error, rate limit, or unexpected response."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMMessage(BaseModel):
    """Represents a single message in a conversation sequence."""

    role: str = Field(..., description="Message author role: 'system', 'user', or 'assistant'")
    content: str = Field(..., description="Message text content")

    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary matching standard chat completion API formats."""
        return {"role": self.role, "content": self.content}


from app.models.schemas import ToolCall


class LLMResponse(BaseModel):
    """Standardized response produced by any LLM provider."""

    content: str = Field(default="", description="Generated text response from the model")
    model: str = Field(..., description="Identifier of the model that generated the response")
    role: str = Field(default="assistant", description="Role of the responding entity")
    finish_reason: Optional[str] = Field(default=None, description="Reason generation finished")
    usage: Optional[Dict[str, Any]] = Field(default=None, description="Token usage statistics")
    tool_calls: Optional[List[ToolCall]] = Field(
        default=None,
        description="Structured tool calls requested by the model",
    )


class LLMClient(ABC):
    """Abstract base class for LLM clients."""

    @abstractmethod
    def generate(
        self,
        messages: List[Union[LLMMessage, Dict[str, str]]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> LLMResponse:
        """Generate a completion response from the model for the given messages."""
        pass
