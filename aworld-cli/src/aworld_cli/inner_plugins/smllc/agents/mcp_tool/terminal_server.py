import asyncio
import json
import platform
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Union

from dotenv import load_dotenv
from mcp.server import FastMCP
from mcp.types import TextContent
from pydantic import Field, BaseModel
from pydantic.fields import FieldInfo

from aworld.logs.util import Color, logger

import os
import tempfile


import traceback
from pathlib import Path
from urllib.parse import urlparse

import requests

from aworld.logs.util import Color
from pydantic import BaseModel, Field
from typing import Any, Literal


class DocumentMetadata(BaseModel):
    """Metadata extracted from document processing."""

    file_name: str = Field(description="Original file name")
    file_size: int = Field(description="File size in bytes")
    file_type: str = Field(description="Document file type/extension")
    absolute_path: str = Field(description="Absolute path to the document file")
    page_count: int | None = Field(default=None, description="Number of pages in document")
    processing_time: float = Field(
        description="Time taken to process the document in seconds", deprecated=True, exclude=True
    )
    extracted_images: list[str] = Field(default_factory=list, description="Paths to extracted image files")
    extracted_media: list[dict[str, str]] = Field(
        default_factory=list, description="list of extracted media files with type and path"
    )
    output_format: str = Field(description="Format of the extracted content")
    llm_enhanced: bool = Field(default=False, description="Whether LLM enhancement was used", exclude=True)
    ocr_applied: bool = Field(default=False, description="Whether OCR was applied", exclude=True)
    extracted_text_file_path: str | None = Field(
        default=None, description="Absolute path to the extracted text file (if applicable)"
    )

class ActionResponse(BaseModel):
    r"""Protocol: MCP Action Response"""

    success: bool = Field(default=False, description="Whether the action is successfully executed")
    message: Any = Field(default=None, description="The execution result of the action")
    metadata: dict[str, Any] = Field(default={}, description="The metadata of the action")


def _validate_file_path(file_path: str) -> Path:
    """Validate and resolve file path. Rely on the predefined supported_extensions class variable.

    Args:
        file_path: Path to the document or media file

    Returns:
        Resolved Path object

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file type is not supported
    """
    path = Path(file_path)
    if not path.is_absolute():
        path = path.expanduser().resolve()

    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    return path

def _color_log(value: str, color: Color = None, level: str = "info"):
    return color_log(value, color, level=level)

def color_log(value: str, color: Color | None, level: str | None = None):
    # Default to 'info' level if none specified
    # logger = logger.Logger
    if level is None:
        level = "info"

    # Format the message with color
    if color is None:
        message = f"{Color.black} {value} {Color.reset}"
    else:
        message = f"{color} {value} {Color.reset}"

    # Log according to the specified level
    level_lower = level.lower()
    if level_lower == "debug":
        logger.debug(message)
    elif level_lower == "info":
        logger.info(message)
    elif level_lower == "warning" or level_lower == "warn":
        logger.warning(message)
    elif level_lower == "error":
        logger.error(message)
    elif level_lower == "critical":
        logger.critical(message)
    else:
        # Default to info for unknown levels
        logger.info(message)

def is_url(path_or_url: str) -> bool:
    """
    Check if the given string is a URL.

    Args:
        path_or_url: String to check

    Returns:
        bool: True if the string is a URL, False otherwise
    """
    parsed = urlparse(path_or_url)
    return bool(parsed.scheme and parsed.netloc)


def get_mime_type(file_path: str, default_mime: str | None = None) -> str:
    """
    Detect MIME type of a file using python-magic if available,
    otherwise fallback to extension-based detection.

    Args:
        file_path: Path to the file
        default_mime: Default MIME type to return if detection fails

    Returns:
        str: Detected MIME type
    """
    # Try using python-magic for accurate MIME type detection
    try:
        import magic
        mime = magic.Magic(mime=True)
        return mime.from_file(file_path)
    except (AttributeError, IOError):
        # Fallback to extension-based detection
        extension_mime_map = {
            # Audio formats
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".ogg": "audio/ogg",
            ".m4a": "audio/mp4",
            ".flac": "audio/flac",
            # Image formats
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".tiff": "image/tiff",
            # Video formats
            ".mp4": "video/mp4",
            ".avi": "video/x-msvideo",
            ".mov": "video/quicktime",
            ".mkv": "video/x-matroska",
            ".webm": "video/webm",
        }

        ext = Path(file_path).suffix.lower()
        return extension_mime_map.get(ext, default_mime or "application/octet-stream")


def get_file_from_source(
        source: str,
        max_size_mb: float = 100.0,
        timeout: int = 60,
) -> tuple[str, str, bytes]:
    """
    Unified function to get file content from a URL or local path with validation.

    Args:
        source: URL or local file path
        max_size_mb: Maximum allowed file size in MB
        timeout: Timeout for URL requests in seconds

    Returns:
        Tuple[str, str, bytes]: (file_path, mime_type, file_content)
        - For URLs, file_path will be a temporary file path
        - For local files, file_path will be the original path

    Raises:
        ValueError: When file doesn't exist, exceeds size limit, or has invalid MIME type
        IOError: When file cannot be read
        requests.RequestException: When URL request fails
    """
    max_size_bytes = max_size_mb * 1024 * 1024

    if is_url(source):
        # Handle URL source
        try:
            # Make a HEAD request first to check content length
            head_response = requests.head(source, timeout=timeout, allow_redirects=True)
            head_response.raise_for_status()

            # Check content length if available
            content_length = head_response.headers.get("content-length")
            if content_length and int(content_length) > max_size_bytes:
                raise ValueError(
                    f"File size ({int(content_length) / (1024 * 1024):.2f} MB) "
                    f"exceeds maximum allowed size ({max_size_mb} MB)"
                )

            # Download the file
            response = requests.get(source, timeout=timeout, stream=True)
            response.raise_for_status()

            # Read content with size checking
            content = b""
            for chunk in response.iter_content(chunk_size=8192):
                if len(content) + len(chunk) > max_size_bytes:
                    raise ValueError(f"File size exceeds maximum allowed size ({max_size_mb} MB)")
                content += chunk

            # Create temporary file
            parsed_url = urlparse(source)
            filename = os.path.basename(parsed_url.path) or "downloaded_file"

            # Create temporary file with proper extension
            suffix = Path(filename).suffix or ".tmp"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                temp_file.write(content)
                temp_path = temp_file.name

            # Get MIME type
            mime_type = get_mime_type(temp_path)

            return temp_path, mime_type, content

        except requests.RequestException as e:
            raise requests.RequestException(f"Failed to download file from URL: {e}: {traceback.format_exc()}")
        except Exception as e:
            raise IOError(f"Error processing URL: {e}: {traceback.format_exc()}") from e

    else:
        # Handle local file path
        file_path = Path(source)

        # Check if file exists
        if not file_path.exists():
            raise ValueError(f"File does not exist: {source}")

        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {source}")

        # Check file size
        file_size = file_path.stat().st_size
        if file_size > max_size_bytes:
            raise ValueError(
                f"File size ({file_size / (1024 * 1024):.2f} MB) exceeds maximum allowed size ({max_size_mb} MB)"
            )

        # Read file content
        try:
            with open(file_path, "rb") as f:
                content = f.read()
        except Exception as e:
            raise IOError(f"Cannot read file {source}: {e}: {traceback.format_exc()}") from e

        # Get MIME type
        mime_type = get_mime_type(str(file_path))

        return str(file_path), mime_type, content

load_dotenv()
#workspace = Path("/tmp/project")
workspace = Path.home()

command_history: list[dict] = []
max_history_size = 50

# Define dangerous commands for safety
dangerous_commands = [
    "rm -rf /",
    "mkfs",
    "dd if=",
    ":(){ :|:& };:",  # Unix
    "del /f /s /q",
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


mcp = FastMCP("terminal-server", instructions="""
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
""")


@mcp.tool(description="""
Execute a terminal command with safety checks and timeout controls.

        This tool provides secure command execution with:
        - Cross-platform compatibility (Windows, macOS, Linux)
        - Configurable timeout controls
        - Safety checks for dangerous commands
        - LLM-optimized result formatting
        - Command history tracking

        Example params:
        {"command": "ls -la", "timeout": 30, "output_format": "markdown"}

        Specialized Feature:
        - Execute Python code and output the result to stdout
            - Example (Directly execute simple Python code): `python -c "nums = [1, 2, 3, 4]\nsum_of_nums = sum(nums)\nprint(f'{sum_of_nums=}')"`
            - Example (Execute code from a file): `python my_script.py`
""")
async def execute_command(
        command: str = Field(description="Terminal command to execute"),
        timeout: int = Field(default=30, description="Command timeout in seconds (default: 30)"),
        output_format: str = Field(default="markdown", description="Output format: 'markdown', 'json', or 'text'"),
) -> Union[str, TextContent]:
    if isinstance(command, FieldInfo):
        command = command.default
    if isinstance(timeout, FieldInfo):
        timeout = timeout.default
    if isinstance(output_format, FieldInfo):
        output_format = output_format.default

    output_data=""

    try:
        # Format validation check
        is_valid_format, format_error = _validate_output_format(output_format)
        if not is_valid_format:
            action_response = ActionResponse(
                success=False,
                message=format_error,
                metadata=TerminalMetadata(
                    command=command,
                    platform=platform_info["system"],
                    working_directory=str(workspace),
                    timeout_seconds=timeout,
                    safety_check_passed=True,
                    error_type="invalid_format",
                    output_data=format_error,
                ).model_dump(),
            )
            return TextContent(
                type="text",
                text=json.dumps(action_response.model_dump()),
                **{"metadata": {}}
            )

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
            return TextContent(
                type="text",
                text=json.dumps(action_response.model_dump()),  # Empty string instead of None
                **{"metadata": {}}  # Pass as additional fields
            )

        _color_log(f"🔧 Executing command: {command}", Color.cyan)

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
            _color_log("✅ Command completed successfully", Color.green)
        else:
            _color_log(f"❌ Command failed with return code {result.return_code}", Color.red)
            metadata.error_type = "execution_failure"

        action_response = ActionResponse(success=result.success, message=formatted_output,
                                         metadata=metadata.model_dump())
        return TextContent(
            type="text",
            text=json.dumps(action_response.model_dump()),  # Empty string instead of None
            **{"metadata": {}}  # Pass as additional fields
        )

    except Exception as e:
        error_msg = f"Failed to execute command: {str(e)}"
        logger.error(f"Command execution error: {traceback.format_exc()}")

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
            text=json.dumps(action_response.model_dump()),  # Empty string instead of None
            **{"metadata": {}}  # Pass as additional fields
        )


@mcp.tool(description="""
Retrieve recent command execution history.

Example params:
{"count": 10, "output_format": "markdown"}
""")
async def get_command_history(
        count: int = Field(default=10, description="Number of recent commands to return (default: 10)"),
        output_format: str = Field(default="markdown", description="Output format: 'markdown', 'json', or 'text'"),
) -> Union[str, TextContent]:
    if isinstance(count, FieldInfo):
        count = count.default
    if isinstance(output_format, FieldInfo):
        output_format = output_format.default

    try:
        # Format validation check
        is_valid_format, format_error = _validate_output_format(output_format)
        if not is_valid_format:
            action_response = ActionResponse(
                success=False,
                message=format_error,
                metadata=TerminalMetadata(
                    command="get_command_history",
                    platform=platform_info["system"],
                    working_directory=str(workspace),
                    timeout_seconds=0,
                    error_type="invalid_format",
                    output_data=format_error,
                ).model_dump(),
            )
            return TextContent(
                type="text",
                text=json.dumps(action_response.model_dump()),
                **{"metadata": {}}
            )

        # Get recent history
        recent_history = command_history[-count:] if command_history else []

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
            platform=platform_info["system"],
            working_directory=str(workspace),
            timeout_seconds=0,
            history_count=len(recent_history),
        )

        action_response = ActionResponse(success=True, message=message, metadata=metadata.model_dump())
        return TextContent(
            type="text",
            text=json.dumps(action_response.model_dump()),  # Empty string instead of None
            **{"metadata": {}}  # Pass as additional fields
        )

    except Exception as e:
        error_msg = f"Failed to retrieve command history: {str(e)}"
        logger.error(f"History retrieval error: {traceback.format_exc()}")

        action_response = ActionResponse(
            success=False,
            message=error_msg,
            metadata=TerminalMetadata(
                command="get_command_history",
                platform=platform_info["system"],
                working_directory=str(workspace),
                timeout_seconds=0,
                error_type="internal_error",
            ).model_dump(),
        )
        return TextContent(
            type="text",
            text=json.dumps(action_response.model_dump()),  # Empty string instead of None
            **{"metadata": {}}  # Pass as additional fields
        )


@mcp.tool(description="""
Get information about terminal service capabilities and configuration.

Example params:
{"count": 10, "output_format": "markdown"}
""")
async def get_terminal_capabilities(
) -> Union[str, TextContent]:
    capabilities = {
        "platform_info": platform_info,
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
            "max_history_size": max_history_size,
            "current_history_count": len(command_history),
            "working_directory": str(workspace),
            "dangerous_commands_count": len(dangerous_commands),
        },
        "safety_features": [
            "Dangerous command detection",
            "Timeout controls",
            "Error handling and logger",
            "Command validation",
        ],
    }

    formatted_info = f"""# Terminal Service Capabilities

            ## Platform Information
            - **System:** {platform_info["system"]}
            - **Platform:** {platform_info["platform"]}
            - **Architecture:** {platform_info["architecture"]}

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

    action_response = ActionResponse(success=True, message=formatted_info, metadata=capabilities)
    return TextContent(
        type="text",
        text=json.dumps(action_response.model_dump()),  # Empty string instead of None
        **{"metadata": {}}  # Pass as additional fields
    )


# Valid output formats
VALID_OUTPUT_FORMATS = ["markdown", "json", "text"]


def _validate_output_format(output_format: str) -> tuple[bool, str | None]:
    """Validate if the output format is supported.

    Args:
        output_format: Output format string to validate

    Returns:
        Tuple of (is_valid, error_message_with_examples_if_invalid)
    """
    if output_format.lower() in VALID_OUTPUT_FORMATS:
        return True, None

    # Build error message with correct format examples
    error_message = f"""Invalid output format: '{output_format}'

Supported formats and examples:

1. **markdown** (default) - Rich formatted output for readability
   Example: {{"command": "ls -la", "output_format": "markdown"}}
   
2. **json** - Structured JSON output for programmatic processing
   Example: {{"command": "ls -la", "output_format": "json"}}
   
3. **text** - Plain text output for simple display
   Example: {{"command": "ls -la", "output_format": "text"}}

Please use one of the above formats."""

    return False, error_message


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


def _format_command_output(result: CommandResult, output_format: str = "markdown") -> str:
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
                command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, shell=True
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


def main():
    from dotenv import load_dotenv

    load_dotenv(override=True)

    logger.info("Starting  MCP terminal-server...", file=sys.stderr)
    mcp.run(transport="stdio")


# Make the module callable
def __call__():
    """
    Make the module callable for uvx.
    This function is called when the module is executed directly.
    """
    main()


sys.modules[__name__].__call__ = __call__

if __name__ == "__main__":
    main()
