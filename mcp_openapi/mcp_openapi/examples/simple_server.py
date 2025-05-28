"""
Simple example showing how to use the MCP OpenAPI server
"""

import asyncio
import os
from mcp_openapi import run

async def main():
    # Get the path to the example config file
    current_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(current_dir, "config_example.json")
    
    # Run the server
    await run(
        host="127.0.0.1",
        port=9000,
        config_path=config_path
    )

if __name__ == "__main__":
    asyncio.run(main()) 