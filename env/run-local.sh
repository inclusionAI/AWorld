#!/bin/sh
cd "$(dirname "$0")"

sh gaia-mcp-server/run.sh && \

export MCP_SERVER_URL=http://localhost:8080/mcp
export MCP_SERVER_TOKEN=1234567890

open 'http://localhost:5901/vnc.html?autoconnect=true'