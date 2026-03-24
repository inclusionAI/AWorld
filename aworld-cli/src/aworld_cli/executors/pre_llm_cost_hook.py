# coding: utf-8
"""
Pre-LLM cost hook for aworld-cli executor.

Checks token consumption for the current session before each LLM call,
reusing the logic from the /cost command in console.py.
Reads history from ~/.aworld/cli_history.jsonl and displays token stats
for the current session or globally.

When LIMIT_TOKENS is set and current session token usage exceeds it:
- limit_strategy=terminate: exits the conversation immediately.
- limit_strategy=compress (default): runs context compression and continues.
"""
import os
import sys
from pathlib import Path

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.logs.util import logger
from aworld.runners.hook.hook_factory import HookFactory
from aworld.runners.hook.hooks import PreLLMCallHook
from aworld.core.agent.base import AgentFactory, BaseAgent

try:
    from .._globals import console as global_console
except ImportError:
    global_console = None


def _get_limit_strategy() -> str:
    """Resolve limit_strategy from env. Default: compress."""
    s = (os.environ.get("LIMIT_STRATEGY") or "compress").strip().lower()
    return s if s in ("compress", "terminate") else "compress"


@HookFactory.register(name="PreLlmCostHook")
class PreLlmCostHook(PreLLMCallHook):
    """
    Checks token consumption for the current query/session before each LLM call.

    Reuses the logic from the /cost command in console.py (lines 940-963):
    - Reads JSONLHistory from ~/.aworld/cli_history.jsonl
    - If session_id exists, displays history for the current session (including token usage)
    - If no session_id, displays global history

    When LIMIT_TOKENS env is set and session total_tokens >= limit:
    - limit_strategy=terminate: sys.exit(0)
    - limit_strategy=compress (default): run context optimization and continue

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

        agent = AgentFactory.agent_instance(message.sender)
        if not agent:
            logger.warning(f"Agent {message.sender} not found")
            return message

        if not context:
            return message

        try:
            from ..core.context import (
                check_session_token_limit,
                get_default_history_path,
            )
            from ..history import JSONLHistory

            history_path = get_default_history_path()
            if not history_path.exists():
                if console:
                    console.print("[dim]No history file. Start chatting to generate history.[/dim]")
                return message

            session_id = getattr(context, "session_id", None) if context else None
            agent_name = agent.name()
            exceeded, stats, limit = check_session_token_limit(
                session_id=session_id,
                history_path=history_path,
                agent_name=agent_name,
            )

            logger.info(
                f"PreLlmCostHook session_id: {session_id} agent: {agent_name} limit: {limit} exceeded: {exceeded}"
            )

            if exceeded and limit > 0:
                # Use current agent's ctx for display when agent_name provided
                if agent_name:
                    by_agent = stats.get("by_agent") or {}
                    agent_stats = by_agent.get(agent_name)
                    total = agent_stats.get("context_window_tokens", 0) if agent_stats else stats.get("total_tokens", 0)
                else:
                    total = stats.get("total_tokens", 0)
                strategy = _get_limit_strategy()

                if strategy == "terminate":
                    msg = (
                        f"[yellow]Agent {agent_name or 'session'} ctx ({total:,}) exceeds LIMIT_TOKENS ({limit:,}). "
                        "Exiting conversation.[/yellow]"
                    )
                    if console:
                        console.print(msg)
                    else:
                        logger.warning(msg)
                    sys.exit(0)

                # strategy == "compress": run context optimization and continue
                try:
                    from ..core.context import run_context_optimization
                    ok, tokens_before, tokens_after, msg, compressed_content = await run_context_optimization(message.sender, context)
                    if ok:
                        ratio = ((tokens_before - tokens_after) / tokens_before) * 100 if tokens_before > 0 else 0
                        if console:
                            console.print(
                                f"[bold green]Context Optimization Complete[/bold green]"
                                f"\nOriginal: {tokens_before:,} tokens"
                                f"\nCurrent:  {tokens_after:,} tokens"
                                f"\nReduced:  {tokens_before - tokens_after:,} tokens ({ratio:.1f}%)"
                                f"\n[dim]Agent {agent_name or 'session'} continuing with optimized context.[/dim]"
                            )
                    else:
                        if console:
                            console.print(
                                f"[yellow]⚠️ Agent {agent_name or 'session'} ctx ({total:,}) exceeds limit ({limit:,}).[/yellow]\n"
                                f"[dim]Context compression skipped: {msg}[/dim]"
                            )
                except Exception as comp_err:
                    logger.warning(
                        f"PreLlmCostHook compression failed: {comp_err}. Continuing."
                    )

            # Same as console /cost: show cost stats for current session or global
            history = JSONLHistory(str(history_path))
            display = history.format_cost_display(session_id=session_id)
            logger.info(f"[PreLlmCostHook] {display}")

        except Exception as e:
            if console:
                console.print(f"[dim]PreLlmCostHook: {e}[/dim]")
            logger.debug(f"PreLlmCostHook failed: {e}")

        return message
