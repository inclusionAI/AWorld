#!/bin/bash
cd "$(dirname "$0")"

docker compose -f docker-compose-mcp.yml build && \
  docker compose -f docker-compose-mcp.yml up -d && \
  docker compose -f docker-compose-mcp.yml logs -f