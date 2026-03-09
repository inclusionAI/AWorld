"""
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
"""

import asyncio
import json
import os
import platform
import re
import subprocess
import time
import traceback
from datetime import datetime

# Python 3.11+ ExceptionGroup/BaseExceptionGroup from anyio TaskGroup
import builtins
_ExceptionGroup = getattr(builtins, "ExceptionGroup", type("_Never", (), {}))
_BaseExceptionGroup = getattr(builtins, "BaseExceptionGroup", type("_Never", (), {}))

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo

from aworld.logs.util import Color, logger
from examples.gaia.mcp_collections.base import ActionArguments, ActionCollection, ActionResponse

# pylint: disable=C0301


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


class TerminalActionCollection(ActionCollection):
    """MCP service for terminal command execution with safety controls.

    Provides secure terminal command execution capabilities including:
    - Cross-platform command execution
    - Configurable timeout controls
    - Command history tracking
    - Safety checks for dangerous operations
    - LLM-friendly result formatting
    - Error handling and logging
    """

    def __init__(self, arguments: ActionArguments) -> None:
        super().__init__(arguments)

        # Initialize command history
        self.command_history: list[dict] = []
        self.max_history_size = 50

        # Define dangerous commands for safety
        self.dangerous_commands = [
            "rm -rf /",
            "mkfs",
            "dd if=",
            ":(){ :|:& };:",  # Unix
            "del /f /s /q",
            # "format",
            "diskpart",  # Windows
            "sudo rm",
            "sudo dd",
            "sudo mkfs",  # Sudo variants
        ]

        # Interactive-only commands (block stdin, cannot run non-interactively)
        self.interactive_command_patterns = [
            r"(?:^|\s)(vim|vi|nano|emacs)(?:\s|$)",
            r"(?:^|\s)(less|more)(?:\s|$)",
            r"(?:^|\s)(top|htop)(?:\s|$)",
            r"(?:^|\s)(ftp|telnet)(?:\s|$)",
            r"(?:^|\s)(python3?|bash)\s+-i\b",
        ]

        # Get current platform info
        self.platform_info = {
            "system": platform.system(),
            "platform": platform.platform(),
            "architecture": platform.architecture()[0],
        }

        self._color_log("Terminal service initialized", Color.green, "debug")
        self._color_log(f"Platform: {self.platform_info['system']}", Color.blue, "debug")

    def _check_command_safety(self, command: str) -> tuple[bool, str | None]:
        """Check if command is safe to execute.

        Args:
            command: Command string to check

        Returns:
            Tuple of (is_safe, reason_if_unsafe)
        """
        command_lower = command.lower().strip()

        for dangerous_cmd in self.dangerous_commands:
            if dangerous_cmd.lower() in command_lower:
                return False, f"Command contains dangerous pattern: {dangerous_cmd}"

        return True, None

    def _check_interactive_command(self, command: str) -> tuple[bool, str | None]:
        """Check if command is interactive-only (forbidden; use non-interactive flags instead).

        Args:
            command: Command string to check

        Returns:
            Tuple of (is_allowed, reason_if_forbidden)
        """
        for pattern in self.interactive_command_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return False, (
                    "Interactive commands are not allowed. Use non-interactive alternatives "
                    "(e.g. --yes, -y, CI=1, DEBIAN_FRONTEND=noninteractive) or different tools."
                )
        return True, None

    def _format_command_output(self, result: CommandResult, output_format: str = "markdown") -> str:
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
                output_parts.extend(["\n## Errors/Warnings", "```", result.stderr.strip(), "```"])

            return "\n".join(output_parts)

    async def _execute_command_async(self, command: str, timeout: int) -> CommandResult:
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
            # stdin=DEVNULL: prevent interactive prompts from blocking; subprocess gets EOF on read
            if self.platform_info["system"] == "Windows":
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    shell=True,
                )
            else:
                # start_new_session=True: subprocess gets its own process group so we can
                # safely kill it (and children like curl) on timeout without affecting parent
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    shell=True,
                    executable="/bin/bash",
                    start_new_session=True,
                )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
                stdout = stdout.decode("utf-8", errors="replace")
                stderr = stderr.decode("utf-8", errors="replace")
                return_code = process.returncode

            except asyncio.TimeoutError:
                try:
                    # Kill process group so child processes (e.g. curl) are also terminated
                    if self.platform_info["system"] != "Windows" and process.pid:
                        os.killpg(os.getpgid(process.pid), 9)
                    else:
                        process.kill()
                except (ProcessLookupError, OSError, Exception):
                    try:
                        process.kill()
                    except Exception:
                        self.logger.error(f"Command execution timeout: {traceback.format_exc()}")

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
            self.command_history.append(
                {
                    "timestamp": start_time.isoformat(),
                    "command": command,
                    "success": return_code == 0,
                    "duration": duration,
                }
            )

            # Maintain history size limit
            if len(self.command_history) > self.max_history_size:
                self.command_history.pop(0)

            return result

        except asyncio.CancelledError:
            # Convert cancellation to result instead of propagating (avoids TaskGroup crash)
            duration = str(datetime.now() - start_time)
            return CommandResult(
                command=command,
                success=False,
                stdout="",
                stderr="Command execution was cancelled",
                return_code=-1,
                duration=duration,
                timestamp=start_time.isoformat(),
            )
        except Exception as e:
            # Python 3.11+ ExceptionGroup from anyio TaskGroup; extract first sub-exception
            if isinstance(e, _ExceptionGroup) and getattr(e, "exceptions", None):
                err_msg = str(e.exceptions[0])
            else:
                err_msg = str(e)
            duration = str(datetime.now() - start_time)
            return CommandResult(
                command=command,
                success=False,
                stdout="",
                stderr=f"Error executing command: {err_msg}",
                return_code=-1,
                duration=duration,
                timestamp=start_time.isoformat(),
            )

    async def mcp_execute_command(
        self,
        command: str = Field(
            description="Terminal command to execute. Interactive commands (vim, less, etc.) are forbidden; use non-interactive flags (--yes, -y, CI=1) when available."
        ),
        timeout: int = Field(default=30, description="Command timeout in seconds (default: 30)"),
        output_format: str = Field(default="markdown", description="Output format: 'markdown', 'json', or 'text'"),
    ) -> ActionResponse:
        """Execute a terminal command with safety checks and timeout controls.

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
        - For curl/wget downloads: add `--no-progress-meter` (curl) or `-q` (wget) to avoid
          stderr progress output that can cause pipe pressure; add `--max-time N` (curl) for
          transfer timeout to avoid indefinite hangs on slow networks.

        Args:
            command: The terminal command to execute
            timeout: Maximum execution time in seconds
            output_format: Format for the response output

        Returns:
            ActionResponse with command execution results and metadata
        """
        # Handle FieldInfo objects
        if isinstance(command, FieldInfo):
            command = command.default
        if isinstance(timeout, FieldInfo):
            timeout = timeout.default
        if isinstance(output_format, FieldInfo):
            output_format = output_format.default

        try:
            # Safety check
            is_safe, safety_reason = self._check_command_safety(command)
            if not is_safe:
                return ActionResponse(
                    success=False,
                    message=f"Command rejected for security reasons: {safety_reason}",
                    metadata=TerminalMetadata(
                        command=command,
                        platform=self.platform_info["system"],
                        working_directory=str(self.workspace),
                        timeout_seconds=timeout,
                        safety_check_passed=False,
                        error_type="security_violation",
                    ).model_dump(),
                )

            # Interactive command check (stdin-blocking tools are forbidden)
            is_allowed, interactive_reason = self._check_interactive_command(command)
            if not is_allowed:
                return ActionResponse(
                    success=False,
                    message=f"Command rejected: {interactive_reason}",
                    metadata=TerminalMetadata(
                        command=command,
                        platform=self.platform_info["system"],
                        working_directory=str(self.workspace),
                        timeout_seconds=timeout,
                        safety_check_passed=True,
                        error_type="interactive_forbidden",
                    ).model_dump(),
                )

            self._color_log(f"🔧 Executing command: {command}", Color.cyan)

            # Execute command
            start_time = time.time()
            result = await self._execute_command_async(command, timeout)
            execution_time = time.time() - start_time

            # Format output
            formatted_output = self._format_command_output(result, output_format)

            # Create metadata
            metadata = TerminalMetadata(
                command=command,
                platform=self.platform_info["system"],
                working_directory=str(self.workspace),
                timeout_seconds=timeout,
                execution_time=execution_time,
                return_code=result.return_code,
                safety_check_passed=True,
            )

            if result.success:
                self._color_log("✅ Command completed successfully", Color.green)
            else:
                self._color_log(f"❌ Command failed with return code {result.return_code}", Color.red)
                metadata.error_type = "execution_failure"

            return ActionResponse(success=result.success, message=formatted_output, metadata=metadata.model_dump())

        except Exception as e:
            error_msg = f"Failed to execute command: {str(e)}"
            self.logger.error(f"Command execution error: {traceback.format_exc()}")

            return ActionResponse(
                success=False,
                message=error_msg,
                metadata=TerminalMetadata(
                    command=command,
                    platform=self.platform_info["system"],
                    working_directory=str(self.workspace),
                    timeout_seconds=timeout,
                    safety_check_passed=True,
                    error_type="internal_error",
                ).model_dump(),
            )

    def mcp_get_command_history(
        self,
        count: int = Field(default=10, description="Number of recent commands to return (default: 10)"),
        output_format: str = Field(default="markdown", description="Output format: 'markdown', 'json', or 'text'"),
    ) -> ActionResponse:
        """Retrieve recent command execution history.

        Args:
            count: Number of recent commands to return
            output_format: Format for the response output

        Returns:
            ActionResponse with command history and metadata
        """
        # Handle FieldInfo objects
        if isinstance(count, FieldInfo):
            count = count.default
        if isinstance(output_format, FieldInfo):
            output_format = output_format.default

        try:
            # Get recent history
            recent_history = self.command_history[-count:] if self.command_history else []

            if not recent_history:
                message = "No command history available."
            else:
                if output_format == "json":
                    message = json.dumps(recent_history, indent=2)
                elif output_format == "text":
                    history_lines = []
                    for i, entry in enumerate(recent_history, 1):
                        status = "SUCCESS" if entry["success"] else "FAILED"
                        history_lines.append(
                            f"{i}. [{entry['timestamp']}] {entry['command']} - {status}"
                            f" ({entry.get('duration', 'N/A')})"
                        )
                    message = "\n".join(history_lines)
                else:  # markdown
                    history_lines = ["# Command History", f"Showing {len(recent_history)} recent commands:\n"]

                    for i, entry in enumerate(recent_history, 1):
                        status_emoji = "✅" if entry["success"] else "❌"
                        history_lines.extend(
                            [
                                f"## {i}. {status_emoji} `{entry['command']}`",
                                f"- **Timestamp:** {entry['timestamp']}",
                                f"- **Duration:** {entry.get('duration', 'N/A')}",
                                "",
                            ]
                        )

                    message = "\n".join(history_lines)

            metadata = TerminalMetadata(
                command="get_command_history",
                platform=self.platform_info["system"],
                working_directory=str(self.workspace),
                timeout_seconds=0,
                history_count=len(recent_history),
            )

            return ActionResponse(success=True, message=message, metadata=metadata.model_dump())

        except Exception as e:
            error_msg = f"Failed to retrieve command history: {str(e)}"
            self.logger.error(f"History retrieval error: {traceback.format_exc()}")

            return ActionResponse(
                success=False,
                message=error_msg,
                metadata=TerminalMetadata(
                    command="get_command_history",
                    platform=self.platform_info["system"],
                    working_directory=str(self.workspace),
                    timeout_seconds=0,
                    error_type="internal_error",
                ).model_dump(),
            )

    def mcp_get_terminal_capabilities(self) -> ActionResponse:
        """Get information about terminal service capabilities and configuration.

        Returns:
            ActionResponse with terminal service capabilities and current configuration
        """
        capabilities = {
            "platform_info": self.platform_info,
            "supported_features": [
                "Cross-platform command execution",
                "Configurable timeout controls",
                "Command history tracking",
                "Safety checks for dangerous commands",
                "Multiple output formats (markdown, json, text)",
                "LLM-optimized result formatting",
                "Async command execution",
            ],
            "supported_formats": ["markdown", "json", "text"],
            "configuration": {
                "max_history_size": self.max_history_size,
                "current_history_count": len(self.command_history),
                "working_directory": str(self.workspace),
                "dangerous_commands_count": len(self.dangerous_commands),
            },
            "safety_features": [
                "Dangerous command detection",
                "Timeout controls",
                "Error handling and logging",
                "Command validation",
            ],
        }

        formatted_info = f"""# Terminal Service Capabilities
        
        ## Platform Information
        - **System:** {self.platform_info["system"]}
        - **Platform:** {self.platform_info["platform"]}
        - **Architecture:** {self.platform_info["architecture"]}

        ## Features
        {chr(10).join(f"- {feature}" for feature in capabilities["supported_features"])}

        ## Supported Output Formats
        {chr(10).join(f"- {fmt}" for fmt in capabilities["supported_formats"])}

        ## Current Configuration
        - **Max History Size:** {capabilities["configuration"]["max_history_size"]}
        - **Current History Count:** {capabilities["configuration"]["current_history_count"]}
        - **Working Directory:** {capabilities["configuration"]["working_directory"]}
        - **Dangerous Commands Monitored:** {capabilities["configuration"]["dangerous_commands_count"]}

        ## Safety Features
        {chr(10).join(f"- {feature}" for feature in capabilities["safety_features"])}
        """

        return ActionResponse(success=True, message=formatted_info, metadata=capabilities)


# Default arguments for testing
if __name__ == "__main__":
    load_dotenv()
    import logging
    # Reduce MCP SDK log level to suppress "Processing request of type" INFO messages
    logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)
    # Or silence the entire mcp package
    logging.getLogger("mcp").setLevel(logging.WARNING)

    arguments = ActionArguments(
        name="terminal",
        transport="stdio",
        workspace=os.getenv("AWORLD_WORKSPACE", "./"),
    )
    try:
        service = TerminalActionCollection(arguments)
        service.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Exit silently on Ctrl+C; do not log traceback to avoid noisy output
        pass
    except _BaseExceptionGroup as e:
        # Python 3.11+ anyio TaskGroup "unhandled errors" (e.g. subprocess cancellation)
        logger.error(f"TaskGroup error: {e}\n{traceback.format_exc()}")
    except Exception as e:
        logger.error(f"An error occurred: {e}: {traceback.format_exc()}")
