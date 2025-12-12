import asyncio
import os
from aworld.sandbox import Sandbox


async def main():
    #1、注册中心，通过环境变量获取注册中心地址，如果不填写默认本地，默认注册中心本地地址是：~/workspace/registry.json
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
    #
    # 2、使用注册中心调用环境
    sand_box = Sandbox(
        tools=["read_text_file"],
        mcp_servers=["filesystem"])

    # 获取相关环境工具和调用工具
    mcp_tools = await sand_box.list_tools()  # 调用工具直接call_tool
    print(mcp_tools)
    #
    # 3、使用自定义配置使用环境
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
    # 获取相关环境工具和调用工具
    mcp_tools = await sand_box.list_tools()  # 调用工具直接call_tool
    print(mcp_tools)

    # 4、外部工具安装到环境内部
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
    # 获取相关环境工具和调用工具
    mcp_tools = await sand_box.list_tools()


if __name__ == "__main__":
    asyncio.run(main())
