# MCP配置文件 - SearchReasoningAgent

```python
# mcp_config.py - MCP服务器配置

mcp_config = {
    "mcpServers": {
        "search": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.search"
            ],
            "env": {
                "GOOGLE_API_KEY": "${GOOGLE_API_KEY}",
                "GOOGLE_CSE_ID": "${GOOGLE_CSE_ID}"
            },
            "client_session_timeout_seconds": 9999.0
        },
        "reasoning": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.intelligence.think"
            ],
            "env": {},
            "client_session_timeout_seconds": 9999.0
        },
        "terminal": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.terminal"
            ],
            "env": {},
            "client_session_timeout_seconds": 9999.0
        },
        "browser": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.browser"
            ],
            "env": {
                "LLM_MODEL_NAME": "${LLM_MODEL_NAME}",
                "LLM_API_KEY": "${LLM_API_KEY}",
                "LLM_BASE_URL": "${LLM_BASE_URL}"
            },
            "client_session_timeout_seconds": 9999.0
        }
    }
}
```