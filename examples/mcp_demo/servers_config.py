mcp_config = {
    "mcpServers": {
        "aworld": {
            "url": "http://localhost:20000/sse"
        },
        "amap-amap-sse": {
            "type": "sse",
            "url": "https://mcp.amap.com/sse?key=YOUR_API_KEY",
            "timeout": 5,
            "sse_read_timeout": 300
        },
        "tavily-mcp": {
            "type": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "tavily-mcp@0.1.2"
            ],
            "env": {
                "TAVILY_API_KEY": "YOUR_API_KEY"
            }
        },
        "simple-calculator": {
            "type": "sse",
            "url": "http://127.0.0.1:8500/calculator/sse",
            "timeout": 5,
            "sse_read_timeout": 300
        }
    }
}
