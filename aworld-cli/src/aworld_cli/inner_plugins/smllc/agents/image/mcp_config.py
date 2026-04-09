import sys

mcp_config = {
    "mcpServers": {
        "terminal": {
            "command": sys.executable,
            "args": ["-m", "examples.gaia.mcp_collections.tools.terminal"],
            "env": {},
            "client_session_timeout_seconds": 9999.0,
        }
    }
}