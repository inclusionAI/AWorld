"""Permission decision handling for hooks

Provides permission decision logic for hooks, including:
- Interactive CLI prompting (ask mode)
- Non-interactive environment detection and auto-deny
- Environment variable overrides
"""

import logging
import os
import sys
from typing import Literal, Optional, Tuple, Callable, Awaitable

logger = logging.getLogger(__name__)

# Type for interactive prompt callback
InteractivePromptCallback = Callable[[str, Optional[dict]], Awaitable[Literal['allow', 'deny']]]


class PermissionDecisionHandler:
    """Handle permission decisions for hooks

    Supports three modes:
    - 'allow': Always allow
    - 'deny': Always deny
    - 'ask': Prompt user in interactive environments, auto-deny in non-interactive

    Attributes:
        _is_interactive: Whether running in an interactive environment
        _permission_mode: Override mode from environment variable
        _interactive_prompt: Optional callback for interactive prompting
    """

    def __init__(self):
        """Initialize permission handler"""
        self._is_interactive = self._detect_interactive()
        self._permission_mode = os.environ.get('AWORLD_PERMISSION_MODE', None)
        self._interactive_prompt: Optional[InteractivePromptCallback] = None

        logger.debug(
            f"PermissionDecisionHandler initialized: "
            f"interactive={self._is_interactive}, "
            f"mode={self._permission_mode}"
        )

    def set_interactive_prompt(self, callback: Optional[InteractivePromptCallback]):
        """Set the interactive prompt callback (used by CLI)

        Args:
            callback: Async function that prompts user and returns 'allow' or 'deny'
        """
        self._interactive_prompt = callback
        logger.debug(f"Interactive prompt callback {'set' if callback else 'cleared'}")

    def _detect_interactive(self) -> bool:
        """Detect if running in an interactive environment

        Checks if stdin is a TTY (terminal). Returns False for:
        - CI/CD environments
        - Piped input
        - Redirected input
        - Non-terminal execution

        Returns:
            True if interactive terminal detected
        """
        try:
            return sys.stdin.isatty()
        except Exception:
            return False

    async def resolve_permission(
        self,
        decision: Literal['allow', 'deny', 'ask'],
        reason: Optional[str] = None,
        context: Optional[dict] = None
    ) -> Tuple[Literal['allow', 'deny'], str]:
        """Resolve a permission decision

        Resolution logic:
        1. If decision is 'allow' or 'deny', return as-is
        2. If decision is 'ask' and AWORLD_PERMISSION_MODE is set, use that
        3. If decision is 'ask' and non-interactive, return 'deny'
        4. If decision is 'ask' and interactive, call interactive prompt (future)

        Args:
            decision: Initial permission decision from hook
            reason: Optional reason for the decision
            context: Optional context dict (tool_name, args, etc.)

        Returns:
            Tuple of (final_decision, resolution_reason)
        """
        # Direct allow/deny
        if decision in ('allow', 'deny'):
            return decision, reason or f"Hook returned '{decision}'"

        # 'ask' mode resolution
        if decision == 'ask':
            # Check environment variable override
            if self._permission_mode:
                if self._permission_mode.lower() == 'allow':
                    return 'allow', f"AWORLD_PERMISSION_MODE={self._permission_mode}"
                elif self._permission_mode.lower() == 'deny':
                    return 'deny', f"AWORLD_PERMISSION_MODE={self._permission_mode}"

            # Non-interactive environment: auto-deny
            if not self._is_interactive:
                logger.info(
                    f"Permission 'ask' resolved to 'deny' (non-interactive environment). "
                    f"Reason: {reason}"
                )
                return 'deny', (
                    f"Auto-denied in non-interactive environment. "
                    f"Set AWORLD_PERMISSION_MODE=allow to override. "
                    f"Original reason: {reason}"
                )

            # Interactive environment: call interactive prompt
            if self._interactive_prompt:
                try:
                    user_decision = await self._interactive_prompt(reason or "Permission requested", context)
                    logger.info(f"User interactively decided: {user_decision}")
                    return user_decision, f"User interactively decided: {user_decision}. Reason: {reason}"
                except Exception as e:
                    logger.error(f"Interactive prompt failed: {e}")
                    return 'deny', f"Interactive prompt failed: {e}"
            else:
                logger.warning(
                    f"Permission 'ask' in interactive environment but no prompt callback set. "
                    f"Auto-denying. Reason: {reason}"
                )
                return 'deny', (
                    f"Interactive prompting not configured (no callback set). "
                    f"Auto-denied. Original reason: {reason}"
                )

        # Fallback: unknown decision value
        logger.error(f"Unknown permission decision: {decision!r}")
        return 'deny', f"Unknown permission decision: {decision}"

    def resolve_permission_sync(
        self,
        decision: Literal['allow', 'deny', 'ask'],
        reason: Optional[str] = None,
        context: Optional[dict] = None
    ) -> Tuple[Literal['allow', 'deny'], str]:
        """Synchronous version of resolve_permission

        Uses asyncio.run() for async operations in sync context.

        Args:
            decision: Initial permission decision from hook
            reason: Optional reason for the decision
            context: Optional context dict (tool_name, args, etc.)

        Returns:
            Tuple of (final_decision, resolution_reason)
        """
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already in async context, cannot use asyncio.run()
                # Fall back to auto-deny for safety
                logger.warning(
                    f"Permission 'ask' called in sync context with running event loop. "
                    f"Auto-denying. Reason: {reason}"
                )
                return 'deny', f"Cannot resolve 'ask' in sync context with running loop. Auto-denied. Reason: {reason}"
            else:
                return asyncio.run(self.resolve_permission(decision, reason, context))
        except RuntimeError:
            # No event loop, create one
            return asyncio.run(self.resolve_permission(decision, reason, context))


# Global singleton instance
_permission_handler: Optional[PermissionDecisionHandler] = None


def get_permission_handler() -> PermissionDecisionHandler:
    """Get the global permission handler singleton

    Returns:
        PermissionDecisionHandler instance
    """
    global _permission_handler
    if _permission_handler is None:
        _permission_handler = PermissionDecisionHandler()
    return _permission_handler
