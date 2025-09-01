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

    def create_env(
        self,
        *,
        llm_base_url: str,
        llm_model_name: str,
        llm_api_key: str,
        jina_api_key: str,
        tavily_api_key: str,
        google_api_key: str,
        google_cse_id: str,
        datalab_api_key: str,
        e2b_api_key: str,
    ):
        is_ready = self._ensure_env_file_ready(
            llm_base_url,
            llm_model_name,
            llm_api_key,
            jina_api_key,
            tavily_api_key,
            google_api_key,
            google_cse_id,
            datalab_api_key,
            e2b_api_key,
        )
        assert is_ready, "Env file is not ready!"

        image_ready = self._build_image()
        assert image_ready, "Image is not ready!"

        service_config = self._start_service()
        assert service_config, "Service config is not ready!"

        service_ready = self._check_service_ready(service_config)
        assert service_ready, "Service is not ready!"

        return service_config

    def _ensure_env_file_ready(
        self,
        llm_base_url: str,
        llm_model_name: str,
        llm_api_key: str,
        jina_api_key: str,
        tavily_api_key: str,
        google_api_key: str,
        google_cse_id: str,
        datalab_api_key: str,
        e2b_api_key: str,
    ):
        env_template_file = (
            self.env_dir / "gaia-mcp-server" / "mcp_servers" / ".env_template"
        )
        env_file = self.env_dir / "gaia-mcp-server" / "mcp_servers" / ".env"

        if env_file.exists():
            logger.info(f"Env file exists, check empty values: {env_file}")
        else:
            logger.info(f"Env file not exists, creating from template: {env_file}")
            shutil.copy(env_template_file, env_file)

        def _update_config(key, value):
            set_key(env_file, key, value, quote_mode="never")

        _update_config("MCP_LLM_BASE_URL", llm_base_url)
        _update_config("MCP_LLM_MODEL_NAME", llm_model_name)
        _update_config("MCP_LLM_API_KEY", llm_api_key)

        _update_config("MCP_LLM_BASE_URL", llm_base_url)
        _update_config("MCP_LLM_MODEL_NAME", llm_model_name)
        _update_config("MCP_LLM_API_KEY", llm_api_key)

        _update_config("JINA_API_KEY", jina_api_key)
        _update_config("TAVILY_API_KEY", tavily_api_key)
        _update_config("GOOGLE_API_KEY", google_api_key)
        _update_config("GOOGLE_CSE_ID", google_cse_id)
        _update_config("DATALAB_API_KEY", datalab_api_key)
        _update_config("E2B_API_KEY", e2b_api_key)

        empty_values = {
            k: v
            for k, v in dotenv_values(env_file, interpolate=True).items()
            if v is None or v == ""
        }
        if empty_values:
            empty_values_str = "\n  - " + "\n  - ".join(empty_values.keys())
            logger.info(
                f"Empty values found in env file: {env_file}\n{empty_values_str}"
            )
            return False
        return True

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
    train_env.create_env(
        llm_base_url="https://api.openai.com/v1",
        llm_model_name="gpt-4o",
        llm_api_key="sk-proj-1234567890",
        jina_api_key="sk-proj-1234567890",
        tavily_api_key="sk-proj-1234567890",
        google_api_key="sk-proj-1234567890",
        google_cse_id="sk-proj-1234567890",
        datalab_api_key="sk-proj-1234567890",
        e2b_api_key="sk-proj-1234567890",
    )
