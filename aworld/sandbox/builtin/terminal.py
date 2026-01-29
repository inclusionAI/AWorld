# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Builtin terminal tool implementation."""

import asyncio
import json
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from aworld.logs.util import logger
from aworld.sandbox.builtin.base import BuiltinTool, SERVICE_TERMINAL


class TerminalTool(BuiltinTool):
    """Builtin terminal tool implementation."""
    
    def __init__(self, workspace: Optional[str] = None):
        """
        Args:
            workspace: Working directory for command execution. If None, uses default workspace.
        """
        super().__init__(SERVICE_TERMINAL)
        self.workspace = Path(workspace) if workspace else Path.home() / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        
        self.platform_info = {
            "system": platform.system(),
            "platform": platform.platform(),
            "architecture": platform.architecture()[0],
        }
        
        self.dangerous_commands = [
            "rm -rf /",
            "mkfs",
            "dd if=",
            ":(){ :|:& };:",
            "del /f /s /q",
            "diskpart",
            "sudo rm",
            "sudo dd",
            "sudo mkfs",
        ]
    
    def _check_command_safety(self, command: str) -> tuple[bool, Optional[str]]:
        """Check if command is safe to execute."""
        command_lower = command.lower().strip()
        
        for dangerous_cmd in self.dangerous_commands:
            if dangerous_cmd.lower() in command_lower:
                return False, f"Command contains dangerous pattern: {dangerous_cmd}"
        
        return True, None
    
    async def execute(self, tool_name: str, **kwargs) -> Any:
        """Execute a terminal tool."""
        method = getattr(self, tool_name, None)
        if not method or not callable(method):
            raise ValueError(f"Unknown tool: {tool_name}")
        return await method(**kwargs)
    
    async def run_code(
        self,
        code: str,
        timeout: int = 30,
        output_format: str = "markdown"
    ) -> str:
        """Execute terminal command or code.
        
        Args:
            code: Terminal command or code to execute
            timeout: Command timeout in seconds (default: 30)
            output_format: Output format: 'markdown', 'json', or 'text'
            
        Returns:
            Formatted command execution result
        """
        is_safe, safety_reason = self._check_command_safety(code)
        if not is_safe:
            error_msg = f"Command rejected for security reasons: {safety_reason}"
            return self._format_error_result(code, error_msg, "security_violation")
        
        logger.info(f"🔧 Executing command: {code}")
        
        start_time = time.time()
        result = await self._execute_command(code, timeout)
        execution_time = time.time() - start_time
        
        formatted_output = self._format_command_output(result, output_format)
        
        response = {
            "success": result["success"],
            "message": formatted_output,
            "metadata": {
                "command": code,
                "platform": self.platform_info["system"],
                "working_directory": str(self.workspace),
                "timeout_seconds": timeout,
                "execution_time": execution_time,
                "return_code": result["return_code"],
                "safety_check_passed": True,
            }
        }
        
        if not result["success"]:
            response["metadata"]["error_type"] = "execution_failure"
        
        return json.dumps(response, ensure_ascii=False)
    
    async def _execute_command(self, command: str, timeout: int) -> dict:
        """Execute command asynchronously with timeout."""
        start_time = datetime.now()
        
        try:
            if self.platform_info["system"] == "Windows":
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    shell=True,
                    cwd=str(self.workspace),
                )
            else:
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    shell=True,
                    executable="/bin/bash",
                    cwd=str(self.workspace),
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
                return {
                    "command": command,
                    "success": False,
                    "stdout": "",
                    "stderr": f"Command timed out after {timeout} seconds",
                    "return_code": -1,
                    "duration": duration,
                    "timestamp": start_time.isoformat(),
                }
            
            duration = str(datetime.now() - start_time)
            return {
                "command": command,
                "success": return_code == 0,
                "stdout": stdout,
                "stderr": stderr,
                "return_code": return_code,
                "duration": duration,
                "timestamp": start_time.isoformat(),
            }
            
        except Exception as e:
            duration = str(datetime.now() - start_time)
            return {
                "command": command,
                "success": False,
                "stdout": "",
                "stderr": f"Error executing command: {str(e)}",
                "return_code": -1,
                "duration": duration,
                "timestamp": start_time.isoformat(),
            }
    
    def _format_command_output(self, result: dict, output_format: str = "markdown") -> str:
        """Format command execution results."""
        if output_format == "json":
            return json.dumps(result, indent=2, ensure_ascii=False)
        
        elif output_format == "text":
            output_parts = [
                f"Command: {result['command']}",
                f"Status: {'SUCCESS' if result['success'] else 'FAILED'}",
                f"Duration: {result['duration']}",
                f"Return Code: {result['return_code']}",
            ]
            
            if result.get("stdout"):
                output_parts.extend(["\nOutput:", result["stdout"]])
            
            if result.get("stderr"):
                output_parts.extend(["\nErrors/Warnings:", result["stderr"]])
            
            return "\n".join(output_parts)
        
        else:  # markdown (default)
            status_emoji = "✅" if result["success"] else "❌"
            
            output_parts = [
                f"# Terminal Command Execution {status_emoji}",
                f"**Command:** `{result['command']}`",
                f"**Status:** {'SUCCESS' if result['success'] else 'FAILED'}",
                f"**Duration:** {result['duration']}",
                f"**Return Code:** {result['return_code']}",
                f"**Timestamp:** {result['timestamp']}",
            ]
            
            if result.get("stdout"):
                output_parts.extend(["\n## Output", "```", result["stdout"].strip(), "```"])
            
            if result.get("stderr"):
                output_parts.extend(["\n## Errors/Warnings", "```", result["stderr"].strip(), "```"])
            
            return "\n".join(output_parts)
    
    def _format_error_result(self, command: str, error_msg: str, error_type: str) -> str:
        """Format error result."""
        response = {
            "success": False,
            "message": error_msg,
            "metadata": {
                "command": command,
                "platform": self.platform_info["system"],
                "working_directory": str(self.workspace),
                "safety_check_passed": False,
                "error_type": error_type,
            }
        }
        return json.dumps(response, ensure_ascii=False)
