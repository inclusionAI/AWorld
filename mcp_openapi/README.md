# MCP OpenAPI

A simple OpenAPI proxy for Model Context Protocol (MCP) servers.

## Requirements

- Python 3.11 or higher

## Installation

```bash
pip install mcp_openapi
```

## Usage

### Command Line

```bash
# Start the server with a configuration file
mcp-openapi --config_path path/to/config.json --host 127.0.0.1 --port 9000
```

### Configuration File Example

```json
{
  "mcpServers": {
    "maps": {
      "type": "sse",
      "url": "http://localhost:8000/sse"
    },
    "image-processor": {
      "type": "stdio",
      "command": "python",
      "args": ["path/to/mcp_server.py"],
      "env": {
        "MODEL_PATH": "/path/to/model"
      }
    }
  }
}
```

### Python API

```python
import asyncio
from mcp_openapi import run  

async def main():
    await run(
        host="127.0.0.1",
        port=9000,
        config_path="path/to/config.json"
    )

if __name__ == "__main__":
    asyncio.run(main())
```

## Features

- Automatically creates REST API endpoints for MCP tools
- Supports both SSE and stdio MCP server types
- Each request creates a new session for thread safety
- Provides a unified API for accessing multiple MCP servers

## License

MIT 