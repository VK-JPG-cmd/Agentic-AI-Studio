#!/usr/bin/env bash
# Render Start Script for Native Python Environment
set -o errexit

PORT="${PORT:-10000}"
echo "===> Launching Agentic AI Studio on port ${PORT}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
