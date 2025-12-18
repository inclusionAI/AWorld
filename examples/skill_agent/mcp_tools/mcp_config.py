import os

MCP_CONFIG = {
    "mcpServers": {
        "ms-playwright": {
            "command": "npx",
            "args": [
                "@playwright/mcp@0.0.37",
                "--no-sandbox",
                "--isolated",
                "--output-dir=/tmp/playwright",
                "--timeout-action=10000",
                # "--cdp-endpoint=http://localhost:9222"
            ],
            "env": {
                "PLAYWRIGHT_TIMEOUT": "120000",
                "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
            }
        },
        "document_server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_tools.document_server"
            ],
            "env": {
                "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
            }
        },
        "terminal-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_tools.terminal_server"
            ],
            "env": {
            }
        },
        "filesystem-server": {
            "type": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "@modelcontextprotocol/server-filesystem",
                "~/workspace"
            ]
        }
    }
}
