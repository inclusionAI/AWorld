# -*- coding: utf-8 -*-
"""
Digest logger statistics parser for batch job monitoring.

Parses digest_logger.log format and aggregates metrics for:
- agent_run: Agent execution time per agent
- run_task: Task success/failure rate and duration
- llm_call: Model call tokens and duration

Example:
    >>> stats = DigestLogStats.parse_file("/path/to/digest_logger.log")
    >>> print(stats.success_rate)
    95.5
    >>> print(stats.run_task_summary)
"""
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Set, Tuple


@dataclass
class AgentRunStats:
    """Statistics for agent_run metric."""

    count: int = 0
    total_duration_sec: float = 0.0
    durations: List[float] = field(default_factory=list)
    by_agent: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))


@dataclass
class RunTaskStats:
    """Statistics for run_task metric."""

    success_count: int = 0
    failed_count: int = 0
    timeout_count: int = 0
    total_duration_sec: float = 0.0
    durations: List[float] = field(default_factory=list)
    by_agent: Dict[str, Dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: {"success": 0, "failed": 0, "timeout": 0})
    )
    errors: List[str] = field(default_factory=list)


@dataclass
class LlmCallStats:
    """Statistics for llm_call metric."""

    count: int = 0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_duration_sec: float = 0.0
    by_model: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: defaultdict(
            lambda: {"calls": 0, "tokens": 0, "duration": 0.0}
        )
    )
    by_agent: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: defaultdict(
            lambda: {"calls": 0, "tokens": 0, "duration": 0.0}
        )
    )


@dataclass
class DigestLogStats:
    """
    Aggregated statistics from digest_logger.log.

    Attributes:
        agent_run: Agent run statistics
        run_task: Task execution statistics
        llm_call: LLM call statistics
        success_rate: Task success rate (0-100)
    """

    agent_run: AgentRunStats = field(default_factory=AgentRunStats)
    run_task: RunTaskStats = field(default_factory=RunTaskStats)
    llm_call: LlmCallStats = field(default_factory=LlmCallStats)

    @property
    def success_rate(self) -> float:
        """Calculate task success rate percentage."""
        total = (
            self.run_task.success_count
            + self.run_task.failed_count
            + self.run_task.timeout_count
        )
        if total == 0:
            return 0.0
        return round(100.0 * self.run_task.success_count / total, 2)

    @property
    def total_tasks(self) -> int:
        """Total number of run_task entries."""
        return (
            self.run_task.success_count
            + self.run_task.failed_count
            + self.run_task.timeout_count
        )

    @classmethod
    def _extract_metric_line(cls, line: str) -> Optional[str]:
        """
        Extract metric payload from digest log line.

        Digest format: timestamp|digest|trace_id|level{message}
        Message contains: metric_type|field1|field2|...

        Example:
            >>> cls._extract_metric_line("2026-01-26 13:28:07.078| digest | abc|INFO llm_call|agent|model|...")
            "llm_call|agent|model|..."
        """
        line = line.strip()
        if not line:
            return None
        # Match metric type prefix in message
        for prefix in ("agent_run|", "run_task|", "llm_call|"):
            idx = line.find(prefix)
            if idx >= 0:
                return line[idx:]
        return None

    @classmethod
    def _parse_agent_run(cls, parts: List[str]) -> Optional[Dict[str, Any]]:
        """Parse agent_run metric: agent_run|agent_id|user|session_id|task_id|duration."""
        if len(parts) < 6:
            return None
        try:
            duration = float(parts[5])
            return {
                "agent_id": parts[1],
                "user": parts[2],
                "session_id": parts[3],
                "task_id": parts[4],
                "duration": duration,
            }
        except (ValueError, IndexError):
            return None

    @classmethod
    def _parse_run_task(cls, parts: List[str]) -> Optional[Dict[str, Any]]:
        """
        Parse run_task metric.
        run_task|stream|agent_id|user_id|session_id|task_id|status|duration|error?
        """
        if len(parts) < 8:
            return None
        try:
            duration = int(parts[7]) if parts[7].lstrip("-").isdigit() else 0
            result = {
                "stream_mode": parts[1],
                "agent_id": parts[2],
                "user_id": parts[3],
                "session_id": parts[4],
                "task_id": parts[5],
                "status": parts[6],
                "duration": duration,
                "error": parts[8] if len(parts) > 8 else None,
            }
            return result
        except (ValueError, IndexError):
            return None

    @classmethod
    def _parse_llm_call(cls, parts: List[str]) -> Optional[Dict[str, Any]]:
        """
        Parse llm_call metric.
        llm_call|agent_id|model|user|session_id|task_id|total_tokens|prompt_tokens|completion_tokens|duration
        """
        if len(parts) < 10:
            return None
        try:
            total_tokens = int(parts[6])
            prompt_tokens = int(parts[7])
            completion_tokens = int(parts[8])
            duration = float(parts[9])
            return {
                "agent_id": parts[1],
                "model": parts[2],
                "user": parts[3],
                "session_id": parts[4],
                "task_id": parts[5],
                "total_tokens": total_tokens,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "duration": duration,
            }
        except (ValueError, IndexError):
            return None

    @classmethod
    def _task_id_matches(
        cls,
        parsed: Dict[str, Any],
        task_id_key: str,
        task_ids: Optional[Set[str]],
    ) -> bool:
        """Return True if task_ids is None (no filter) or parsed task_id in set."""
        if task_ids is None:
            return True
        tid = parsed.get(task_id_key)
        return tid in task_ids if tid else False

    @classmethod
    def parse_lines(
        cls,
        lines: List[str],
        task_ids: Optional[Set[str]] = None,
    ) -> "DigestLogStats":
        """
        Parse digest log lines and aggregate statistics.

        Args:
            lines: Raw log lines (may include non-metric lines)
            task_ids: Optional set of task_ids to filter (only include matching entries).
                     When None, include all entries (no filter).

        Returns:
            DigestLogStats with aggregated metrics

        Example:
            >>> stats = DigestLogStats.parse_lines(lines)
            >>> stats = DigestLogStats.parse_lines(lines, task_ids={"batch_0_abc123"})
        """
        stats = cls()
        for line in lines:
            metric_line = cls._extract_metric_line(line)
            if not metric_line:
                continue
            parts = metric_line.split("|")
            if not parts:
                continue
            metric_type = parts[0]
            if metric_type == "agent_run":
                parsed = cls._parse_agent_run(parts)
                if parsed and cls._task_id_matches(parsed, "task_id", task_ids):
                    stats.agent_run.count += 1
                    stats.agent_run.total_duration_sec += parsed["duration"]
                    stats.agent_run.durations.append(parsed["duration"])
                    stats.agent_run.by_agent[parsed["agent_id"]].append(
                        parsed["duration"]
                    )
            elif metric_type == "run_task":
                parsed = cls._parse_run_task(parts)
                if parsed and cls._task_id_matches(parsed, "task_id", task_ids):
                    status = parsed["status"]
                    if status == "success":
                        stats.run_task.success_count += 1
                    elif status == "failed":
                        stats.run_task.failed_count += 1
                        if parsed.get("error"):
                            stats.run_task.errors.append(parsed["error"][:200])
                    elif status == "timeout":
                        stats.run_task.timeout_count += 1
                    stats.run_task.total_duration_sec += parsed["duration"]
                    stats.run_task.durations.append(parsed["duration"])
                    agent_id = parsed["agent_id"]
                    if status == "success":
                        stats.run_task.by_agent[agent_id]["success"] += 1
                    elif status == "failed":
                        stats.run_task.by_agent[agent_id]["failed"] += 1
                    else:
                        stats.run_task.by_agent[agent_id]["timeout"] += 1
            elif metric_type == "llm_call":
                parsed = cls._parse_llm_call(parts)
                if parsed and cls._task_id_matches(parsed, "task_id", task_ids):
                    stats.llm_call.count += 1
                    stats.llm_call.total_tokens += parsed["total_tokens"]
                    stats.llm_call.prompt_tokens += parsed["prompt_tokens"]
                    stats.llm_call.completion_tokens += parsed["completion_tokens"]
                    stats.llm_call.total_duration_sec += parsed["duration"]
                    model = parsed["model"]
                    stats.llm_call.by_model[model]["calls"] += 1
                    stats.llm_call.by_model[model]["tokens"] += parsed[
                        "total_tokens"
                    ]
                    stats.llm_call.by_model[model]["duration"] += parsed[
                        "duration"
                    ]
                    agent_id = parsed["agent_id"]
                    stats.llm_call.by_agent[agent_id]["calls"] += 1
                    stats.llm_call.by_agent[agent_id]["tokens"] += parsed[
                        "total_tokens"
                    ]
                    stats.llm_call.by_agent[agent_id]["duration"] += parsed[
                        "duration"
                    ]
        return stats

    @classmethod
    def parse_file(
        cls,
        path: str,
        from_position: Optional[int] = 0,
        task_ids: Optional[Set[str]] = None,
    ) -> Tuple["DigestLogStats", int]:
        """
        Parse digest log file, optionally from a byte position.

        Args:
            path: Path to digest_logger.log
            from_position: Byte offset to start reading (for incremental reads)
            task_ids: Optional set of task_ids to filter (only include matching entries)

        Returns:
            Tuple of (DigestLogStats, bytes_read)

        Example:
            >>> stats, _ = DigestLogStats.parse_file("logs/digest_logger.log")
            >>> stats, _ = DigestLogStats.parse_file(path, task_ids={"batch_0_abc123"})
        """
        path_obj = Path(path)
        if not path_obj.exists():
            return cls(), 0
        try:
            with open(path_obj, "r", encoding="utf-8", errors="ignore") as f:
                if from_position > 0:
                    f.seek(from_position)
                content = f.read()
                lines = content.splitlines()
                stats = cls.parse_lines(lines, task_ids=task_ids)
                return stats, len(content.encode("utf-8"))
        except OSError:
            return cls(), 0

    def format_summary(
        self, filtered_by_task_id: bool = False
    ) -> str:
        """Format statistics as human-readable summary string."""
        lines = []
        title = "📊 Digest Logger 统计"
        if filtered_by_task_id:
            title += " (本批次)"
        lines.append(title)
        lines.append("=" * 50)
        # run_task
        lines.append("\n📋 run_task (任务执行)")
        lines.append(
            f"  总任务数: {self.total_tasks} | "
            f"成功: {self.run_task.success_count} | "
            f"失败: {self.run_task.failed_count} | "
            f"超时: {self.run_task.timeout_count}"
        )
        lines.append(f"  成功率: {self.success_rate}%")
        if self.run_task.durations:
            avg_d = sum(self.run_task.durations) / len(self.run_task.durations)
            lines.append(f"  平均耗时: {avg_d:.1f}s")
            lines.append(
                f"  总耗时: {self.run_task.total_duration_sec:.1f}s"
            )
        # agent_run
        lines.append("\n🤖 agent_run (Agent 执行)")
        lines.append(f"  总调用次数: {self.agent_run.count}")
        if self.agent_run.durations:
            avg_d = (
                sum(self.agent_run.durations) / len(self.agent_run.durations)
            )
            lines.append(f"  平均耗时: {avg_d:.2f}s")
            lines.append(
                f"  总耗时: {self.agent_run.total_duration_sec:.1f}s"
            )
        if self.agent_run.by_agent:
            lines.append("  按 Agent:")
            for agent, durs in sorted(
                self.agent_run.by_agent.items(),
                key=lambda x: -sum(x[1]),
            )[:5]:
                avg = sum(durs) / len(durs) if durs else 0
                lines.append(f"    - {agent}: {len(durs)} 次, 平均 {avg:.2f}s")
        # llm_call
        lines.append("\n🔮 llm_call (模型调用)")
        lines.append(f"  总调用次数: {self.llm_call.count}")
        lines.append(
            f"  Token: 总计 {self.llm_call.total_tokens} "
            f"(prompt: {self.llm_call.prompt_tokens}, "
            f"completion: {self.llm_call.completion_tokens})"
        )
        if self.llm_call.total_duration_sec > 0:
            lines.append(f"  总耗时: {self.llm_call.total_duration_sec:.1f}s")
        if self.llm_call.by_model:
            lines.append("  按模型:")
            for model, data in sorted(
                self.llm_call.by_model.items(),
                key=lambda x: -x[1]["tokens"],
            )[:5]:
                lines.append(
                    f"    - {model}: {data['calls']} 次, "
                    f"{data['tokens']} tokens"
                )
        return "\n".join(lines)
