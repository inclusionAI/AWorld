import logging
import json
import traceback

from typing_extensions import Optional, List, Dict, Any

from aworld.mcp_client.utils import sandbox_mcp_tool_desc_transform, call_api, get_server_instance, cleanup_server, \
    call_function_tool
from mcp.types import TextContent, ImageContent

from aworld.core.common import ActionResult


class McpServers:

    def __init__(
            self,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Dict[str, Any] = None,
            sandbox = None,
    ) -> None:
        self.mcp_servers = mcp_servers
        self.mcp_config = mcp_config
        self.sandbox = sandbox
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
            traceback.print_exc()
            logging.warning(f"Failed to list tools: {e}")
            return []

    async def call_tool(
            self,
            action_list: List[Dict[str, Any]] = None,
            task_id: str = None,
            session_id: str = None
    ) -> List[ActionResult]:
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
                result_key = f"{server_name}__{tool_name}"
                

                operation_info = {
                    "server_name": server_name,
                    "tool_name": tool_name,
                    "params": parameter
                }
                
                if parameter is None:
                    parameter = {}
                # if task_id:
                #     parameter["task_id"] = task_id
                # if session_id:
                #     parameter["session_id"] = session_id

                if not server_name or not tool_name:
                    continue

                # Check server type
                server_type = None
                if self.mcp_config and self.mcp_config.get("mcpServers"):
                    server_config = self.mcp_config.get("mcpServers").get(server_name, {})
                    server_type = server_config.get("type", "")

                if server_type == "function_tool":
                    try:
                        call_result = await call_function_tool(
                            server_name, tool_name, parameter, self.mcp_config
                        )
                        results.append(call_result)

                        self._update_metadata(result_key, call_result, operation_info)
                    except Exception as e:
                        logging.warning(f"Error calling function_tool tool: {e}")
                        self._update_metadata(result_key, {"error": str(e)}, operation_info)
                    continue

                # For API type servers, use call_api function directly
                if server_type == "api":
                    try:
                        call_result = await call_api(
                            server_name, tool_name, parameter, self.mcp_config
                        )
                        results.append(call_result)

                        self._update_metadata(result_key, call_result, operation_info)
                    except Exception as e:
                        logging.warning(f"Error calling API tool: {e}")
                        self._update_metadata(result_key, {"error": str(e)}, operation_info)
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

                        self._update_metadata(result_key, {"error": "Failed to create server instance"}, operation_info)
                        continue

                # Use server instance to call the tool
                call_result_raw = None
                action_result = ActionResult(
                    tool_name=server_name,
                    action_name=tool_name,
                    content="",
                    keep=True
                )
                max_retry = 3
                for i in range(max_retry):
                    try:
                        call_result_raw = await server.call_tool(tool_name, parameter)
                        break
                    except Exception as e:
                        logging.warning(f"Error calling tool error: {e}")
                logging.info(f"tool_name:{server_name},action_name:{tool_name},call-mcp-tool-result: {call_result_raw}")
                if not call_result_raw:
                    logging.warning(f"Error calling tool with cached server: {e}")

                    self._update_metadata(result_key, {"error": str(e)}, operation_info)

                    # If using cached server instance fails, try to clean up and recreate
                    if server_name in self.server_instances:
                        try:
                            await cleanup_server(self.server_instances[server_name])
                            del self.server_instances[server_name]
                        except Exception as e:
                            logging.warning(f"Failed to cleanup server {server_name}: {e}")
                else:
                    if call_result_raw and call_result_raw.content:
                        if isinstance(call_result_raw.content[0], TextContent):
                            action_result = ActionResult(
                                tool_name=server_name,
                                action_name=tool_name,
                                content=call_result_raw.content[0].text,
                                keep=True,
                                metadata=call_result_raw.content[0].model_extra.get(
                                    "metadata", {}
                                ),
                            )
                        elif isinstance(call_result_raw.content[0], ImageContent):
                            action_result = ActionResult(
                                tool_name=server_name,
                                action_name=tool_name,
                                content=f"data:image/jpeg;base64,{call_result_raw.content[0].data}",
                                keep=True,
                                metadata=call_result_raw.content[0].model_extra.get("metadata", {}),
                            )
                    results.append(action_result)
                    self._update_metadata(result_key, action_result, operation_info)

        except Exception as e:
            logging.warning(f"Failed to call_tool: {e}")
            return None

        return results
    
    def _update_metadata(self, result_key: str, result: Any, operation_info: Dict[str, Any]):
        """
        Update sandbox metadata with a single tool call result

        Args:
            result_key: The key name in metadata
            result: Tool call result
            operation_info: Operation information
        """
        if not self.sandbox or not hasattr(self.sandbox, '_metadata'):
            return
            
        try:
            metadata = self.sandbox._metadata.get("mcp_metadata",{})
            tmp_data = {
                "input": operation_info,
                "output": result
            }
            if not metadata:
                metadata["mcp_metadata"] = {}
                metadata["mcp_metadata"][result_key] = [tmp_data]
                self.sandbox._metadata["mcp_metadata"] = metadata
                return

            _metadata = metadata.get(result_key, [])
            if not _metadata:
                _metadata[result_key] = [_metadata]
            else:
                _metadata[result_key].append(tmp_data)
            metadata[result_key] = _metadata
            self.sandbox._metadata["mcp_metadata"] = metadata
            return

        except Exception as e:
            logging.warning(f"Failed to update sandbox metadata: {e}")

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

