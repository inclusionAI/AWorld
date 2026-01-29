import json
import traceback
import os
import uuid

from aworld.core.context.base import Context
from aworld.logs.util import logger


from aworld.utils.common import sync_exec

from aworld.events.util import send_message

from aworld.core.event.base import Message, Constants, BackgroundTaskMessage, TopicType
from typing_extensions import Optional, List, Dict, Any
from typing import TYPE_CHECKING

from aworld.mcp_client.utils import mcp_tool_desc_transform, call_api, get_server_instance, cleanup_server, \
    call_function_tool, mcp_tool_desc_transform_v2, mcp_tool_desc_transform_v2_reuse, call_mcp_tool_with_exit_stack, call_mcp_tool_with_reuse
from mcp.types import TextContent, ImageContent

from aworld.core.common import ActionResult, Observation
from aworld.output import Output

# Import env_channel for subscription
# from env_channel import EnvChannelMessage, env_channel_sub

if TYPE_CHECKING:
    from aworld.sandbox.base import Sandbox


class McpServers:

    def __init__(
            self,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Dict[str, Any] = None,
            sandbox: Optional["Sandbox"] = None,
            black_tool_actions: Dict[str, List[str]] = None,
            skill_configs: Dict[str, Any] = None,
            tool_actions: Optional[List[str]] = None,
    ) -> None:
        self.mcp_servers = mcp_servers
        self.mcp_config = mcp_config
        self.skill_configs = skill_configs or {}
        self.sandbox = sandbox
        # Dictionary to store server instances {server_name: server_instance}
        self.server_instances = {}
        self.server_instances_session = {}
        self.tool_list = None
        self.black_tool_actions = black_tool_actions or {}
        self.map_tool_list = {}
        self.tool_actions = tool_actions or []
        # Mapping from tool_key to env_content parameter name
        # Format: {"server_name__tool_name": "env_content"}
        self._env_content_param_mapping: Dict[str, str] = {}

    def _should_reuse(self) -> bool:
        """Check if server connections should be reused based on sandbox.reuse."""
        return bool(self.sandbox and hasattr(self.sandbox, 'reuse') and self.sandbox.reuse)

    async def list_tools(self, context: Context = None) -> List[Dict[str, Any]]:
        if self.tool_list:
            return self.tool_list
        if not self.mcp_servers or not self.mcp_config:
            return []
        try:
            sandbox_id = self.sandbox.sandbox_id if self.sandbox is not None else None
            if self._should_reuse():
                self.tool_list = await mcp_tool_desc_transform_v2_reuse(
                    tools=self.mcp_servers,
                    mcp_config=self.mcp_config,
                    context=context,
                    server_instances=self.server_instances,
                    black_tool_actions=self.black_tool_actions,
                    sandbox_id=sandbox_id,
                    tool_actions=self.tool_actions,
                    server_instances_session=self.server_instances_session
                )
            else:
                self.tool_list = await mcp_tool_desc_transform_v2(
                    tools=self.mcp_servers,
                    mcp_config=self.mcp_config,
                    context=context,
                    server_instances=self.server_instances,
                    black_tool_actions=self.black_tool_actions,
                    sandbox_id=sandbox_id,
                    tool_actions=self.tool_actions
                )
            if self.sandbox and self.tool_list:
                self._process_and_save_env_content_mapping()

            return self.tool_list
        except Exception as e:
            logger.warning(f"Failed to list tools: {traceback.format_exc()}")
            return []


    async def check_tool_params(self, context: Context, server_name: str, tool_name: str,
                                parameter: Dict[str, Any]) -> Any:
        """
        Check tool parameters and automatically supplement session_id, task_id and other parameters from context
        
        Args:
            context: Context object containing session_id, task_id and other information
            server_name: Server name
            tool_name: Tool name
            parameter: Parameter dictionary, will be modified
            
        Returns:
            bool: Whether parameter check passed
        """
        # Ensure tool_list is loaded
        if not self.tool_list or not context:
            return False

        if not self.mcp_servers or not self.mcp_config:
            return False

        try:
            # Build unique identifier for the tool
            tool_identifier = f"{server_name}__{tool_name}"

            # Find corresponding tool in tool_list
            target_tool = None
            for tool in self.tool_list:
                if tool.get("type") == "function" and tool.get("function", {}).get("name") == tool_identifier:
                    target_tool = tool
                    break

            if not target_tool:
                logger.warning(f"Tool not found: {tool_identifier}")
                return False

            # Get tool parameter definitions
            function_info = target_tool.get("function", {})
            tool_parameters = function_info.get("parameters", {})
            properties = tool_parameters.get("properties", {})

            # Check if session_id or task_id parameters are needed
            # Check if session_id is needed
            if "session_id" in properties:
                if hasattr(context, 'session_id') and context.session_id:
                    parameter["session_id"] = context.session_id
                    logger.info(f"Auto-added session_id: {context.session_id}")

            # Check if task_id is needed
            if "task_id" in properties:
                if hasattr(context, 'task_id') and context.task_id:
                    parameter["task_id"] = context.task_id
                    logger.info(f"Auto-added task_id: {context.task_id}")

            return True

        except Exception as e:
            logger.warning(f"Error checking tool parameters: {e}")
            return False

    async def call_tool(
            self,
            action_list: List[Dict[str, Any]] = None,
            task_id: str = None,
            session_id: str = None,
            context: Context = None,
            event_message: Message = None
    ) -> List[ActionResult]:
        results = []
        if not action_list:
            return None

        # Lazy initialization: ensure tool_list is loaded before calling tools
        if not self.tool_list:
            await self.list_tools(context=context)

        try:
            for action in action_list:
                if not isinstance(action, dict):
                    action_dict = vars(action)
                else:
                    action_dict = action

                # Get values from dictionary
                server_name = action_dict.get("tool_name")
                tool_name = action_dict.get("action_name")
                parameter = action_dict.get("params", {})
                result_key = f"{server_name}__{tool_name}"

                operation_info = {
                    "server_name": server_name,
                    "tool_name": tool_name,
                    "params": parameter
                }

                if not server_name or not tool_name:
                    continue

                # Inject env_content parameter if needed (before other processing)
                self._inject_env_content_parameter(result_key, parameter, context, event_message)

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
                        logger.warning(f"Error calling function_tool tool: {e}")
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
                        logger.warning(f"Error calling API tool: {e}")
                        self._update_metadata(result_key, {"error": str(e)}, operation_info)
                    continue

                # Define progress callback for this tool call
                async def progress_callback(
                        progress: float, total: float | None, message: str | None
                ):
                    # for debug vnc
                    message_str = message.replace('\n', '\\n') if message else message
                    logger.info(f"McpServers|progress_callback|{progress}|{total}|{message_str}")
                    try:
                        output = Output()
                        output.data = message
                        tool_output_message = Message(
                            category=Constants.OUTPUT,
                            payload=output,
                            sender=f"{server_name}__{tool_name}",
                            session_id=context.session_id if context else "",
                            headers={"context": context}
                        )
                        sync_exec(send_message, tool_output_message)
                    except BaseException as e:
                        logger.warning(f"Error calling progress callback: {e}")

                # Check and supplement tool parameters
                await self.check_tool_params(
                    context=context,
                    server_name=server_name,
                    tool_name=tool_name,
                    parameter=parameter
                )

                call_result_raw = None
                action_result = ActionResult(
                    tool_name=server_name,
                    action_name=tool_name,
                    content="",
                    keep=True
                )
                call_mcp_e = None

                sandbox_id = self.sandbox.sandbox_id if self.sandbox is not None else None

                if self._should_reuse():
                    # Reuse mode: use cached server instances (delegated to utils.py)
                    call_result_raw = await call_mcp_tool_with_reuse(
                        server_name=server_name,
                        tool_name=tool_name,
                        parameter=parameter,
                        server_instances=self.server_instances,
                        mcp_config=self.mcp_config,
                        context=context,
                        sandbox_id=sandbox_id,
                        progress_callback=progress_callback,
                        max_retry=3,
                        timeout=120.0
                    )

                    if not call_result_raw:
                        call_mcp_e = Exception("Failed to call tool after all retry attempts")
                else:
                    # Non-reuse mode: use AsyncExitStack (delegated to utils.py)
                    call_result_raw = await call_mcp_tool_with_exit_stack(
                        server_name=server_name,
                        tool_name=tool_name,
                        parameter=parameter,
                        mcp_config=self.mcp_config,
                        context=context,
                        sandbox_id=sandbox_id,
                        progress_callback=progress_callback,
                        max_retry=3,
                        timeout=120.0
                    )

                    if not call_result_raw:
                        call_mcp_e = Exception("Failed to call tool after all retry attempts")

                logger.info(f"tool_name:{server_name},action_name:{tool_name} finished.")
                logger.debug(f"tool_name:{server_name},action_name:{tool_name} call-mcp-tool-result: {call_result_raw}")

                if not call_result_raw:
                    logger.warning(f"Error calling tool: {server_name}__{tool_name}")
                    action_result = ActionResult(
                        tool_name=server_name,
                        action_name=tool_name,
                        content=f"Error calling tool {tool_name}",
                        keep=True,
                        metadata={},
                        parameter=parameter
                    )
                    results.append(action_result)
                    self._update_metadata(result_key, {"error": call_mcp_e}, operation_info)
                else:
                    if call_result_raw and call_result_raw.content:
                        metadata = call_result_raw.content[0].model_extra.get("metadata", {})
                        artifact_datas = []

                        content_list: list[str] = []
                        for content in call_result_raw.content:
                            logger.debug(
                                f"tool_name:{server_name},action_name:{tool_name} call-mcp-tool-result: {content}")
                            if isinstance(content, TextContent):
                                content_list.append(content.text)
                                _metadata = content.model_extra.get("metadata", {})
                                if "artifact_data" in _metadata and isinstance(_metadata["artifact_data"], dict):
                                    artifact_datas.append({
                                        "artifact_type": _metadata["artifact_type"],
                                        "artifact_data": _metadata["artifact_data"]
                                    })
                            elif isinstance(content, ImageContent):
                                content_list.append(f"data:image/jpeg;base64,{content.data}")
                                _metadata = content.model_extra.get("metadata", {})
                                if "artifact_data" in _metadata and isinstance(_metadata["artifact_data"], dict):
                                    artifact_datas.append({
                                        "artifact_type": _metadata["artifact_type"],
                                        "artifact_data": _metadata["artifact_data"]
                                    })
                    if metadata and artifact_datas:
                        metadata["artifacts"] = artifact_datas

                    action_result = ActionResult(
                        tool_name=server_name,
                        action_name=tool_name,
                        content=json.dumps(content_list, ensure_ascii=False),
                        keep=True,
                        metadata=metadata,
                        parameter=parameter
                    )
                    results.append(action_result)
                    self._update_metadata(result_key, action_result, operation_info)

        except Exception as e:
            logger.warning(
                f"Failed to call_tool: {e}.Extra info: session_id = {session_id}, action_list = {action_list}, traceback = {traceback.format_exc()}")
            return None

        return results

    def _process_and_save_env_content_mapping(self):
        """
        Process env_content parameters in tool schemas.
        Removes env_content parameters from tool schemas and saves mapping relationships.
        This ensures LLM doesn't see these parameters, but they will be injected during tool calls.

        This method should be called immediately after mcp_tool_desc_transform_v2 generates tool_list
        to ensure the mapping is saved before the schema is returned.
        """
        if not self.sandbox or not self.tool_list:
            return

        env_content_name = self.sandbox.env_content_name
        if not env_content_name:
            return

        # Clear previous mapping
        self._env_content_param_mapping = {}

        for tool in self.tool_list:
            if tool.get("type") != "function":
                continue

            function = tool.get("function", {})
            tool_key = function.get("name", "")  # Format: "server_name__tool_name"
            if not tool_key:
                continue

            parameters = function.get("parameters", {})
            if not isinstance(parameters, dict):
                continue

            properties = parameters.get("properties", {})
            required = parameters.get("required", [])

            # Check if env_content_name parameter exists in this tool
            if env_content_name in properties:
                # Save mapping relationship (must save before removing)
                self._env_content_param_mapping[tool_key] = env_content_name

                # Remove from schema (so LLM doesn't see it)
                del properties[env_content_name]

                # Remove from required list if present
                if isinstance(required, list) and env_content_name in required:
                    required.remove(env_content_name)

                logger.debug(
                    f"Removed env_content parameter '{env_content_name}' from tool '{tool_key}' schema and saved mapping")

    def _inject_env_content_parameter(self, tool_key: str, parameter: Dict[str, Any], context: Context = None,
                                      event_message: Message = None):
        """
        Inject env_content parameter into tool call parameters.

        This method:
        1. Checks if the tool needs env_content injection (based on mapping)
        2. Builds env_content value from sandbox.env_content (user-defined)
        3. Dynamically adds task_id and session_id from context
        4. Merges into parameter (user-provided values take priority)

        Args:
            tool_key: Tool identifier in format "server_name__tool_name"
            parameter: Tool call parameters dictionary (will be modified)
            context: Context object containing task_id and session_id
            event_message: Optional message object that may contain additional context
        """
        # Check if this tool needs env_content injection
        if tool_key not in self._env_content_param_mapping:
            return

        if not self.sandbox:
            return

        env_content_name = self._env_content_param_mapping[tool_key]

        # Build env_content value
        env_content_value = {}

        # 1. Copy user-defined context from sandbox.env_content
        if hasattr(self.sandbox, 'env_content'):
            env_content_value.update(self.sandbox.env_content)

        # 2. Dynamically add task_id and session_id from context
        if context:
            if hasattr(context, 'task_id') and context.task_id:
                env_content_value["task_id"] = context.task_id
            if hasattr(context, 'session_id') and context.session_id:
                env_content_value["session_id"] = context.session_id

        # 3. Dynamically add additional context from event_message
        if event_message:
            if hasattr(event_message, 'sender') and event_message.sender:
                env_content_value["agent_id"] = event_message.sender

        # 4. Merge into parameter
        # If user already provided the parameter, merge (user values take priority)
        if env_content_name not in parameter:
            parameter[env_content_name] = env_content_value
        else:
            # User provided value exists, merge it (user values override)
            user_value = parameter[env_content_name]
            if isinstance(user_value, dict):
                # Merge: user values override env_content values
                parameter[env_content_name] = {**env_content_value, **user_value}
            # If user_value is not a dict, keep it as is (user's choice)

        logger.debug(f"Injected env_content parameter '{env_content_name}' for tool '{tool_key}'")

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
            metadata = self.sandbox._metadata.get("mcp_metadata", {})
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
            logger.debug(f"Failed to update sandbox metadata: {e}")

    # def _init_tool_result_subscription(self, env_session_id: Optional[str] = None, context: Context = None, result_key: Optional[str] = None):
    #     """Initialize subscription for tool results.
    #
    #     Args:
    #         env_session_id: Environment session ID for WebSocket connection
    #         context: Context object containing task_id and session_id
    #         result_key: Tool identifier in format "server_name__tool_name"
    #     """
    #     # Only initialize once
    #     if hasattr(self, '_tool_result_handler'):
    #         return
    #     if not env_session_id:
    #         logger.warning("env_session_id is not provided")
    #         return
    #
    #     try:
    #         token = os.getenv("ENV_CHANNEL_TOKEN", "")
    #         _ws_headers = {"Authorization": f"Bearer {token}"}
    #
    #         server_url = f"ws://mcp.aworldagents.com/vpc-pre/stream/{env_session_id}/channel"
    #
    #         @env_channel_sub(
    #             server_url=server_url,
    #             topics=["env-tool-message-topic"],
    #             auto_connect=True,
    #             auto_reconnect=True,
    #             reconnect_interval=10.0,
    #             headers=_ws_headers,
    #             auto_start=True
    #         )
    #         async def handle_tool_result(msg: EnvChannelMessage):
    #             parent_task_id = None
    #             if context and hasattr(context, 'task_id') and context.task_id:
    #                 parent_task_id = context.task_id
    #
    #             bg_msg = BackgroundTaskMessage(
    #                     background_task_id=f"bg_{uuid.uuid4().hex}",
    #                     parent_task_id=parent_task_id,
    #                     payload=msg.message,
    #                     sender=result_key,
    #                     topic=TopicType.BACKGROUND_TOOL_COMPLETE,
    #                     headers={"context": context}
    #                 )
    #             await send_message(bg_msg)
    #             logger.debug(f"tool:sender: {result_key},result-logging: {msg.message}")
    #
    #         # Store the handler to keep reference
    #         self._tool_result_handler = handle_tool_result
    #         logger.info(
    #             f"Initialized tool result subscription for env_session_id: {env_session_id}, server_url: {server_url}")
    #     except Exception as e:
    #         logger.warning(f"Failed to initialize tool result subscription: {e}")

    # Add cleanup method, called when Sandbox is destroyed
    async def cleanup(self):
        """Clean up all server connections (only needed when reuse=True)"""
        if not self._should_reuse():
            return

        for server_name, server in list(self.server_instances.items()):
            try:
                await cleanup_server(server)
                del self.server_instances[server_name]
                if server_name in self.server_instances_session:
                    del self.server_instances_session[server_name]
                logger.info(f"Cleaned up server instance for {server_name}")
            except Exception as e:
                logger.warning(f"Failed to cleanup server {server_name}: {e}")
