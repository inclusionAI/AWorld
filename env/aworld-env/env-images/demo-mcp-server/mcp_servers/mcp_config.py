import os

import dotenv

dotenv.load_dotenv(verbose=True, override=True)

black_list_tools = [
    ("mcp-server-name", "tool-name "),
]


mcp_config = {
    "mcpServers": {
        "ms-playwright": {
            "command": "npx",
            "args": ["@playwright/mcp@v0.0.30", "--no-sandbox"],
            "env": {
                "PLAYWRIGHT_TIMEOUT": "120000",
                "SESSION_REQUEST_CONNECT_TIMEOUT": "120",
            },
        },
    }
}


if __name__ == "__main__":
    print("✅ MCP Server List:")
    print(list(mcp_config.get("mcpServers").keys()))
