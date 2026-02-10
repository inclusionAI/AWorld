## Install AWorld

The Environment is included in the AWorld framework and does not require separate installation.

```bash
pip install aworld
```

## Minimal Example

```python
import asyncio
from aworld.sandbox import Sandbox

# Configure MCP servers
mcp_config = {
    "mcpServers": {
        "gaia-mcp": {
            "type": "streamable-http",
            "url": "https://mcp.example.com/mcp",
            "headers": {
                "Authorization": "Bearer YOUR_TOKEN"
            }
        }
    }
}

async def main():
    # Create an Environment instance
    sandbox = Sandbox(mcp_config=mcp_config)
    
    # List available tools
    tools = await sandbox.list_tools()
    print(f"Number of available tools: {len(tools)}")
    
    # Clean up resources
    await sandbox.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
```

### Online Access to Complex Environments
Provisioning rich environments is hard—packages conflict, APIs need keys, concurrency must scale. We make it painless with three access modes:
1. Use our default hosted setup (tooling with usage costs includes a limited free tier).
2. Bring your own API keys for unrestricted access (coming soon).
3. Pull our Docker images and run everything on your own infrastructure (coming soon).

```python
import os
import asyncio
from aworld.sandbox import Sandbox

INVITATION_CODE = os.environ.get("INVITATION_CODE", "")

mcp_config = {
    "mcpServers": {
        "gaia_server": {
            "type": "streamable-http",
            "url": "https://playground.aworldagents.com/environments/mcp",
            "timeout": 600,
            "sse_read_timeout": 600,
            "headers": {
                "ENV_CODE": "gaia",
                "Authorization": f"Bearer {INVITATION_CODE}",
            }
        }
    }
}

async def _list_tools():
    sand_box = Sandbox(mcp_config=mcp_config, mcp_servers=["gaia_server"])
    return await sand_box.mcpservers.list_tools()

if __name__ == "__main__":
    tools = asyncio.run(_list_tools())
    print(tools)
```

![](../imgs/how_to_access_env.gif)
