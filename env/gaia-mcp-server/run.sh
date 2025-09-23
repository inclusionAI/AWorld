#!/bin/sh
cd "$(dirname "$0")"

sh ../mcp-server-base/build-image.sh && \

docker compose up --build --force-recreate -d && \

export MCP_SERVER_URL=http://localhost:8080/mcp && \

cat << 'EOF'
✅ Start mcp server success
Please using the following config to connect to the mcp server:
```json
{
  "gaia-mcp-server": {
    "type": "streamable_http",
    "url": "http://localhost:8000/mcp",
    "timeout": 6000,
    "sse_read_timeout": 6000,
    "client_session_timeout_seconds": 6000
  }
}
```
EOF
