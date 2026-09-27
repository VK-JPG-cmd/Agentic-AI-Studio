"""Application configuration module.

Handles environment variables and configuration loading with Pydantic Settings.
Secrets like HF_TOKEN are strictly typed as SecretStr to prevent accidental exposure.
"""

from functools import lru_cache
from typing import Optional
from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable fallback and validation."""

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    api_host: str = Field(default="0.0.0.0", validation_alias=AliasChoices("HOST", "API_HOST"))
    api_port: int = Field(default=8000, validation_alias=AliasChoices("PORT", "API_PORT"))

    # SQLite Database
    database_path: str = Field(default="agentic_ai.db", alias="DATABASE_PATH")

    # Hugging Face Inference API configuration
    hf_token: Optional[SecretStr] = Field(default=None, alias="HF_TOKEN")
    hf_model: str = Field(
        default="meta-llama/Meta-Llama-3-8B-Instruct",
        validation_alias=AliasChoices("HF_MODEL", "HF_MODEL_ID"),
    )
    hf_base_url: str = Field(
        default="https://router.huggingface.co/v1",
        alias="HF_BASE_URL",
    )
    llm_timeout: float = Field(
        default=30.0,
        validation_alias=AliasChoices("LLM_TIMEOUT", "HF_TIMEOUT"),
    )
    use_mock_llm: bool = Field(
        default=False,
        alias="USE_MOCK_LLM",
    )
    llm_fallback_to_mock: bool = Field(
        default=True,
        alias="LLM_FALLBACK_TO_MOCK",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def is_production(self) -> bool:
        """Return True if running in production."""
        return self.app_env.lower() == "production"

    @property
    def is_testing(self) -> bool:
        """Return True if running in test environment."""
        return self.app_env.lower() == "testing"

    @property
    def hf_model_id(self) -> str:
        """Backwards-compatible alias for hf_model."""
        return self.hf_model

    def get_hf_token_value(self) -> Optional[str]:
        """Safely retrieve the plain text token when needed for client initialization.
        
        Returns None if not configured. Never print or log this value.
        """
        if self.hf_token is None:
            return None
        return self.hf_token.get_secret_value()


@lru_cache()
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()
