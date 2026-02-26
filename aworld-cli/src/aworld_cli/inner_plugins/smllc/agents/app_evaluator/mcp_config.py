mcp_config: {
    "mcpServers": {
                "terminal": {
                    "command": "python",
                    "args": ["-m", "examples.gaia.mcp_collections.tools.terminal"],
                    "env": {},
                    "client_session_timeout_seconds": 9999.0,
                },
                "ms-playwright": {
                    "command": "npx",
                    "args": [
                        "@playwright/mcp@latest",
                        "--no-sandbox",
                        "--isolated",
                        "--output-dir=/tmp/playwright",
                        "--timeout-action=10000",
                    ],
                    "env": {
                        "PLAYWRIGHT_TIMEOUT": "120000",
                        "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
                    }
                }
            }
}