import logging

from typing_extensions import Optional, List, Dict, Any

from aworld.mcp_client.utils import sandbox_mcp_tool_desc_transform, call_api, get_server_instance, cleanup_server
from aworld.sandbox.models import SandboxEnvType
from mcp.types import TextContent, ImageContent

from aworld.core.common import ActionResult


class McpServers:

    def __init__(
            self,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Dict[str, Any] = None,
    ) -> None:
        self.mcp_servers = mcp_servers
        self.mcp_config = mcp_config
        # Dictionary to store server instances {server_name: server_instance}
        self.server_instances = {}
        self.tool_list = None

    async def list_tools(
            self,
    ) -> List[Dict[str, Any]]:
        if self.tool_list:
            return self.tool_list
        if not self.mcp_servers or not self.mcp_config:
            return []
        try:
            self.tool_list = await sandbox_mcp_tool_desc_transform(self.mcp_servers, self.mcp_config)
            return self.tool_list
        except Exception as e:
            logging.warning(f"Failed to list tools: {e}")
            return []

    async def call_tool(
            self,
            action_list: List[Dict[str, Any]] = None,
    ) -> None:
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

                # Check server type
                server_type = None
                if self.mcp_config and self.mcp_config.get("mcpServers"):
                    server_config = self.mcp_config.get("mcpServers").get(server_name, {})
                    server_type = server_config.get("type", "")

                # For API type servers, use call_api function directly
                if server_type == "api":
                    call_result = await call_api(
                        server_name, tool_name, parameter, self.mcp_config
                    )
                    results.append(call_result)
                    continue

                # Prioritize using existing server instances
                server = self.server_instances.get(server_name)
                if server is None:
                    # If it doesn't exist, create a new instance and save it
                    server = await get_server_instance(server_name, self.mcp_config)
                    if server:
                        self.server_instances[server_name] = server
                        logging.info(f"Created and cached new server instance for {server_name}")
                    else:
                        logging.warning(f"Created new server failed: {server_name}")
                        continue

                # Use server instance to call the tool
                try:
                    call_result_raw = await server.call_tool(tool_name, parameter)

                    # Process the return result, consistent with the original logic
                    action_result = ActionResult(
                        content="",
                        keep=True
                    )

                    if call_result_raw and call_result_raw.content:
                        if isinstance(call_result_raw.content[0], TextContent):
                            action_result = ActionResult(
                                content=call_result_raw.content[0].text,
                                keep=True
                            )
                        elif isinstance(call_result_raw.content[0], ImageContent):
                            action_result = ActionResult(
                                content=f"data:image/jpeg;base64,{call_result_raw.content[0].data}",
                                keep=True
                            )

                    results.append(action_result)
                except Exception as e:
                    logging.warning(f"Error calling tool with cached server: {e}")
                    # If using cached server instance fails, try to clean up and recreate
                    if server_name in self.server_instances:
                        try:
                            await cleanup_server(self.server_instances[server_name])
                            del self.server_instances[server_name]
                        except:
                            pass
                    # Fall back to the original method
                    call_result = await call_tool(
                        server_name, tool_name, parameter, self.mcp_config
                    )
                    results.append(call_result)
        except Exception as e:
            logging.warning(f"Failed to call_tool: {e}")
            return None

        return results

    # Add cleanup method, called when Sandbox is destroyed
    async def cleanup(self):
        """Clean up all server connections"""
        for server_name, server in list(self.server_instances.items()):
            try:
                await cleanup_server(server)
                del self.server_instances[server_name]
                logging.info(f"Cleaned up server instance for {server_name}")
            except Exception as e:
                logging.warning(f"Failed to cleanup server {server_name}: {e}")

    def get_mcp_configs(
            self,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Optional[Any] = None,
            metadata: Optional[Dict[str, str]] = None,
            env_type: Optional[int] = None,
    ) -> None:
        try:
            if env_type == SandboxEnvType.LOCAL:
                return mcp_config
            elif env_type == SandboxEnvType.SUPERCOMPUTER:
                return self.get_mcp_configs_from_super(mcp_servers=mcp_servers, mcp_config=mcp_config,
                                                       metadata=metadata, env_type=env_type)
            elif env_type == SandboxEnvType.K8S:
                return self.get_mcp_configs_from_k8s(mcp_servers=mcp_servers, mcp_config=mcp_config, metadata=metadata,
                                                     env_type=env_type)
            return None
        except Exception as e:
            logging.warning(f"Failed to get_mcp_configs: {e}")
            return None

    def get_mcp_configs_from_super(
            self,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Optional[Any] = None,
            metadata: Optional[Dict[str, str]] = None,
            env_type: Optional[int] = None,
    ) -> None:
        try:
            if env_type != SandboxEnvType.SUPERCOMPUTER or not metadata or not metadata.get("host"):
                return mcp_config
            host = metadata.get("host")

            if not mcp_servers:
                return None
            if not mcp_config or mcp_config.get("mcpServers") is None:
                mcp_config = {
                    "mcpServers": {}
                }
            _mcp_servers = mcp_config.get("mcpServers")

            for server in mcp_servers:
                if server not in _mcp_servers:
                    _mcp_servers[server] = {
                        "type": "sse",
                        "url": f"{host}/{server}"
                    }

            return mcp_config
        except Exception as e:
            logging.warning(f"Failed to get_mcp_configs_from_super: {e}")
            return None

    def get_mcp_configs_from_k8s(
            self,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Optional[Any] = None,
            metadata: Optional[Dict[str, str]] = None,
            env_type: Optional[int] = None,
    ) -> None:
        try:
            if env_type != SandboxEnvType.K8S or not metadata or (
                    not metadata.get("cluster_ip") and not metadata.get("host")):
                return mcp_config
            host = metadata.get("host") or metadata.get("cluster_ip")

            if not mcp_servers:
                return None
            if not mcp_config or mcp_config.get("mcpServers") is None:
                mcp_config = {
                    "mcpServers": {}
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
            logging.warning(f"Failed to get_mcp_configs_from_k8s: {e}")
            return None
