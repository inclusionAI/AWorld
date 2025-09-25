#!/bin/sh

cd "$(dirname "$0")"

docker build -t gaia-mcp-server -f Dockerfile . && \

echo "✅ Build image success: gaia-mcp-server"
