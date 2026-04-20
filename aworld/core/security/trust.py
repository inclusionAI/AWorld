"""Workspace trust management for secure hook loading.

This module implements a minimal workspace trust mechanism to prevent
arbitrary code execution from untrusted projects.
"""

import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class WorkspaceTrust:
    """Workspace trust checker for secure hook loading.

    This class implements a simple trust model:
    1. If AWORLD_TRUST_ALL_WORKSPACES=true → trust all (dev/test only)
    2. If .aworld/trusted marker exists → trust this workspace
    3. Otherwise → untrusted, config hooks will not be loaded

    Design rationale:
    - Explicit opt-in: Users must manually mark workspaces as trusted
    - Per-workspace granularity: No global trust patterns that could be exploited
    - Environment override: Developers can bypass for convenience
    - Fail-secure: Default is untrusted
    """

    @staticmethod
    def is_trusted(workspace_path: str) -> bool:
        """Check if a workspace is trusted.

        Args:
            workspace_path: Absolute path to the workspace directory

        Returns:
            True if the workspace is trusted, False otherwise

        Trust rules (in priority order):
        1. AWORLD_TRUST_ALL_WORKSPACES=true → trust all
        2. .aworld/trusted marker file exists → trust this workspace
        3. Otherwise → untrusted
        """
        # Developer mode: trust all workspaces
        if os.getenv('AWORLD_TRUST_ALL_WORKSPACES') == 'true':
            logger.warning(
                "AWORLD_TRUST_ALL_WORKSPACES=true, all workspaces are trusted. "
                "This should only be used in development/test environments."
            )
            return True

        # Check project-level trust marker
        trust_marker = Path(workspace_path) / '.aworld' / 'trusted'
        is_trusted = trust_marker.exists()

        if not is_trusted:
            logger.info(
                f"Workspace is not trusted: {workspace_path}. "
                f"Config hooks will not be loaded. "
                f"To trust this workspace, run: touch {trust_marker}"
            )

        return is_trusted

    @staticmethod
    def mark_trusted(workspace_path: str) -> None:
        """Mark a workspace as trusted by creating the trust marker.

        Args:
            workspace_path: Absolute path to the workspace directory

        Note:
            This is typically called manually by the user via CLI,
            not automatically by the system.
        """
        trust_marker = Path(workspace_path) / '.aworld' / 'trusted'
        trust_marker.parent.mkdir(parents=True, exist_ok=True)
        trust_marker.touch()
        logger.info(f"Workspace marked as trusted: {workspace_path}")

    @staticmethod
    def get_workspace_from_config_path(config_path: str) -> Optional[str]:
        """Extract workspace path from config file path.

        Args:
            config_path: Path to hooks.yaml (e.g., /path/to/project/.aworld/hooks.yaml)

        Returns:
            Workspace path (parent of .aworld directory), or None if invalid
        """
        config_path_obj = Path(config_path).resolve()

        # Check if path contains .aworld directory
        if '.aworld' not in config_path_obj.parts:
            logger.warning(f"Config path does not contain .aworld: {config_path}")
            return None

        # Find .aworld in path and return its parent
        parts = list(config_path_obj.parts)
        try:
            aworld_index = parts.index('.aworld')
            workspace_parts = parts[:aworld_index]
            if not workspace_parts:
                return None
            return str(Path(*workspace_parts))
        except (ValueError, TypeError):
            return None
