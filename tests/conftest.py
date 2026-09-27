"""Pytest configuration and shared fixtures."""

import os
from typing import Generator
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db.database import init_db
from app.main import app


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path: os.PathLike) -> Generator[None, None, None]:
    """Isolate environment and configure temporary SQLite database for tests."""
    test_db = str(tmp_path / "test_agentic_ai.db")
    os.environ["APP_ENV"] = "testing"
    os.environ["DATABASE_PATH"] = test_db
    os.environ["HF_TOKEN"] = "hf_test_dummy_token_12345"
    os.environ["HF_MODEL"] = "meta-llama/Meta-Llama-3-8B-Instruct"

    get_settings.cache_clear()
    init_db(test_db)

    yield

    get_settings.cache_clear()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Provide a FastAPI TestClient."""
    with TestClient(app) as test_client:
        yield test_client
