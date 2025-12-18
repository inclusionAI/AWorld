import asyncio
import os
from aworld.sandbox import Sandbox


async def main():
    # Registration center, obtain the registration center address through env variables.
    # If `ENV_REGISTRY_URL` is not filled in, use: ~/workspace/registry.json
    os.environ["ENV_REGISTRY_URL"] = '**'
    os.environ["REGISTRY_TOKEN"] = '**'
    servers = {
        "filesystem": {
            "command": "npx",
            "args": [
                "-y",
                "@modelcontextprotocol/server-filesystem",
                "~/tmp"
            ]
        }
    }
    result = await Sandbox.register(servers=servers)

    # 2、call the environment
    sand_box = Sandbox(
        tools=["read_text_file"],
        mcp_servers=["filesystem"])

    # get tools
    mcp_tools = await sand_box.list_tools()
    print(mcp_tools)
    # call tools
    # await sand_box.call_tool(...)

    # 3、custom config
    sand_box = Sandbox(
        mcp_servers=["filesystem"],
        mcp_config={
            "mcpServers": {
                "filesystem1": {
                    "command": "npx",
                    "args": [
                        "-y",
                        "@modelcontextprotocol/server-filesystem",
                        "~/tmp"
                    ]
                }
            }
        })
    mcp_tools = await sand_box.list_tools()
    print(mcp_tools)

    # 4、External tools
    os.environ["CUSTOM_ENV_URL"] = "***"
    os.environ["CUSTOM_ENV_TOKEN"] = "***"
    os.environ["CUSTOM_ENV_IMAGE_VERSION"] = '***'
    sand_box = Sandbox(
        custom_env_tools={
            "hello-world-http": {
                "type": "remote",
                # https://username:password@gitee.com/***/npm_test.git
                # "branch": "main",
                # "tag": ""
                # "rev": ""
                "project_path": "http_mcp_demo",
            },
            "filesystem-server": {
                "type": "stdio",
                "command": "npx",
                "args": [
                    "-y",
                    "@modelcontextprotocol/server-filesystem",
                    "~/tmp"
                ]
            }
        }
    )
    mcp_tools = await sand_box.list_tools()
    print(mcp_tools)


if __name__ == "__main__":
    asyncio.run(main())
