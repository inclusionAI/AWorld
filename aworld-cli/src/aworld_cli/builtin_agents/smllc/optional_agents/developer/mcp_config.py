# External providers used only by the standalone developer sandbox. Terminal
# and filesystem execution are supplied by Sandbox(builtin_tools=...).
mcp_config = {
    "mcpServers": {
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
