"""
Stream token statistics helpers for CLI display.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from rich.console import Console

try:
    from aworld.models.utils import ModelUtils
except ImportError:
    ModelUtils = None


def _merge_usage_dicts(accumulator: Dict[str, Any], usage: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in usage.items():
        if isinstance(value, dict):
            existing = accumulator.get(key)
            if not isinstance(existing, dict):
                existing = {}
            accumulator[key] = _merge_usage_dicts(dict(existing), value)
            continue
        if isinstance(value, bool):
            accumulator[key] = accumulator.get(key) or value
            continue
        if isinstance(value, (int, float)):
            existing = accumulator.get(key, 0)
            if not isinstance(existing, (int, float)) or isinstance(existing, bool):
                existing = 0
            accumulator[key] = existing + value
            continue
        if value is not None:
            accumulator[key] = value
    return accumulator


def build_llm_usage_observability(
    llm_calls: Optional[List[Dict[str, Any]]],
    *,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a HUD/plugin-friendly usage snapshot from captured llm_calls."""
    if not llm_calls:
        return {}

    candidate_calls = []
    for llm_call in reversed(llm_calls):
        if not isinstance(llm_call, dict):
            continue
        if task_id is not None:
            llm_call_task_id = llm_call.get("task_id")
            if llm_call_task_id == task_id:
                candidate_calls.append(llm_call)
                continue
            if llm_call_task_id is not None:
                continue
        candidate_calls.append(llm_call)

    if task_id is not None:
        exact_task_calls = [call for call in candidate_calls if call.get("task_id") == task_id]
        if exact_task_calls:
            candidate_calls = exact_task_calls
        elif any(isinstance(call, dict) and call.get("task_id") is not None for call in llm_calls):
            return {}

    if not candidate_calls:
        return {}

    latest_call = candidate_calls[0]
    aggregated_usage_normalized: Dict[str, Any] = {}
    aggregated_usage_raw: Dict[str, Any] = {}

    for llm_call in candidate_calls:
        usage_normalized = llm_call.get("usage_normalized")
        if isinstance(usage_normalized, dict):
            aggregated_usage_normalized = _merge_usage_dicts(aggregated_usage_normalized, usage_normalized)

        usage_raw = llm_call.get("usage_raw")
        if isinstance(usage_raw, dict):
            aggregated_usage_raw = _merge_usage_dicts(aggregated_usage_raw, usage_raw)
        elif isinstance(usage_normalized, dict):
            aggregated_usage_raw = _merge_usage_dicts(aggregated_usage_raw, usage_normalized)

    input_tokens = aggregated_usage_normalized.get("prompt_tokens") or 0
    output_tokens = aggregated_usage_normalized.get("completion_tokens") or 0
    total_tokens = aggregated_usage_normalized.get("total_tokens") or (input_tokens + output_tokens)

    cache_usage = {
        key: value
        for key, value in aggregated_usage_raw.items()
        if key in {
            "cache_hit_tokens",
            "cache_write_tokens",
            "prompt_tokens_details",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "input_tokens_details",
        }
    }

    snapshot = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "context_used": total_tokens,
        "request_id": latest_call.get("request_id"),
        "provider_request_id": latest_call.get("provider_request_id"),
        "model": latest_call.get("model"),
        "raw_usage": aggregated_usage_raw,
    }
    if cache_usage:
        snapshot["cache_usage"] = cache_usage
    return snapshot


def format_tokens(n: int) -> str:
    """Format token count: 2900 -> 2.9k, 1000 -> 1k, 100 -> 100."""
    if n >= 1000:
        s = f"{n / 1000:.1f}k"
        return s.replace(".0k", "k")
    return str(n)


def format_timestamp() -> str:
    """Return current time as HH:MM:SS for display."""
    return datetime.now().strftime("%H:%M:%S")


def format_elapsed(sec: float) -> str:
    """Format elapsed seconds for display."""
    if sec < 60:
        return f"{sec:.2f}s" if sec < 10 else f"{sec:.1f}s"
    if sec < 3600:
        return f"{int(sec // 60)}m {int(sec % 60)}s"
    hours = int(sec // 3600)
    minutes = int((sec % 3600) // 60)
    return f"{hours}h {minutes}m"


def format_context_bar(used_tokens: int, max_tokens: int, bar_width: int = 10) -> str:
    """
    Format context usage as a visual progress bar.

    Args:
        used_tokens: Number of tokens used
        max_tokens: Maximum context window size
        bar_width: Width of the progress bar in characters (default: 10)

    Returns:
        Formatted string like "Ctx ████░░░░░░ 41%" or "Ctx 20.2k/200k"

    Examples:
        >>> format_context_bar(82000, 200000, 10)
        'Ctx ████░░░░░░ 41%'
        >>> format_context_bar(150000, 200000, 10)
        'Ctx ███████░░░ 75%'
    """
    if max_tokens <= 0:
        # Fallback: just show token count
        return f"Ctx {format_tokens(used_tokens)}"

    # Calculate percentage
    percentage = min(100, int((used_tokens / max_tokens) * 100))

    # Calculate filled blocks
    filled = int((used_tokens / max_tokens) * bar_width)
    empty = bar_width - filled

    # Unicode block characters for better visual effect
    bar = "█" * filled + "░" * empty

    # Color coding based on usage
    if percentage >= 90:
        color = "red"  # Critical
    elif percentage >= 70:
        color = "yellow"  # Warning
    else:
        color = "green"  # Normal

    return f"[{color}]Ctx {bar} {percentage}%[/{color}]"


def format_context_bar_hud(used_tokens: int, max_tokens: int, bar_width: int = 10) -> str:
    """Format context bar for HUD summary line without rich color markup."""
    context_bar = format_context_bar(used_tokens, max_tokens, bar_width=bar_width)
    for tag in ("[green]", "[/green]", "[yellow]", "[/yellow]", "[red]", "[/red]"):
        context_bar = context_bar.replace(tag, "")
    if context_bar.startswith("Ctx "):
        return f"Ctx: {context_bar[4:]}"
    return context_bar


class StreamTokenStats:
    """
    Tracks token stats for the current (last) streaming agent.
    Only keeps the most recent agent's stats for display.
    When clear() is called (e.g. on agent handoff), stats are snapshotted for history.
    """

    def __init__(self) -> None:
        self._stats: Dict[str, Dict[str, Any]] = {}
        self._last_for_history: Optional[Dict[str, Any]] = None

    def update(
        self,
        agent_id: str,
        agent_name: Optional[str],
        output_tokens: int,
        input_tokens: Optional[int],
        tool_calls_count: int,
        output_estimated: bool = False,
        input_estimated: bool = False,
        tool_calls_estimated: bool = False,
        tool_calls_content_length: Optional[int] = None,
        tool_calls_content_estimated: bool = False,
        tool_calls: Optional[List[Any]] = None,
        content: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> None:
        """Update stats for the current agent. Clears previous agent's data."""
        key = agent_id or "default"
        self._stats.clear()
        self._stats[key] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tool_calls_count": tool_calls_count,
            "tool_calls_content_length": tool_calls_content_length,
            "agent_name": agent_name or key,
            "model_name": model_name,
            "output_estimated": output_estimated,
            "input_estimated": input_estimated,
            "tool_calls_estimated": tool_calls_estimated,
            "tool_calls_content_estimated": tool_calls_content_estimated,
            "tool_calls": tool_calls,
            "content": content,
        }

    def clear(self) -> None:
        """Clear all stats. Call after last chunk / stream ends. Snapshots for history first."""
        stats = self.get_current_stats()
        if stats is not None:
            self._last_for_history = dict(stats)
        self._stats.clear()

    def get_current_stats(self) -> Optional[Dict[str, Any]]:
        """Get the current agent's stats dict, or None if empty."""
        if not self._stats:
            return None
        return next(iter(self._stats.values()), None)

    def get_stats_for_history(self) -> Optional[Dict[str, Any]]:
        """Get stats for history: current stats, or last snapshot from clear(). Consumes snapshot."""
        current = self.get_current_stats()
        if current is not None:
            return current
        snapshot = self._last_for_history
        self._last_for_history = None
        return snapshot

    def _compute_total_tokens(self, stats: Dict[str, Any]) -> Optional[int]:
        """Compute total tokens using num_tokens_from_messages with tool_calls."""
        inp = stats.get("input_tokens")
        out_val = stats.get("output_tokens")
        tool_calls = stats.get("tool_calls")
        content = stats.get("content") or ""
        if content or tool_calls:
            try:
                from aworld.models.utils import num_tokens_from_messages
                msg: Dict[str, Any] = {"role": "assistant", "content": content or ""}
                if tool_calls:
                    tc_list = []
                    for tc in tool_calls:
                        if hasattr(tc, "to_dict"):
                            tc_list.append(tc.to_dict())
                        elif isinstance(tc, dict):
                            tc_list.append(tc)
                        else:
                            tc_list.append({"function": {"name": "", "arguments": str(tc)}})
                    msg["tool_calls"] = tc_list
                output_tokens = num_tokens_from_messages([msg])
                return (inp or 0) + output_tokens
            except Exception:
                pass
        if inp is not None and out_val is not None:
            return inp + out_val
        return None

    def to_hud_usage(self) -> Dict[str, Any]:
        """Export current stats as a HUD-friendly usage snapshot."""
        stats = self.get_current_stats() or self._last_for_history
        if not stats:
            return {}

        input_tokens = stats.get("input_tokens") or 0
        output_tokens = stats.get("output_tokens") or 0
        total_tokens = self._compute_total_tokens(stats) or (input_tokens + output_tokens)
        model_name = stats.get("model_name")

        context_max = 0
        if model_name and ModelUtils:
            try:
                context_max = ModelUtils.get_context_window(model_name)
            except Exception:
                context_max = 0

        context_percent = int((total_tokens / context_max) * 100) if context_max else None
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "context_used": total_tokens,
            "context_max": context_max or None,
            "context_percent": context_percent,
            "model": model_name,
            "tool_calls_count": stats.get("tool_calls_count", 0),
        }

    def format_streaming_line(self, elapsed_str: str) -> Optional[str]:
        """Format the streaming status line for the current agent. Returns None if no stats."""
        stats = self.get_current_stats()
        if not stats:
            return None
        inp = stats.get("input_tokens")
        out_val = stats.get("output_tokens")
        tc_count = stats.get("tool_calls_count", 0)
        aname = stats.get("agent_name") or "agent"
        model_name = stats.get("model_name")
        inp_est = stats.get("input_estimated", False)
        out_est = stats.get("output_estimated", False)
        tc_est = stats.get("tool_calls_estimated", False)
        inp_str = (f"{format_tokens(inp)}" if inp_est else format_tokens(inp)) if inp is not None else "?"
        out_str = (f"{format_tokens(out_val)}" if out_est else format_tokens(out_val)) if out_val is not None else "?"
        parts = [f"[dim]{aname} stats[/dim]"]
        parts.append(f"[dim]↑ {inp_str} in[/dim]")
        parts.append(f"[dim]↓ {out_str} out[/dim]")
        if tc_count > 0:
            tc_str = f"{tc_count}" if tc_est else str(tc_count)
            parts.append(f"[dim]{tc_str} tool call(s)[/dim]")

        # Add context usage visualization
        total_tokens = self._compute_total_tokens(stats)
        if total_tokens is not None and model_name and ModelUtils:
            max_tokens = ModelUtils.get_context_window(model_name)
            if max_tokens > 0:
                # Show visual progress bar
                context_bar = format_context_bar(total_tokens, max_tokens, bar_width=10)
                parts.append(context_bar)
            else:
                # Fallback to token count
                parts.append(f"[dim]~{format_tokens(total_tokens)} tokens[/dim]")
        elif total_tokens is not None:
            # No model info, just show token count
            parts.append(f"[dim]~{format_tokens(total_tokens)} tokens[/dim]")

        parts.append(f"[dim]{elapsed_str}[/dim]")
        parts.append(f"[dim]{format_timestamp()}[/dim]")
        return "  ".join(parts)

    def show_final(self, console: Optional[Console], elapsed_sec: Optional[float] = None) -> None:
        """Display final token stats for the last agent in CLI terminal."""
        if not self._stats or not console:
            return
        for _aid, stats in self._stats.items():
            inp = stats.get("input_tokens")
            out_val = stats.get("output_tokens")
            tc_count = stats.get("tool_calls_count", 0)
            aname = stats.get("agent_name", "agent")
            model_name = stats.get("model_name")
            if inp is not None or out_val is not None or tc_count > 0 or elapsed_sec is not None:
                inp_est = stats.get("input_estimated", False)
                out_est = stats.get("output_estimated", False)
                tc_est = stats.get("tool_calls_estimated", False)
                inp_str = (f"{format_tokens(inp)}" if inp_est else format_tokens(inp)) if inp is not None else "?"
                out_str = (f"{format_tokens(out_val)}" if out_est else format_tokens(out_val)) if out_val is not None else "?"
                parts = [f"[dim]{aname} stats[/dim]"]
                parts.append(f"[dim]↑ {inp_str} tokens[/dim]")
                parts.append(f"[dim]↓ {out_str} tokens[/dim]")
                if tc_count > 0:
                    tc_str = f"{tc_count}" if tc_est else str(tc_count)
                    parts.append(f"[dim]{tc_str} tool call(s)[/dim]")

                # Add context usage visualization
                total_tokens = self._compute_total_tokens(stats)
                if total_tokens is not None and model_name and ModelUtils:
                    max_tokens = ModelUtils.get_context_window(model_name)
                    if max_tokens > 0:
                        # Show visual progress bar
                        context_bar = format_context_bar(total_tokens, max_tokens, bar_width=10)
                        parts.append(context_bar)
                    else:
                        # Fallback to token count
                        parts.append(f"[dim]~{format_tokens(total_tokens)} tokens[/dim]")
                elif total_tokens is not None:
                    # No model info, just show token count
                    parts.append(f"[dim]~{format_tokens(total_tokens)} tokens[/dim]")

                if elapsed_sec is not None:
                    parts.append(f"[dim]{format_elapsed(elapsed_sec)}[/dim]")
                parts.append(f"[dim]{format_timestamp()}[/dim]")
                line = "  ".join(parts)
                console.print(f"\n{line}")
            break
