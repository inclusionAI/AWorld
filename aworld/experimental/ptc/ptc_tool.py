# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""PTC (Programmatic Tool Calling) Tool for executing Python code with MCP tool access.

This tool allows agents to execute Python scripts that can orchestrate multiple MCP tool calls
programmatically, keeping intermediate results out of the context window.
"""
import traceback
import asyncio
from typing import Any, Dict, Tuple, List
import sys
from io import StringIO

from aworld.agents.llm_agent import Agent
from aworld.config import ToolConfig
from aworld.core.agent.base import AgentFactory, BaseAgent
from aworld.core.common import Observation, ActionModel, ActionResult, ToolActionInfo, ParamInfo
from aworld.core.context.amni import AmniContext
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import ToolFactory, AsyncTool
from aworld.logs.util import logger
from aworld.tools.utils import build_observation
from aworld.experimental.ptc.mcp_client_code_generator import generate_ptc_tool_module_from_openai_tools

PTC_TOOL = "PTC"


class PtcExecuteAction(ToolAction):
    """PTC Tool Action Definition."""

    EXECUTE_CODE = ToolActionInfo(
        name="execute_ptc_code",
        input_params={
            "code": ParamInfo(
                name="code",
                type="str",
                required=True,
                desc="Python code to execute. The code can call MCP tools marked with [allow_code_execution]."
            )
        },
        desc="Execute Python code in a sandboxed environment with access to MCP tools. "
             "Use this when you need to orchestrate multiple tool calls programmatically."
    )


@ToolFactory.register(
    name=PTC_TOOL,
    desc=PTC_TOOL,
    supported_action=PtcExecuteAction
)
class PtcTool(AsyncTool):
    """Tool for executing PTC (Programmatic Tool Calling) Python scripts.
    
    This tool executes Python code in a sandboxed environment and provides access to MCP tools
    that are marked with [allow_code_execution]. The code can orchestrate multiple tool calls
    programmatically, keeping intermediate results out of the context window.
    
    Example:
        # Agent can call this tool with Python code
        action = ActionModel(
            action_name="execute_ptc_code",
            params={"code": "result = await read_file(path='large_log.txt'); ..."}
        )
    """

    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        """Initialize PTC Tool.
        
        Args:
            conf: Tool configuration
            **kwargs: Additional arguments
        """
        super(PtcTool, self).__init__(conf, **kwargs)
        self.cur_observation = None
        self.content = None
        self.step_finished = True

    async def reset(
        self,
        *,
        seed: int | None = None,
        options: Dict[str, str] | None = None
    ) -> Tuple[Observation, dict[str, Any]]:
        """Reset the tool state.
        
        Args:
            seed: Random seed (not used)
            options: Additional options (not used)
            
        Returns:
            Tuple of (observation, info dict)
        """
        await super().reset(seed=seed, options=options)
        await self.close()
        self.step_finished = True
        return build_observation(
            observer=self.name(),
            ability=PtcExecuteAction.EXECUTE_CODE.value.name
        ), {}

    def init(self) -> None:
        """Initialize the tool."""
        self.initialized = True

    async def close(self) -> None:
        """Close the tool and clean up resources."""
        pass

    async def finished(self) -> bool:
        """Check if the tool step is finished.
        
        Returns:
            True if finished, False otherwise
        """
        return self.step_finished

    async def _get_sandbox_from_message(self, message: Message):
        """Get sandbox instance from message.
        
        Args:
            message: Message containing agent and context
            
        Returns:
            Sandbox instance or None
        """
        if not message:
            return None
        
        # Try to get from context if available
        if hasattr(message, 'context') and message.context:
            # Context might have sandbox reference
            agent = AgentFactory.agent_instance(message.sender)
            if agent and agent.sandbox:
                return agent.sandbox
        return None

    async def _generate_tool_modules(self, sandbox, context: Context, agent: Agent) -> Dict[str, str]:
        """Generate Python tool modules for PTC-compatible tools.
        
        Args:
            sandbox: Sandbox instance with MCP servers
            context: Context for tool discovery
            
        Returns:
            Dictionary mapping server names to generated module code
        """
        if not sandbox or not hasattr(sandbox, 'mcp_servers') or not sandbox.mcp_servers:
            return {}
        
        tool_modules = {}
        
        try:
            # Get tools in OpenAI format
            if agent.tools:
                openai_tools = agent.tools
            else:
                openai_tools = await agent.async_desc_transform(context)
            
            # Group tools by server
            server_tools = {}
            for tool in openai_tools:
                if not isinstance(tool, dict) or "function" not in tool:
                    continue
                
                func_info = tool["function"]
                tool_name = func_info.get("name", "")
                description = func_info.get("description", "")
                
                # Check if tool is marked for PTC
                if not description.startswith("[allow_code_execution]"):
                    continue

                # Extract server name from tool name (format: "server_name__tool_name")
                server_name = agent.tool_mapping[tool_name]

                if server_name and server_name not in server_tools:
                    server_tools[server_name] = []
                server_tools[server_name].append(tool)
            
            # Generate modules for each server
            for server_name, tools in server_tools.items():
                try:
                    module_code = generate_ptc_tool_module_from_openai_tools(
                        openai_tools=tools,
                        server_name=server_name
                    )
                    tool_modules[server_name] = module_code
                    logger.debug(f"Generated PTC tool module for server: {server_name}")
                except Exception as e:
                    logger.warning(f"Failed to generate tool module for {server_name}: {e}")
        
        except Exception as e:
            logger.warning(f"Failed to generate tool modules: {e}")
        
        return tool_modules

    async def _execute_code(
        self,
        code: str,
        sandbox,
        context: AmniContext,
        tool_modules: Dict[str, str]
    ) -> Tuple[Any, str]:
        """Execute Python code in a sandboxed environment.
        
        Args:
            code: Python code to execute
            sandbox: Sandbox instance for MCP tool calls
            context: Context for tool calls
            tool_modules: Dictionary of generated tool modules
            
        Returns:
            Tuple of (result, error_message)
        """
        # Create execution namespace
        exec_globals = {
            '__builtins__': __builtins__,
            'sandbox': sandbox,
            'context': context,
        }
        exec_locals = {}
        
        # Inject tool modules into execution environment
        for server_name, module_code in tool_modules.items():
            try:
                # Execute module code to create tool functions
                module_globals = {
                    '__builtins__': __builtins__,
                    'sandbox': sandbox,
                }
                exec(module_code, module_globals)
                
                # Set sandbox in the module
                if 'set_sandbox' in module_globals:
                    module_globals['set_sandbox'](sandbox)
                
                # Import all tool functions into exec_globals
                for name, value in module_globals.items():
                    if not name.startswith('_') and callable(value):
                        exec_globals[name] = value
                
                logger.debug(f"Injected tool functions from {server_name}")
            except Exception as e:
                logger.warning(f"Failed to inject tool module {server_name}: {e}")
        
        # Capture stdout/stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        stdout_capture = StringIO()
        stderr_capture = StringIO()
        
        try:
            sys.stdout = stdout_capture
            sys.stderr = stderr_capture
            
            # Execute the code
            # Wrap code in an async function to support both sync and async operations
            # Indent user code properly
            import textwrap
            indented_code = textwrap.indent(code, '    ')
            wrapped_code = f"""async def _ptc_main():
{indented_code}
    # Try to return result if it exists, otherwise return None
    if 'result' in locals():
        return result
    return None
"""
            
            # Compile and execute wrapped code
            compiled_code = compile(wrapped_code, '<ptc_code>', 'exec')
            exec(compiled_code, exec_globals, exec_locals)
            
            # Execute the async function
            if '_ptc_main' in exec_locals:
                main_func = exec_locals['_ptc_main']
                if asyncio.iscoroutinefunction(main_func):
                    result = await main_func()
                else:
                    result = main_func()
            else:
                result = None
            
            stdout_output = stdout_capture.getvalue()
            stderr_output = stderr_capture.getvalue()
            
            # Combine outputs
            output = ""
            if stdout_output:
                output += f"STDOUT:\n{stdout_output}\n"
            if stderr_output:
                output += f"STDERR:\n{stderr_output}\n"
            
            return result, output
        
        except Exception as e:
            error_msg = f"Execution error: {str(e)}\n{traceback.format_exc()}"
            logger.error(f"PTC code execution failed: {error_msg}")
            return None, error_msg
        
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    async def do_step(
        self,
        actions: list[ActionModel],
        message: Message = None,
        **kwargs
    ) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        """Execute PTC code step.
        
        Args:
            actions: List of actions to execute
            message: Message containing agent and context
            **kwargs: Additional arguments
            
        Returns:
            Tuple of (observation, reward, terminated, truncated, info)
        """
        self.step_finished = False
        reward = 0.0
        fail_error = ""
        observation = build_observation(
            observer=self.name(),
            ability=PtcExecuteAction.EXECUTE_CODE.value.name
        )
        info = {}
        
        try:
            if not actions:
                raise ValueError("actions is empty")
            
            if not isinstance(message.context, AmniContext):
                raise ValueError("context is not AmniContext")
            
            # Get sandbox
            sandbox = await self._get_sandbox_from_message(message)
            if not sandbox:
                raise ValueError("sandbox not available in message")
            
            # Generate tool modules for PTC-compatible tools
            tool_modules = await self._generate_tool_modules(sandbox, message.context, AgentFactory.agent_instance(message.sender))
            
            for action in actions:
                logger.info(f"PTC Tool|do_step: {action}")
                action_name = action.action_name
                
                if action_name == PtcExecuteAction.EXECUTE_CODE.value.name:
                    code = action.params.get("code", "")
                    if not code:
                        raise ValueError("code parameter is required")
                    
                    # Execute the code
                    result, error_output = await self._execute_code(
                        code=code,
                        sandbox=sandbox,
                        context=message.context,
                        tool_modules=tool_modules
                    )
                    
                    if error_output and "error" in error_output.lower():
                        raise RuntimeError(f"Code execution error: {error_output}")
                    
                    # Format result
                    if result is not None:
                        if isinstance(result, (dict, list)):
                            import json
                            result_str = json.dumps(result, indent=2, ensure_ascii=False)
                        else:
                            result_str = str(result)
                    else:
                        result_str = error_output or "Code executed successfully (no return value)"
                    
                    observation.content = result_str
                    observation.action_result.append(
                        ActionResult(
                            is_done=True,
                            success=True,
                            content=result_str,
                            keep=False
                        )
                    )
                else:
                    raise ValueError(f"Unknown action: {action_name}")
            
            reward = 1.0
        
        except Exception as e:
            fail_error = str(e)
            logger.warn(f"PTC Tool|failed do_step: {traceback.format_exc()}")
            observation.action_result.append(
                ActionResult(
                    is_done=True,
                    success=False,
                    content=f"Error: {fail_error}",
                    error=fail_error,
                    keep=False
                )
            )
        finally:
            self.step_finished = True
        
        info["exception"] = fail_error
        info.update(kwargs)
        return (
            observation,
            reward,
            kwargs.get("terminated", False),
            kwargs.get("truncated", False),
            info
        )

