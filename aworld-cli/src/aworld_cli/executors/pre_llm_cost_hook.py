# coding: utf-8
"""
Pre-LLM cost hook for aworld-cli executor.

Checks token consumption for the current session before each LLM call,
reusing the logic from the /cost command in console.py.
Reads history from ~/.aworld/cli_history.jsonl and displays token stats
for the current session or globally.

When LIMIT_TOKENS is set and current session token usage exceeds it,
exits the conversation immediately.
"""
import os
import sys
from pathlib import Path

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.logs.util import logger
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PreLLMCallHook

try:
    from .._globals import console as global_console
except ImportError:
    global_console = None


@HookFactory.register(name="PreLlmCostHook")
class PreLlmCostHook(PreLLMCallHook):
    """
    Checks token consumption for the current query/session before each LLM call.

    Reuses the logic from the /cost command in console.py (lines 940-963):
    - Reads JSONLHistory from ~/.aworld/cli_history.jsonl
    - If session_id exists, displays history for the current session (including token usage)
    - If no session_id, displays global history

    When LIMIT_TOKENS env is set and session total_tokens >= limit, exits the
    conversation immediately (sys.exit(0)).

    Only active in CLI environment (when history file exists and console is available).
    """

    async def exec(self, message: Message, context: Context = None) -> Message:
        """
        Displays token consumption for the current session before each LLM call
        (same logic as the /cost command).
        """
        console = message.headers.get("console") if message.headers else None
        if not console and global_console:
            console = global_console

        if not context:
            return message

        try:
            from ..history import JSONLHistory

            history_path = Path.home() / ".aworld" / "cli_history.jsonl"
            if not history_path.exists():
                if console:
                    console.print("[dim]No history file. Start chatting to generate history.[/dim]")
                return message

            history = JSONLHistory(str(history_path))
            session_id = getattr(context, "session_id", None) if context else None

            # Check LIMIT_TOKENS: exit conversation if session usage exceeds limit
            limit_str = (os.environ.get("LIMIT_TOKENS") or "").strip()
            logger.info(f"PreLlmCostHook session_id: {session_id} limit_str: {limit_str}")
            if limit_str and session_id:
                try:
                    limit = int(limit_str)
                    if limit > 0:
                        stats = history.get_token_stats(session_id=session_id)
                        total = stats.get("total_tokens", 0)
                        if total >= limit:
                            msg = (
                                f"[yellow]Session token usage ({total:,}) exceeds LIMIT_TOKENS ({limit:,}). "
                                "Exiting conversation.[/yellow]"
                            )
                            if console:
                                console.print(msg)
                            else:
                                logger.warning(msg)
                            sys.exit(0)
                except ValueError:
                    pass

            # Same as console /cost: show current session if available, else global
            display = history.format_history_display(session_id=session_id, limit=5)

            logger.info(f"[PreLlmCostHook] {display}")

        except Exception as e:
            if console:
                console.print(f"[dim]PreLlmCostHook: {e}[/dim]")
            logger.debug(f"PreLlmCostHook failed: {e}")

        return message
