import logging

from typing_extensions import Optional,List,Dict,Any

from aworld.sandbox.models import SandboxEnvType
from aworld.sandbox.run.mcp.utils import sandbox_mcp_tool_desc_transform, call_tool


class McpServers:

    def __init__(
            self,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Dict[str, Any] = None,
    ) -> None:
        self.mcp_servers = mcp_servers
        self.mcp_config = mcp_config

    async def list_tools(
            self,
    )->None:
        if not self.mcp_servers or not self.mcp_config:
            return None
        try:
            return await sandbox_mcp_tool_desc_transform(self.mcp_servers, self.mcp_config)
        except Exception as e:
            logging.warning(f"Failed to list tools: {e}")
            return None


    async def call_tool(
            self,
            action_list: List[Dict[str, Any]] = None,
    )->None:
        results = []
        if not action_list:
            return None
        try:
            for action in action_list:
                if not isinstance(action, dict):
                    action_dict = vars(action)
                else:
                    action_dict = action

                # Get values from dictionary
                server_name = action_dict.get("tool_name")
                tool_name = action_dict.get("action_name")
                parameter = action_dict.get("params")

                if not server_name or not tool_name:
                    continue

                call_result = await call_tool(
                    server_name, tool_name, parameter, self.mcp_config
                )
                results.append(call_result)
        except Exception as e:
            logging.warning(f"Failed to call_tool: {e}")
            return None

        return results

    def get_mcp_configs(
            self,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Optional[Any] = None,
            metadata: Optional[Dict[str, str]] = None,
            env_type: Optional[int] = None,
    ) -> None:
        try:
            if env_type != SandboxEnvType.K8S or not metadata or (not metadata.get("cluster_ip") and not metadata.get("host")):
                return mcp_config
            host =  metadata.get("host") or metadata.get("cluster_ip")

            if not mcp_servers:
                return None
            if not mcp_config or mcp_config.get("mcpServers") is None:
                mcp_config = {
                    "mcpServers":{}
                }
            _mcp_servers = mcp_config.get("mcpServers")

            for server in mcp_servers:
                if server not in _mcp_servers:
                    _mcp_servers[server] = {
                        "type": "api",
                        "url": f"http://{host}:80/{server}"
                    }

            return mcp_config
        except Exception as e:
            logging.warning(f"Failed to get_mcp_configs: {e}")
            return None