# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Detect available Python command for starting MCP tool servers."""

import shutil
import subprocess
from typing import Optional

from aworld.logs.util import logger


class PythonCommandDetector:
    """Detect usable Python command (python3, python, etc.)."""

    _cached_command: Optional[str] = None

    @classmethod
    def get_python_command(cls) -> Optional[str]:
        """
        Get a working Python command.

        Tries python3, python, then specific versions.
        Returns None if none found.
        """
        if cls._cached_command is not None:
            return cls._cached_command

        candidates = [
            "python3",
            "python",
            "python3.12",
            "python3.11",
            "python3.10",
            "python3.9",
        ]

        for cmd in candidates:
            if cls._is_python_available(cmd):
                cls._cached_command = cmd
                logger.info(f"Detected Python command for tool servers: {cmd}")
                return cmd

        logger.warning("No Python command found in PATH for tool servers")
        return None

    @staticmethod
    def _is_python_available(cmd: str) -> bool:
        """Check if the command exists and runs Python."""
        path = shutil.which(cmd)
        if not path:
            return False
        try:
            result = subprocess.run(
                [cmd, "--version"],
                capture_output=True,
                timeout=2,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    @classmethod
    def clear_cache(cls) -> None:
        """Clear cached command (e.g. for tests)."""
        cls._cached_command = None
