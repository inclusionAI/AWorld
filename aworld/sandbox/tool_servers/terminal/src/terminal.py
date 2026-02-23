import asyncio
import json
import logging
import platform
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Union
import uuid
import os

from dotenv import load_dotenv
from pydantic.fields import FieldInfo
from mcp.server.fastmcp import Context
from mcp.server import FastMCP
from mcp.types import TextContent
from pydantic import Field, BaseModel


load_dotenv()
# 工作目录：从环境变量 AWORLD_WORKSPACE 读取（与 filesystem 一致，可为逗号分隔多路径；terminal 只取第一个）
_env_workspace = os.environ.get("AWORLD_WORKSPACE", "").strip()
if _env_workspace:
    _first = next((p.strip() for p in _env_workspace.split(",") if p.strip()), None)
    workspace = Path(_first) if _first else Path.home() / "workspace"
else:
    workspace = Path.home() / "workspace"

# Allow customizing the leading icon in the terminal card output
TERMINAL_ICON = os.getenv("TERMINAL_ICON", "🖥️")

command_history: list[dict] = []
max_history_size = 50

# Define dangerous commands for safety
dangerous_commands = [
    "rm -rf /",
    "mkfs",
    "dd if=",
    ":(){ :|:& };:",  # Unix
    "del /f /s /q",
    # "format",
    # "format",
    "diskpart",  # Windows
    "sudo rm",
    "sudo dd",
    "sudo mkfs",  # Sudo variants
]

# Get current platform info
platform_info = {
    "system": platform.system(),
    "platform": platform.platform(),
    "architecture": platform.architecture()[0],
}

class ActionResponse(BaseModel):
    r"""Protocol: MCP Action Response"""

    success: bool = Field(default=False, description="Whether the action is successfully executed")
    message: Any = Field(default=None, description="The execution result of the action")
    metadata: dict[str, Any] = Field(default={}, description="The metadata of the action")


class CommandResult(BaseModel):
    """Individual command execution result with structured data."""

    command: str
    success: bool
    stdout: str
    stderr: str
    return_code: int
    duration: str
    timestamp: str


class TerminalMetadata(BaseModel):
    """Metadata for terminal operation results."""

    command: str
    platform: str
    working_directory: str
    timeout_seconds: int
    execution_time: float | None = None
    return_code: int | None = None
    safety_check_passed: bool = True
    error_type: str | None = None
    history_count: int | None = None
    output_data: str | None = None


mcp = FastMCP(
    "terminal-server",
    log_level="DEBUG",
    port=8081,
    instructions="""
Terminal MCP Server

This module provides MCP server functionality for executing terminal commands safely.
It supports command execution with timeout controls and returns LLM-friendly formatted results.

Key features:
- Execute terminal commands with configurable timeouts
- Cross-platform command execution support
- Command history tracking and retrieval
- Safety checks for dangerous commands
- LLM-optimized output formatting

Main functions:
- mcp_execute_command: Execute terminal commands with safety checks
- mcp_get_command_history: Retrieve recent command execution history
- mcp_get_terminal_capabilities: Get terminal service capabilities
""",
)


async def send_command_card(
    ctx: Context, command_id: str, command: str, output: str, workspace: Path
):
    try:
        command_tool_card = {
            "type": "tool_call_card_command_execute",
            "custom_output":f"{TERMINAL_ICON} Terminal $ {command}",
            "card_data": {
                "title": "Termainl Command Execute",
                "command_id": command_id,
                "command": command,
                "result": {"message": output},
                "metadata": {
                    "working_directory": str(workspace),
                },
            },
        }
        message = f"""\
\n\n
```tool_card
{json.dumps(command_tool_card, indent=2, ensure_ascii=False)}
```
\n\n
"""
        if ctx:
            await ctx.report_progress(progress=0.0, total=1.0, message=message)
    except:
        logging.error(f"Error sending command card: {traceback.format_exc()}")


@mcp.tool(
    description="""
Execute a terminal command with safety checks and timeout controls.

        This tool provides secure command execution with:
        - Cross-platform compatibility (Windows, macOS, Linux)
        - Configurable timeout controls
        - Safety checks for dangerous commands
        - LLM-optimized result formatting
        - Command history tracking

        Specialized Feature:
        - Execute Python code and output the result to stdout
            - Example (Directly execute simple Python code): `python -c "nums = [1, 2, 3, 4]\nsum_of_nums = sum(nums)\nprint(f'{sum_of_nums=}')"`
            - Example (Execute code from a file): `python my_script.py`
"""
)
async def run_code(
    ctx: Context,
    code: str = Field(description="Terminal command to execute"),
    timeout: int = Field(
        default=30, description="Command timeout in seconds (default: 30)"
    ),
    output_format: str = Field(
        default="markdown", description="Output format: 'markdown', 'json', or 'text'"
    ),
) -> Union[str, TextContent]:
    # 处理参数：如果是 FieldInfo 则使用默认值，否则使用传入的值
    if isinstance(code, FieldInfo):
        command = code.default
    else:
        command = code
    
    if isinstance(timeout, FieldInfo):
        timeout = timeout.default
    
    if isinstance(output_format, FieldInfo):
        output_format = output_format.default

    output_data = ""
    command_id = str(uuid.uuid4())
    try:
        # Safety check
        is_safe, safety_reason = _check_command_safety(command)
        if not is_safe:
            action_response = ActionResponse(
                success=False,
                message=f"Command rejected for security reasons: {safety_reason}",
                metadata=TerminalMetadata(
                    command=command,
                    platform=platform_info["system"],
                    working_directory=str(workspace),
                    timeout_seconds=timeout,
                    safety_check_passed=False,
                    error_type="security_violation",
                    output_data=safety_reason,
                ).model_dump(),
            )
            # await send_command_card(
            #     ctx,
            #     command_id,
            #     command=command,
            #     output=safety_reason,
            #     workspace=workspace,
            # )
            return TextContent(
                type="text",
                text=json.dumps(
                    action_response.model_dump()
                ),  # Empty string instead of None
                **{"metadata": {}},  # Pass as additional fields
            )

        logging.info(f"🔧 Executing command: {command}")

        # Execute command
        start_time = time.time()
        result = await _execute_command_async(command, timeout)
        execution_time = time.time() - start_time

        # Format output
        formatted_output = _format_command_output(result, output_format)

        outputs = []
        if result.stderr:
            outputs.append(result.stderr)
        if result.stdout:
            outputs.append(result.stdout)
        output_data = "\n".join(outputs)

        # Create metadata
        metadata = TerminalMetadata(
            command=command,
            platform=platform_info["system"],
            working_directory=str(workspace),
            timeout_seconds=timeout,
            execution_time=execution_time,
            return_code=result.return_code,
            safety_check_passed=True,
            output_data=output_data,
        )

        if result.success:
            logging.info(
                "✅ Command completed successfully",
            )
        else:
            logging.info(f"❌ Command failed with return code {result.return_code}")
            metadata.error_type = "execution_failure"

        action_response = ActionResponse(
            success=result.success,
            message=formatted_output,
            metadata=metadata.model_dump(),
        )
        output_dict = {
            "artifact_type": "MARKDOWN",
            "artifact_data": json.dumps(action_response.model_dump()),
        }
        # await send_command_card(
        #     ctx,
        #     command_id,
        #     command=command,
        #     output=metadata.output_data,
        #     workspace=workspace,
        # )
        return TextContent(
            type="text",
            text=json.dumps(
                action_response.model_dump()
            ),  # Empty string instead of None
            **{"metadata": output_dict},  # Pass as additional fields
        )

    except Exception as e:
        error_msg = f"Failed to execute command: {str(e)}"
        logging.error(f"Command execution error: {traceback.format_exc()}")

        action_response = ActionResponse(
            success=False,
            message=error_msg,
            metadata=TerminalMetadata(
                command=command,
                platform=platform_info["system"],
                working_directory=str(workspace),
                timeout_seconds=timeout,
                safety_check_passed=True,
                error_type="internal_error",
            ).model_dump(),
        )
        return TextContent(
            type="text",
            text=json.dumps(
                action_response.model_dump()
            ),  # Empty string instead of None
            **{"metadata": {}},  # Pass as additional fields
        )



def _check_command_safety(command: str) -> tuple[bool, str | None]:
    """Check if command is safe to execute.

    Args:
        command: Command string to check

    Returns:
        Tuple of (is_safe, reason_if_unsafe)
    """
    command_lower = command.lower().strip()

    for dangerous_cmd in dangerous_commands:
        if dangerous_cmd.lower() in command_lower:
            return False, f"Command contains dangerous pattern: {dangerous_cmd}"

    return True, None


def _format_command_output(
    result: CommandResult, output_format: str = "markdown"
) -> str:
    """Format command execution results for LLM consumption.

    Args:
        result: Command execution result
        output_format: Format type ('markdown', 'json', 'text')

    Returns:
        Formatted string suitable for LLM consumption
    """
    if output_format == "json":
        return json.dumps(result.model_dump(), indent=2)

    elif output_format == "text":
        output_parts = [
            f"Command: {result.command}",
            f"Status: {'SUCCESS' if result.success else 'FAILED'}",
            f"Duration: {result.duration}",
            f"Return Code: {result.return_code}",
        ]

        if result.stdout:
            output_parts.extend(["\nOutput:", result.stdout])

        if result.stderr:
            output_parts.extend(["\nErrors/Warnings:", result.stderr])

        return "\n".join(output_parts)

    else:  # markdown (default)
        status_emoji = "✅" if result.success else "❌"

        output_parts = [
            f"# Terminal Command Execution {status_emoji}",
            f"**Command:** `{result.command}`",
            f"**Status:** {'SUCCESS' if result.success else 'FAILED'}",
            f"**Duration:** {result.duration}",
            f"**Return Code:** {result.return_code}",
            f"**Timestamp:** {result.timestamp}",
        ]

        if result.stdout:
            output_parts.extend(["\n## Output", "```", result.stdout.strip(), "```"])

        if result.stderr:
            output_parts.extend(
                ["\n## Errors/Warnings", "```", result.stderr.strip(), "```"]
            )

        return "\n".join(output_parts)


async def _execute_command_async(command: str, timeout: int) -> CommandResult:
    """Execute command asynchronously with timeout.

    Args:
        command: Command to execute
        timeout: Timeout in seconds

    Returns:
        CommandResult with execution details
    """
    start_time = datetime.now()

    try:
        # Create appropriate subprocess for platform
        if platform_info["system"] == "Windows":
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                shell=True,
            )
        else:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                shell=True,
                executable="/bin/bash",
            )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
            stdout = stdout.decode("utf-8", errors="replace")
            stderr = stderr.decode("utf-8", errors="replace")
            return_code = process.returncode

        except asyncio.TimeoutError:
            try:
                process.kill()
            except Exception:
                pass

            duration = str(datetime.now() - start_time)
            return CommandResult(
                command=command,
                success=False,
                stdout="",
                stderr=f"Command timed out after {timeout} seconds",
                return_code=-1,
                duration=duration,
                timestamp=start_time.isoformat(),
            )

        duration = str(datetime.now() - start_time)
        result = CommandResult(
            command=command,
            success=return_code == 0,
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
            duration=duration,
            timestamp=start_time.isoformat(),
        )

        # Add to history
        command_history.append(
            {
                "timestamp": start_time.isoformat(),
                "command": command,
                "success": return_code == 0,
                "duration": duration,
            }
        )

        # Maintain history size limit
        if len(command_history) > max_history_size:
            command_history.pop(0)

        return result

    except Exception as e:
        duration = str(datetime.now() - start_time)
        return CommandResult(
            command=command,
            success=False,
            stdout="",
            stderr=f"Error executing command: {str(e)}",
            return_code=-1,
            duration=duration,
            timestamp=start_time.isoformat(),
        )


if __name__ == "__main__":
    load_dotenv(override=True)
    logging.info("Starting terminal-server MCP server!")
    mcp.run(transport="streamable-http")
