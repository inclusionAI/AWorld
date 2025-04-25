mcp_config = {
  "mcpServers": {
    "aworld": {
      "url": "http://localhost:20000/sse"
    },
    "amap-amap-sse": {
      "url": "https://mcp.amap.com/sse?key=f569d30e3e381f13b93ea4635652bcdf",
      "timeout": 5,
      "sse_read_timeout": 300
    },
    "tavily-mcp": {
      "command": "npx",
      "args": [
        "-y",
        "tavily-mcp@0.1.2"
      ],
      "env": {
        "TAVILY_API_KEY": "YOUR_API_KEY"
      }
    }
  }
}
