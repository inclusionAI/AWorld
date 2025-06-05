#!/usr/bin/env bash
PORT="${PORT:-9099}"
HOST="${HOST:-0.0.0.0}"
# Default value for PIPELINES_DIR
PIPELINES_DIR=${PIPELINES_DIR:-./aworldspace/agents}

UVICORN_LOOP="${UVICORN_LOOP:-auto}"


uvicorn main:app --host "$HOST" --port "$PORT" --forwarded-allow-ips '*' --loop "$UVICORN_LOOP"

