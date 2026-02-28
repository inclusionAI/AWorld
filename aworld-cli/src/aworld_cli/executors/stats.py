"""
Stream token statistics helpers for CLI display.
"""
from typing import Any, Dict, List, Optional

from rich.console import Console


def format_tokens(n: int) -> str:
    """Format token count: 2900 -> 2.9k, 1000 -> 1k, 100 -> 100."""
    if n >= 1000:
        s = f"{n / 1000:.1f}k"
        return s.replace(".0k", "k")
    return str(n)


def format_chars(n: int) -> str:
    """Format character count for display."""
    if n >= 1000:
        s = f"{n / 1000:.1f}k"
        return s.replace(".0k", "k")
    return str(n)


def format_elapsed(sec: float) -> str:
    """Format elapsed seconds for display."""
    if sec < 60:
        return f"{sec:.2f}s" if sec < 10 else f"{sec:.1f}s"
    if sec < 3600:
        return f"{int(sec // 60)}m {int(sec % 60)}s"
    hours = int(sec // 3600)
    minutes = int((sec % 3600) // 60)
    return f"{hours}h {minutes}m"


class StreamTokenStats:
    """
    Tracks token stats for the current (last) streaming agent.
    Only keeps the most recent agent's stats for display.
    """

    def __init__(self) -> None:
        self._stats: Dict[str, Dict[str, Any]] = {}

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
            "output_estimated": output_estimated,
            "input_estimated": input_estimated,
            "tool_calls_estimated": tool_calls_estimated,
            "tool_calls_content_estimated": tool_calls_content_estimated,
            "tool_calls": tool_calls,
            "content": content,
        }

    def clear(self) -> None:
        """Clear all stats. Call after last chunk / stream ends."""
        self._stats.clear()

    def get_current_stats(self) -> Optional[Dict[str, Any]]:
        """Get the current agent's stats dict, or None if empty."""
        if not self._stats:
            return None
        return next(iter(self._stats.values()), None)

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

    def format_streaming_line(self, elapsed_str: str) -> Optional[str]:
        """Format the streaming status line for the current agent. Returns None if no stats."""
        stats = self.get_current_stats()
        if not stats:
            return None
        inp = stats.get("input_tokens")
        out_val = stats.get("output_tokens")
        tc_count = stats.get("tool_calls_count", 0)
        aname = stats.get("agent_name") or "agent"
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
        total_tokens = self._compute_total_tokens(stats)
        if total_tokens is not None:
            parts.append(f"[dim]~{format_tokens(total_tokens)} tokens[/dim]")
        parts.append(f"[dim]{elapsed_str}[/dim]")
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
                total_tokens = self._compute_total_tokens(stats)
                if total_tokens is not None:
                    parts.append(f"[dim]~{format_tokens(total_tokens)} tokens[/dim]")
                if elapsed_sec is not None:
                    parts.append(f"[dim]{format_elapsed(elapsed_sec)}[/dim]")
                line = "  ".join(parts)
                console.print(f"\n{line}")
            break
