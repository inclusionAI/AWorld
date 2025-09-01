import logging
from pathlib import Path
import shutil
import subprocess
import traceback
from dotenv import dotenv_values, set_key
from mcp import ClientSession
from mcp.types import (
    LoggingMessageNotificationParams,
    ElicitResult,
    ElicitRequestParams,
)
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.context import RequestContext

logger = logging.getLogger(__name__)


class TranEnv:

    def __init__(self):
        self.base_dir = Path(__file__).parent.parent
        self.env_dir = self.base_dir / "env"

    def get_env_config(self):
        if self.mcp_config:
            return {
                "mcp_config": self.mcp_config,
                "mcp_servers": list(server_name for server_name in self.mcp_config.get("mcpServers", {}).keys())
            }
        return None

    def create_env(self, name: str = "mcp_server", mode: str = "local") -> dict:
        if mode == "local":
            image_ready = self._build_image()
            assert image_ready, "Image is not ready!"

            service_config = self._start_service()
            assert service_config, "Service config is not ready!"

            service_ready = self._check_service_ready(service_config)
            assert service_ready, "Service is not ready!"

            self.mcp_config = {
                "mcpServers": {
                    name: {
                        "type": "streamable-http",
                        "url": service_config.get("url", "http://localhost:8000/mcp"),
                        "headers": {
                            "Authorization": f"Bearer {service_config.get('token')}",
                            "MCP_SERVERS": "",
                        },
                        "timeout": 600,
                        "sse_read_timeout": 600,
                        "client_session_timeout_seconds": 600,
                    }
                }
            }
            return service_config
        else:
            logger.warn(f"Mode {mode} is not supported!")
            return None

    def _build_image(self):
        try:
            subprocess.check_call(
                ["sh", "build-image.sh"],
                cwd=self.env_dir / "virtualpc-mcp" / "mcp_server",
            )
            subprocess.check_call(
                ["sh", "build-image.sh"], cwd=self.env_dir / "gaia-mcp-server"
            )

            return True
        except Exception as e:
            logger.error(f"Failed to build image: {traceback.format_exc()}")
            return False

    def _start_service(self):
        try:
            subprocess.check_call(
                ["sh", "run-local.sh"],
                cwd=self.env_dir / "virtualpc-mcp",
            )
            return {
                "url": "http://localhost:8000/mcp",
                "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHAiOiJsb2NhbF9kZWJ1ZyIsInZlcnNpb24iOjEsInRpbWUiOjE3NTYzOTUzNzIuMTg0MDc0NH0.SALKn1dxEzsdX82-e3jAJANAo_kE4NO4192Epw5rYmQ",
            }
        except Exception as e:
            logger.error(f"Failed to start service: {traceback.format_exc()}")
            return None

    async def _check_service_ready(self, service_config: dict):
        try:
            url = service_config["url"]
            headers = {
                "Authorization": f"Bearer {service_config['token']}",
            }

            async with streamablehttp_client(
                url=url,
                headers=headers,
            ) as (
                read_stream,
                write_stream,
                get_session_id,
            ):
                async with ClientSession(
                    read_stream=read_stream,
                    write_stream=write_stream,
                ) as session:
                    logger.info(f"MCP client connected: url={url}")
                    await session.initialize()
                    logger.info(
                        f"MCP client session initialized: url={url}, session_id={get_session_id()}"
                    )

                    ls = await session.list_tools()
                    tools = ls.tools
                    tool_str = "\n  - ".join([t.name for t in tools])
                    logger.info(f"list_tools return:\n  - {tool_str}")

                    tool_name = "read_url"
                    args = {
                        "url": "https://www.alipay.com",
                    }
                    result = await session.call_tool(
                        tool_name,
                        args,
                    )
                    logger.info(f"tool result: {result.content[0].text[:300]}")
                    return True
        except Exception as e:
            logger.error(f"Failed to check service ready: {traceback.format_exc()}")
            return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    train_env = TranEnv()
    train_env.create_env()
