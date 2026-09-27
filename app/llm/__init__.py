"""LLM provider package.

Provides a unified interface for language model providers and Hugging Face integration.
"""

from typing import Optional

from app.config import Settings, get_settings
from app.llm.base import (
    LLMAuthenticationError,
    LLMClient,
    LLMError,
    LLMMessage,
    LLMProviderError,
    LLMResponse,
    LLMTimeoutError,
)
from app.llm.huggingface import HuggingFaceLLMClient
from app.llm.mock import MockLLMClient


def get_llm_client(settings: Optional[Settings] = None) -> LLMClient:
    """Factory producing the configured LLM client."""
    cfg = settings or get_settings()
    if cfg.use_mock_llm:
        return MockLLMClient()
    return HuggingFaceLLMClient(settings=cfg)


__all__ = [
    "LLMClient",
    "HuggingFaceLLMClient",
    "MockLLMClient",
    "LLMMessage",
    "LLMResponse",
    "LLMError",
    "LLMAuthenticationError",
    "LLMTimeoutError",
    "LLMProviderError",
    "get_llm_client",
]
