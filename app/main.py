"""FastAPI application entry point.

Defines REST endpoints for the Agentic AI system:
- GET /health: Health check, database connectivity, and LLM configuration readiness
- POST /agent/run: Execute an agent task through LLM orchestration
- POST /agent/dev/llm-test: Dev test endpoint verifying LLM connectivity
"""

from contextlib import asynccontextmanager
import logging
import os
from typing import Any, AsyncGenerator, Dict

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agent.agent import Agent
from app.config import get_settings
from app.db.database import get_db_connection, init_db
from app.llm import (
    LLMAuthenticationError,
    LLMError,
    LLMMessage,
    LLMProviderError,
    LLMTimeoutError,
    get_llm_client,
)
from app.models.schemas import AgentResponse, UserRequest
from app.tools.registry import get_default_registry

# Configure logging
logging.basicConfig(
    level=getattr(logging, get_settings().log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agentic_ai")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown lifecycle."""
    settings = get_settings()
    logger.info("Initializing Agentic AI service (env: %s)...", settings.app_env)
    
    # Initialize SQLite database schema
    init_db(settings.database_path)
    logger.info("Database schema initialized at '%s'", settings.database_path)
    
    yield
    
    logger.info("Agentic AI service shutting down cleanly.")


app = FastAPI(
    title="Agentic AI API",
    description="Hackathon-grade Agentic AI service with LLM integration and persistent state.",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception handlers for LLM errors to return controlled, useful messages without leaking secrets
@app.exception_handler(LLMAuthenticationError)
async def llm_auth_exception_handler(request: Request, exc: LLMAuthenticationError) -> JSONResponse:
    logger.error("Authentication error contacting LLM provider: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Authentication with LLM provider failed. Please check HF_TOKEN configuration."},
    )


@app.exception_handler(LLMTimeoutError)
async def llm_timeout_exception_handler(request: Request, exc: LLMTimeoutError) -> JSONResponse:
    logger.error("Timeout contacting LLM provider: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        content={"detail": "The LLM provider request timed out. Please retry."},
    )


@app.exception_handler(LLMProviderError)
async def llm_provider_exception_handler(request: Request, exc: LLMProviderError) -> JSONResponse:
    logger.error("LLM provider returned error: %s", exc)
    status_code = status.HTTP_502_BAD_GATEWAY
    if exc.status_code and exc.status_code in (429, 503):
        status_code = exc.status_code
    return JSONResponse(
        status_code=status_code,
        content={"detail": f"LLM provider error: {str(exc)}"},
    )


@app.exception_handler(LLMError)
async def llm_generic_exception_handler(request: Request, exc: LLMError) -> JSONResponse:
    logger.error("Generic LLM error: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": "An error occurred while communicating with the LLM provider."},
    )


@app.get("/health", status_code=status.HTTP_200_OK, tags=["System"])
def health_check() -> Dict[str, Any]:
    """Health check endpoint verifying database connectivity and LLM configuration."""
    settings = get_settings()
    db_status = "connected"
    
    try:
        with get_db_connection(settings.database_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1;")
            cursor.fetchone()
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        db_status = f"unhealthy: {exc}"

    registry = get_default_registry()
    has_token = bool(settings.get_hf_token_value() and settings.get_hf_token_value().strip())

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "app_env": settings.app_env,
        "database": db_status,
        "tools_available": [tool.name for tool in registry.list_tools()],
        "llm_model": settings.hf_model,
        "llm_configured": has_token,
    }


@app.post(
    "/agent/run",
    response_model=AgentResponse,
    status_code=status.HTTP_200_OK,
    tags=["Agent"],
)
def run_agent(request: UserRequest) -> AgentResponse:
    """Execute an agent goal through the LLM pipeline and persist audit history."""
    settings = get_settings()
    agent = Agent(settings=settings)
    return agent.run(request)


@app.post(
    "/agent/dev/llm-test",
    status_code=status.HTTP_200_OK,
    tags=["Development"],
)
def dev_test_llm() -> Dict[str, Any]:
    """Development test endpoint to verify LLM client processing with a fixed test prompt.
    
    Disabled in production to protect environment integrity.
    """
    settings = get_settings()
    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Development test endpoints are disabled in production.",
        )

    test_prompt = "Say hello in one sentence."
    llm_client = get_llm_client(settings)
    
    response = llm_client.generate(
        messages=[LLMMessage(role="user", content=test_prompt)],
        max_tokens=64,
    )

    return {
        "status": "success",
        "prompt": test_prompt,
        "response": response.content,
        "model": response.model,
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all exception handler to ensure no internal secrets or sensitive traces leak."""
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error occurred."},
    )


# Mount built React frontend if present
frontend_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")
if os.path.exists(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    cfg = get_settings()
    uvicorn.run("app.main:app", host=cfg.api_host, port=cfg.api_port, reload=False)

