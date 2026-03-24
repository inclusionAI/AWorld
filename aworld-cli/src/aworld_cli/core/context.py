# coding: utf-8
"""
Context utilities for aworld-cli: token limit check and context optimization.

- Token calculation and limit checking (from pre_llm_cost_hook)
- Context compression, file extraction, and merge (from context_hook)
"""
import json
import os
import traceback
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from aworld.config import AgentMemoryConfig, SummaryPromptConfig
from aworld.core.context.amni import AmniContext
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.logs.util import logger
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemorySummary, MessageMetadata
from aworld.models.utils import num_tokens_from_messages

def get_default_history_path() -> Path:
    """Default CLI history path: ~/.aworld/cli_history.jsonl."""
    return Path.home() / ".aworld" / "cli_history.jsonl"


def get_limit_str() -> Optional[str]:
    """
    Get LIMIT_TOKENS from environment.
    Returns stripped string or None if not set.
    """
    raw = (os.environ.get("LIMIT_TOKENS") or "").strip()
    return raw if raw else None


def check_session_token_limit(
    session_id: Optional[str],
    history_path: Optional[Path] = None,
    limit_str: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> Tuple[bool, Dict[str, Any], int]:
    """
    Check if session token usage exceeds the configured limit.

    When agent_name is provided, checks the current agent's context_window_tokens (ctx).
    Otherwise falls back to total_tokens.

    Args:
        session_id: Current session ID. If None, returns (False, {}, 0).
        history_path: Path to cli_history.jsonl. Default: ~/.aworld/cli_history.jsonl.
        limit_str: Limit value as string (e.g. from LIMIT_TOKENS). If None, uses env.
        agent_name: Current agent name. If provided, uses by_agent[agent_name].context_window_tokens.

    Returns:
        (exceeded, stats, limit)
        - exceeded: True if ctx (or total_tokens) >= limit and limit > 0
        - stats: token stats dict from JSONLHistory.get_token_stats
        - limit: parsed limit (0 if invalid/not set)
    """
    if not session_id:
        return False, {}, 0

    limit_str = limit_str or get_limit_str()
    if not limit_str:
        return False, {}, 0

    try:
        limit = int(limit_str)
    except ValueError:
        return False, {}, 0

    if limit <= 0:
        return False, {}, limit

    history_path = history_path or get_default_history_path()
    if not history_path.exists():
        return False, {}, limit

    try:
        from ..history import JSONLHistory

        history = JSONLHistory(str(history_path))
        stats = history.get_token_stats(session_id=session_id)

        # Use current agent's context_window_tokens (ctx) when agent_name provided
        if agent_name:
            by_agent = stats.get("by_agent") or {}
            agent_stats = by_agent.get(agent_name)
            total = (
                agent_stats.get("context_window_tokens", 0)
                if agent_stats
                else stats.get("total_tokens", 0)
            )
        else:
            total = stats.get("total_tokens", 0)

        exceeded = total >= limit
        return exceeded, stats, limit
    except Exception:
        return False, {}, limit


def extract_file_context(
    root_path: Path,
    ignore_patterns: Optional[list] = None,
) -> str:
    """
    Extract file context from the current directory (reuses ANALYZE_REPOSITORY logic from cast_analysis_tool).
    Imports cast on demand to avoid grep_ast dependency at load time.

    Args:
        root_path: Repository root directory path
        ignore_patterns: List of ignore patterns

    Returns:
        File context string, or empty string on failure
    """
    if ignore_patterns is None:
        ignore_patterns = ["__pycache__", "*.pyc", ".git"]

    try:
        from aworld.experimental.cast import ACast
        from aworld.experimental.cast.models import (
            ImplementationLayer,
            SkeletonLayer,
        )
        from dataclasses import replace

        acast = ACast()
        repo_map = acast.analyze(
            root_path=root_path,
            ignore_patterns=ignore_patterns,
            record_name=Path(root_path).name,
        )

        SKELETON_MAX_CHARS = 80_000
        IMPL_MAX_CHARS = 100_000
        skeleton_len = sum(
            len(s) for s in repo_map.skeleton_layer.file_skeletons.values()
        )
        impl_len = sum(
            len(s.content or "")
            for node in repo_map.implementation_layer.code_nodes.values()
            for s in node.symbols
        )
        repo_map_for_return = replace(
            repo_map,
            skeleton_layer=repo_map.skeleton_layer
            if skeleton_len <= SKELETON_MAX_CHARS
            else SkeletonLayer(
                file_skeletons={},
                symbol_signatures={},
                line_mappings={},
            ),
            implementation_layer=repo_map.implementation_layer
            if impl_len <= IMPL_MAX_CHARS
            else ImplementationLayer(code_nodes={}),
        )

        result = {
            "root_path": str(root_path),
            "ignore_patterns": ignore_patterns,
            "repository_map": repo_map_for_return.to_dict(),
            "analysis_success": True,
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(
            f"Context|extract file context failed: {e} {traceback.format_exc()}"
        )
        return ""


async def run_context_optimization(
    agent_id: str,
    context: Optional[Context] = None,
    session_id: Optional[str] = None,
) -> Tuple[bool, int, int, str, str]:
    """
    Run context optimization: compress history, extract file context, merge and add to agent history.

    Flow:
    1. Call _run_summary_in_background to trigger history compression
    2. Call cast_analysis to extract file context from current directory
    3. Merge both results and write to target agent history via _add

    Args:
        agent_id: Agent ID for the compression
        context: Optional Context object. If provided, session_id/task_id/user_id will be extracted from it.
        session_id: Session ID (required if context is not provided)
        task_id: Task ID (optional, will be auto-generated if not provided)
        user_id: User ID (default: "user")

    Returns:
        (success, original_tokens, new_tokens, message, compressed_content)
        - success: True if successful, False if skipped or failed
        - original_tokens: Token count before optimization
        - new_tokens: Token count after optimization
        - message: Detailed message explaining the result
        - compressed_content: The compressed/summarized content
    """
    # Extract values from context if provided
    if context:
        session_id = getattr(context, "session_id", session_id)

    # Validate required parameters
    if not session_id:
        msg = "Missing session_id - cannot perform compression without a session"
        logger.info(f"Context|skip: {msg}")
        return False, 0, 0, msg, ""

    # Calculate original tokens
    history_path = get_default_history_path()
    _, stats_before, _ = check_session_token_limit(
        session_id=session_id,
        history_path=history_path,
        agent_name=agent_id,
    )
    by_agent = stats_before.get("by_agent") or {}
    agent_stats = by_agent.get(agent_id)
    original_tokens = (
        agent_stats.get("context_window_tokens", 0)
        if agent_stats
        else stats_before.get("total_tokens", 0)
    )

    memory = MemoryFactory.instance()
    if not hasattr(memory, "_run_summary_in_background") or not hasattr(
        memory, "_add"
    ):
        msg = "Memory system does not support summary or add operations"
        logger.info(f"Context|skip: {msg}")
        return False, 0, 0, msg, ""

    # Lower thresholds so compression triggers; use LIMIT_TOKENS as summary_context_length when available
    limit_str = get_limit_str()
    try:
        summary_ctx_limit = int(limit_str) if limit_str else 40960
    except ValueError:
        summary_ctx_limit = 40960

    summary_content = ""

    try:
        # Step 1: Get latest memory_item as trigger, call _run_summary_in_background to trigger compression
        filters = {
            "agent_id": agent_id,
            "session_id": session_id,
            "memory_type": ["init", "message", "summary"],
        }
        # `get_all` is a synchronous method in the current memory implementations,
        # so we must not `await` it.
        all_items = memory.get_all(filters=filters)

        # Step 2: Extract file context from current directory
        root_path = Path(os.getcwd())
        file_context_content = extract_file_context(root_path)

        # Step 3: Configure agent memory with summary settings
        agent_memory_config = AgentMemoryConfig(
            enable_summary=True,
            summary_rounds=1,
            summary_prompts=[SummaryPromptConfig(
                template=((Path(__file__).resolve()).parent / "compact_history_prompt.txt").read_text(encoding="utf-8"),
                summary_rule="",
                summary_schema="",
            ),
            SummaryPromptConfig(
                template=((Path(__file__).resolve()).parent / "compact_workspace_prompt.txt").read_text(encoding="utf-8"),
                summary_rule=f"summarize workspace from: {file_context_content}",
                summary_schema="")],
            summary_context_length=summary_ctx_limit,
            history_scope="session",
        )

        if all_items:
            last_item = all_items[-1]
            await memory._run_summary_in_background(
                memory_item=last_item,
                agent_memory_config=agent_memory_config,
            )
            # Get the newly generated summary
            summary_items = memory.get_all(
                filters={
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "memory_type": "summary",
                }
            )
            if summary_items:
                summary_content = summary_items[-1].content or ""

        # Step 4: Merge both results and write to agent history via _add
        combined_content = summary_content

        if not combined_content:
            msg = f"No content generated for compression - found {len(all_items)} memory items but no summary was created"
            logger.info(f"Context|skip: {msg}")
            return False, 0, 0, msg, ""

        summary_metadata = MessageMetadata(
            agent_id=agent_id,
            agent_name=agent_id,
            session_id=session_id,
        )

        combined_memory = MemorySummary(
            item_ids=[],
            summary=combined_content,
            metadata=summary_metadata,
            memory_type="summary",
            role="user",
        )

        await memory._add(
            memory_item=combined_memory,
            filters=None,
            agent_memory_config=agent_memory_config,
        )

        # Calculate new tokens
        new_tokens = num_tokens_from_messages(
            [combined_memory.to_openai_message()]
        )

        logger.info(
            f"Context|success: added combined context (summary + file) to agent {agent_id}"
        )
        msg = f"Successfully compressed context for agent {agent_id}"
        return True, original_tokens, new_tokens, msg, combined_content

    except Exception as e:
        error_msg = f"Compression failed with error: {str(e)}"
        logger.warning(f"Context|failed: {e} {traceback.format_exc()}")
        return False, 0, 0, error_msg, ""
