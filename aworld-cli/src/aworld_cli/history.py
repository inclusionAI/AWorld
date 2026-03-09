import json
import os
import time
from typing import Iterable, List, Dict, Optional, Iterator
from prompt_toolkit.history import History
import logging

logger = logging.getLogger(__name__)



class JSONLHistory(History):
    """
    History class that stores history in JSONL format, similar to Claude Code.
    Each line is a JSON object containing metadata like timestamp, session_id, and cwd.
    """

    def __init__(self, filename: str, session_id: Optional[str] = None) -> None:
        self.filename = filename
        self.session_id = session_id
        super().__init__()

    def load_history_strings(self) -> Iterable[str]:
        """
        Load history strings from the file.
        This is used by prompt_toolkit to populate the history buffer.
        We return ALL history records (global history), regardless of the current session.
        We only return the 'display' field (the actual command).
        """
        if not os.path.exists(self.filename):
            return []

        strings = []
        try:
            with open(self.filename, "rb") as f:
                for line in f:
                    try:
                        line_str = line.decode("utf-8")
                        data = json.loads(line_str)
                        if "display" in data:
                            strings.append(data["display"])
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        # Ignore malformed lines
                        continue
        except IOError as e:
            logger.warning(f"Failed to read history file {self.filename}: {e}")
            return []

        # prompt_toolkit expects history in reverse order (newest first)
        # So we need to reverse the list before returning
        return reversed(strings)

    def store_string(self, string: str, token_stats: Optional[Dict] = None, aggregate_with_previous: bool = False) -> None:
        """
        Store a string in the history file.
        We wrap the command string in a JSON object with metadata.

        Args:
            string: The command/query string to store
            token_stats: Optional token statistics dict with keys:
                - input_tokens: int
                - output_tokens: int
                - total_tokens: int
                - duration_seconds: float (optional)
                - model_name: str
            aggregate_with_previous: If True, merge token_stats into the last matching record
                (same sessionId + display) instead of appending a new line.
        """
        record = {
            "display": string,
            "timestamp": int(time.time() * 1000),  # Milliseconds
            "cwd": os.getcwd(),
        }

        if self.session_id:
            record["sessionId"] = self.session_id

        if token_stats:
            ts = {k: v for k, v in token_stats.items() if k != "tool_calls_count"}
            # Add by_model for per-model aggregation (single round)
            model_name = ts.get("model_name", "unknown")
            ts["by_model"] = {
                model_name: {
                    "input_tokens": ts.get("input_tokens", 0),
                    "output_tokens": ts.get("output_tokens", 0),
                    "total_tokens": ts.get("total_tokens", 0),
                    "duration_seconds": ts.get("duration_seconds", 0),
                    "rounds": 1,
                }
            }
            record["token_stats"] = ts

        try:
            if aggregate_with_previous and os.path.exists(self.filename) and token_stats:
                # Find last matching record and merge token_stats (per-model)
                with open(self.filename, "rb") as f:
                    lines = f.readlines()
                match_idx = -1
                for i in range(len(lines) - 1, -1, -1):
                    try:
                        data = json.loads(lines[i].decode("utf-8"))
                        if data.get("display") == string and data.get("sessionId") == self.session_id:
                            match_idx = i
                            break
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                if match_idx >= 0:
                    existing = json.loads(lines[match_idx].decode("utf-8"))
                    existing_ts = existing.get("token_stats") or {}
                    existing_by = existing_ts.get("by_model") or {}
                    # Migrate old format (no by_model) to by_model
                    if not existing_by and existing_ts.get("model_name"):
                        old_model = existing_ts.get("model_name", "unknown")
                        existing_by = {
                            old_model: {
                                "input_tokens": existing_ts.get("input_tokens", 0),
                                "output_tokens": existing_ts.get("output_tokens", 0),
                                "total_tokens": existing_ts.get("total_tokens", 0),
                                "duration_seconds": existing_ts.get("duration_seconds", 0),
                                "rounds": 1,
                            }
                        }
                    # Merge current round's model into by_model
                    cur_model = token_stats.get("model_name", "unknown")
                    cur_by = existing_by.get(cur_model) or {
                        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "duration_seconds": 0, "rounds": 0
                    }
                    existing_by[cur_model] = {
                        "input_tokens": cur_by.get("input_tokens", 0) + token_stats.get("input_tokens", 0),
                        "output_tokens": cur_by.get("output_tokens", 0) + token_stats.get("output_tokens", 0),
                        "total_tokens": cur_by.get("total_tokens", 0) + token_stats.get("total_tokens", 0),
                        "duration_seconds": cur_by.get("duration_seconds", 0) + token_stats.get("duration_seconds", 0),
                        "rounds": cur_by.get("rounds", 0) + 1,
                    }
                    # Recompute totals from by_model
                    tot_in = sum(m.get("input_tokens", 0) for m in existing_by.values())
                    tot_out = sum(m.get("output_tokens", 0) for m in existing_by.values())
                    tot_dur = sum(m.get("duration_seconds", 0) for m in existing_by.values())
                    merged = {
                        "input_tokens": tot_in,
                        "output_tokens": tot_out,
                        "total_tokens": tot_in + tot_out,
                        "duration_seconds": tot_dur,
                        "model_name": ", ".join(sorted(existing_by.keys())),  # All models used
                        "by_model": existing_by,
                    }
                    existing["token_stats"] = merged
                    existing["timestamp"] = record["timestamp"]
                    lines[match_idx] = (json.dumps(existing, ensure_ascii=False) + "\n").encode("utf-8")
                    with open(self.filename, "wb") as f:
                        f.writelines(lines)
                    return
            # Append new record
            with open(self.filename, "ab") as f:
                f.write(json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n")
        except IOError:
            pass

    def get_records(self, session_id: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """
        Retrieve structured history records.

        Args:
            session_id: If provided, filter by this session ID. If None, return all records.
            limit: Maximum number of records to return (from newest).

        Returns:
            List of history records (dicts), ordered chronologically (oldest to newest).
        """
        if not os.path.exists(self.filename):
            return []

        records = []
        try:
            with open(self.filename, "rb") as f:
                # Read all lines (for a large file, we might want to read from end, but for CLI history it's usually fine)
                for line in f:
                    try:
                        line_str = line.decode("utf-8")
                        data = json.loads(line_str)

                        # Filter by session_id if requested
                        if session_id is not None and data.get("sessionId") != session_id:
                            continue

                        records.append(data)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
        except IOError as e:
            logger.warning(f"Failed to read history file {self.filename}: {e}")
            return []

        # Return newest 'limit' records
        return records[-limit:]

    def get_token_stats(self, session_id: Optional[str] = None) -> Dict:
        """
        Calculate token usage statistics from history records.

        Args:
            session_id: If provided, calculate stats for this session only.
                       If None, calculate global stats.

        Returns:
            Dict with aggregated token statistics (no tool_calls; includes time stats).
        """
        records = self.get_records(session_id=session_id, limit=10000)

        stats = {
            "total_queries": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "records_with_stats": 0,
            "first_query_time": None,
            "last_query_time": None,
            "total_duration_seconds": 0.0,
            "by_model": {},
        }

        for record in records:
            ts = record.get("timestamp", 0)
            if ts:
                if stats["first_query_time"] is None or ts < stats["first_query_time"]:
                    stats["first_query_time"] = ts
                if stats["last_query_time"] is None or ts > stats["last_query_time"]:
                    stats["last_query_time"] = ts

            token_stats = record.get("token_stats")
            if token_stats:
                stats["records_with_stats"] += 1
                stats["input_tokens"] += token_stats.get("input_tokens", 0)
                stats["output_tokens"] += token_stats.get("output_tokens", 0)
                stats["total_tokens"] += token_stats.get("total_tokens", 0)
                stats["total_duration_seconds"] += token_stats.get("duration_seconds", 0)

                # Support per-model breakdown (by_model) or flat model_name
                by_model = token_stats.get("by_model") or {}
                if by_model:
                    for model_name, mstats in by_model.items():
                        if model_name not in stats["by_model"]:
                            stats["by_model"][model_name] = {
                                "queries": 0,
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "total_tokens": 0,
                                "total_duration_seconds": 0.0,
                                "first_time": None,
                                "last_time": None,
                            }
                        ms = stats["by_model"][model_name]
                        ms["queries"] += 1
                        ms["input_tokens"] += mstats.get("input_tokens", 0)
                        ms["output_tokens"] += mstats.get("output_tokens", 0)
                        ms["total_tokens"] += mstats.get("total_tokens", 0)
                        ms["total_duration_seconds"] += mstats.get("duration_seconds", 0)
                        if ts:
                            if ms["first_time"] is None or ts < ms["first_time"]:
                                ms["first_time"] = ts
                            if ms["last_time"] is None or ts > ms["last_time"]:
                                ms["last_time"] = ts
                else:
                    model_name = token_stats.get("model_name", "unknown")
                    if model_name not in stats["by_model"]:
                        stats["by_model"][model_name] = {
                            "queries": 0,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                            "total_duration_seconds": 0.0,
                            "first_time": None,
                            "last_time": None,
                        }
                    ms = stats["by_model"][model_name]
                    ms["queries"] += 1
                    ms["input_tokens"] += token_stats.get("input_tokens", 0)
                    ms["output_tokens"] += token_stats.get("output_tokens", 0)
                    ms["total_tokens"] += token_stats.get("total_tokens", 0)
                    ms["total_duration_seconds"] += token_stats.get("duration_seconds", 0)
                    if ts:
                        if ms["first_time"] is None or ts < ms["first_time"]:
                            ms["first_time"] = ts
                        if ms["last_time"] is None or ts > ms["last_time"]:
                            ms["last_time"] = ts

            stats["total_queries"] += 1

        return stats

    def format_history_display(self, session_id: Optional[str] = None, limit: int = 5) -> str:
        """
        Format history records for display.

        Args:
            session_id: If provided, filter by this session ID. If None, show all records.
            limit: Maximum number of records to display (from newest).

        Returns:
            Formatted string for display.
        """
        from datetime import datetime

        records = self.get_records(session_id=session_id, limit=limit)

        if not records:
            return "No history records found."

        lines = []
        lines.append(f"\n{'='*60}")
        if session_id:
            lines.append(f"Current Session History ({session_id})")
        else:
            lines.append("Global History (All Sessions)")
        lines.append(f"Query Count: {len(records)}")
        lines.append(f"{'='*60}\n")

        for i, record in enumerate(records, 1):
            timestamp_ms = record.get("timestamp", 0)
            timestamp_dt = datetime.fromtimestamp(timestamp_ms / 1000)
            timestamp_str = timestamp_dt.strftime("%Y-%m-%d %H:%M:%S")
            display = record.get("display", "")

            token_stats = record.get("token_stats")
            if token_stats:
                input_tok = token_stats.get("input_tokens", 0)
                output_tok = token_stats.get("output_tokens", 0)
                total_tok = token_stats.get("total_tokens", 0)
                duration = token_stats.get("duration_seconds")
                tot_str = f" ↑{input_tok} / ↓{output_tok}"
                if duration is not None and duration > 0:
                    tot_str += f" ({duration:.1f}s)"
                by_model = token_stats.get("by_model") or {}
                if by_model:
                    lines.append(f"[{i}] {timestamp_str}  {tot_str}")
                    lines.append(f"    {display}")
                    for model_name, mstats in sorted(by_model.items()):
                        mi = mstats.get("input_tokens", 0)
                        mo = mstats.get("output_tokens", 0)
                        md = mstats.get("duration_seconds", 0)
                        rnd = mstats.get("rounds", 1)
                        ms = f" ↑{mi} / ↓{mo}"
                        if md and md > 0:
                            ms += f" ({md:.1f}s)"
                        rnd_str = f" ({rnd} rounds)" if rnd > 0 else ""
                        lines.append(f"      {model_name}{rnd_str}: {ms}")
                else:
                    model = token_stats.get("model_name", "unknown")
                    lines.append(f"[{i}] {timestamp_str}  {model}{tot_str}")
                    lines.append(f"    {display}")
            else:
                lines.append(f"[{i}] {timestamp_str}")
                lines.append(f"    {display}")
            lines.append("")

        return "\n".join(lines)

    def format_cost_display(self, session_id: Optional[str] = None) -> str:
        """
        Format token cost statistics for display.

        Args:
            session_id: If provided, show stats for this session only.
                       If None, show global stats.

        Returns:
            Formatted string for display.
        """
        from datetime import datetime

        stats = self.get_token_stats(session_id=session_id)

        lines = []
        lines.append(f"\n{'='*80}")
        if session_id:
            lines.append(f"Token Usage for Session: {session_id}")
        else:
            lines.append("Global Token Usage (All Sessions)")
        lines.append(f"{'='*80}\n")

        lines.append(f"Total Queries: {stats['total_queries']}")
        lines.append(f"Queries with Token Stats: {stats['records_with_stats']}")

        # Time statistics
        first_ts = stats.get("first_query_time")
        last_ts = stats.get("last_query_time")
        if first_ts and last_ts:
            first_str = datetime.fromtimestamp(first_ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
            last_str = datetime.fromtimestamp(last_ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"\nTime Range: {first_str} ~ {last_str}")
        total_dur = stats.get("total_duration_seconds", 0)
        if total_dur > 0:
            if total_dur < 60:
                lines.append(f"Total Duration: {total_dur:.1f}s")
            elif total_dur < 3600:
                lines.append(f"Total Duration: {int(total_dur // 60)}m {int(total_dur % 60)}s")
            else:
                h = int(total_dur // 3600)
                m = int((total_dur % 3600) // 60)
                lines.append(f"Total Duration: {h}h {m}m")

        lines.append(f"\nOverall Statistics:")
        lines.append(f"  Input Tokens:  {stats['input_tokens']:,}")
        lines.append(f"  Output Tokens: {stats['output_tokens']:,}")
        lines.append(f"  Total Tokens:  {stats['total_tokens']:,}")

        # Per-model statistics (no tool_calls; add duration and time range)
        by_model = stats.get("by_model", {})
        if by_model:
            lines.append(f"\nPer-Model Statistics:")
            lines.append(f"{'-'*80}")

            for model_name, model_stats in sorted(by_model.items()):
                lines.append(f"\n  Model: {model_name}")
                lines.append(f"    Queries:       {model_stats['queries']}")
                lines.append(f"    Input Tokens:  {model_stats['input_tokens']:,}")
                lines.append(f"    Output Tokens: {model_stats['output_tokens']:,}")
                lines.append(f"    Total Tokens:  {model_stats['total_tokens']:,}")
                dur = model_stats.get("total_duration_seconds", 0)
                if dur > 0:
                    lines.append(f"    Duration:      {dur:.1f}s")
                first_t = model_stats.get("first_time")
                last_t = model_stats.get("last_time")
                if first_t and last_t:
                    first_str = datetime.fromtimestamp(first_t / 1000).strftime("%Y-%m-%d %H:%M")
                    last_str = datetime.fromtimestamp(last_t / 1000).strftime("%Y-%m-%d %H:%M")
                    lines.append(f"    Time Range:    {first_str} ~ {last_str}")

        lines.append(f"\n{'='*80}\n")
        return "\n".join(lines)
