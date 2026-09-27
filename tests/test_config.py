"""Tests for configuration loading and security."""

from app.config import Settings, get_settings


def test_config_loading_from_env() -> None:
    """Ensure settings correctly read configured environment variables."""
    settings = get_settings()
    assert settings.app_env == "testing"
    assert settings.is_testing is True
    assert settings.is_production is False
    assert settings.hf_model == "meta-llama/Meta-Llama-3-8B-Instruct"
    assert settings.hf_model_id == "meta-llama/Meta-Llama-3-8B-Instruct"
    assert settings.llm_timeout == 30.0


def test_secret_is_masked() -> None:
    """Ensure HF_TOKEN is never leaked in string representation or object repr."""
    settings = get_settings()
    raw_token = "hf_test_dummy_token_12345"

    assert settings.get_hf_token_value() == raw_token
    # Representation should not reveal secret
    assert raw_token not in repr(settings.hf_token)
    assert raw_token not in str(settings.hf_token)
    assert "**********" in repr(settings.hf_token)


def test_custom_settings_override() -> None:
    """Test manual settings instantiation with overrides."""
    custom = Settings(
        APP_ENV="production",
        API_PORT=9000,
        DATABASE_PATH="custom.db",
        HF_TOKEN="custom_token",
    )
    assert custom.app_env == "production"
    assert custom.is_production is True
    assert custom.api_port == 9000
    assert custom.get_hf_token_value() == "custom_token"


def test_hf_model_and_timeout_configuration() -> None:
    """Test HF_MODEL configuration with aliases and LLM_TIMEOUT."""
    s1 = Settings(HF_MODEL="Qwen/Qwen2.5-72B-Instruct", LLM_TIMEOUT=45.0)
    assert s1.hf_model == "Qwen/Qwen2.5-72B-Instruct"
    assert s1.hf_model_id == "Qwen/Qwen2.5-72B-Instruct"
    assert s1.llm_timeout == 45.0

    # Also test backwards-compatible alias HF_MODEL_ID
    s2 = Settings(HF_MODEL_ID="mistralai/Mistral-7B-Instruct-v0.3")
    assert s2.hf_model == "mistralai/Mistral-7B-Instruct-v0.3"
