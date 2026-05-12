from __future__ import annotations

import os
import shlex
from collections import Counter
from pathlib import Path

from aworld_cli.core.command_system import CommandContext
from aworld_cli.plugin_capabilities.commands import PluginBoundCommand
from aworld_cli.memory.durable import durable_memory_file, normalize_durable_memory_type
from aworld_cli.memory.metrics import summarize_promotion_metrics
from aworld_cli.memory.discovery import discover_workspace_instruction_layers
from aworld_cli.memory.promotion import auto_promotion_enabled
from aworld_cli.memory.provider import CliDurableMemoryProvider
from aworld_cli.builtin_plugins.memory_cli.common import (
    ensure_workspace_memory_file,
    open_in_editor,
)


class MemoryCommand(PluginBoundCommand):
    @property
    def command_type(self) -> str:
        return "tool"

    @property
    def completion_items(self) -> dict[str, str]:
        return {
            "/memory clear": "Clear recalled memory artifacts for this workspace",
            "/memory view": "View effective workspace memory instructions",
            "/memory reload": "Explain current memory reload behavior",
            "/memory status": "Show workspace memory status",
        }

    async def execute(self, context: CommandContext) -> str:
        parsed = self._parse_args(context.user_args or "")
        if isinstance(parsed, str):
            return parsed

        subcommand, memory_type, scope = parsed
        if not subcommand or subcommand == "edit":
            return self._edit_workspace_memory(context)
        if subcommand == "clear":
            return self._clear_workspace_memory(context, scope=scope)
        if subcommand == "view":
            return self._view_workspace_memory(context, memory_type=memory_type)
        if subcommand == "status":
            return self._status_workspace_memory(context)
        if subcommand == "reload":
            return self._reload_workspace_memory()
        return self._usage()

    def _workspace_layers(self, context: CommandContext):
        return discover_workspace_instruction_layers(context.cwd)

    def _provider(self) -> CliDurableMemoryProvider:
        return CliDurableMemoryProvider()

    def _edit_workspace_memory(self, context: CommandContext) -> str:
        target, seeded_from, warning = ensure_workspace_memory_file(context.cwd)
        try:
            result = open_in_editor(target)
        except FileNotFoundError:
            argv0 = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
            return (
                f"Editor not found: {argv0}\n"
                "Set EDITOR or VISUAL to a valid editor command."
            )

        lines = []
        if seeded_from is not None:
            lines.append(f"Seeded from compatibility file: {seeded_from}")
        if result.returncode == 0:
            lines.append(f"Workspace memory file: {target}")
            lines.append("Edits will be picked up from disk automatically.")
        else:
            lines.append(f"Editor exited with code {result.returncode}: {target}")
        if warning:
            lines.append(warning)
        return "\n".join(lines)

    def _status_workspace_memory(self, context: CommandContext) -> str:
        layers = self._workspace_layers(context)
        durable_records = self._provider().get_durable_memory_records(context.cwd)
        durable_path = durable_memory_file(context.cwd)
        sessions_dir = Path(context.cwd).resolve() / ".aworld" / "memory" / "sessions"
        session_log_files = tuple(sorted(sessions_dir.glob("*.jsonl"))) if sessions_dir.exists() else ()
        metrics = summarize_promotion_metrics(context.cwd)
        lines = [
            "Memory instruction status",
            f"Workspace: {Path(context.cwd).resolve()}",
            f"Canonical write file: {layers.canonical_write_file}",
        ]
        if layers.global_file is not None:
            lines.append(f"Global file: {layers.global_file}")
        if layers.workspace_file is not None:
            lines.append(f"Workspace file: {layers.workspace_file}")
        if layers.compatibility_file is not None:
            lines.append(f"Compatibility file: {layers.compatibility_file}")
        if layers.effective_read_files:
            lines.append("Active read files:")
            lines.extend(f"- {path}" for path in layers.effective_read_files)
        else:
            lines.append("Active read files: none")
        lines.append(f"Durable record file: {durable_path}")
        lines.append(f"Durable record count: {len(durable_records)}")
        lines.append(f"Session log directory: {sessions_dir}")
        lines.append(f"Session log file count: {len(session_log_files)}")
        if durable_records:
            lines.append("Durable record types:")
            counts = Counter(record.memory_type for record in durable_records)
            for memory_type in sorted(counts):
                lines.append(f"- {memory_type}: {counts[memory_type]}")
        lines.append(f"Promotion metrics file: {metrics.metrics_path}")
        lines.append(f"Promotion evaluations: {metrics.total_evaluations}")
        lines.append(f"Eligible for auto-promotion: {metrics.eligible_for_auto_promotion}")
        lines.append(f"Auto-promotion enabled: {'yes' if auto_promotion_enabled() else 'no'}")
        if metrics.by_confidence:
            lines.append("Promotion confidence:")
            for confidence, count in metrics.by_confidence.items():
                lines.append(f"- {confidence}: {count}")
        if metrics.by_promotion:
            lines.append("Promotion outcomes:")
            for promotion, count in metrics.by_promotion.items():
                lines.append(f"- {promotion}: {count}")
        if metrics.by_reason:
            lines.append("Promotion reasons:")
            for reason, count in metrics.by_reason.items():
                lines.append(f"- {reason}: {count}")
        if metrics.latest_decision is not None:
            lines.append(
                "Latest promotion decision: "
                f"{metrics.latest_decision.get('promotion', 'unknown')} "
                f"({metrics.latest_decision.get('confidence', 'unknown')})"
            )
            latest_content = _one_line(metrics.latest_decision.get("content"))
            if latest_content:
                lines.append(f"Latest decision content: {latest_content}")
        if metrics.last_auto_promoted is not None:
            lines.append(
                "Last auto-promoted reason: "
                f"{metrics.last_auto_promoted.get('reason', 'unknown')}"
            )
            promoted_content = _one_line(metrics.last_auto_promoted.get("content"))
            if promoted_content:
                lines.append(f"Last auto-promoted content: {promoted_content}")
        if metrics.last_eligible_blocked is not None:
            lines.append(
                "Last eligible but blocked reason: "
                f"{metrics.last_eligible_blocked.get('reason', 'unknown')}"
            )
            blocked_content = _one_line(metrics.last_eligible_blocked.get("content"))
            if blocked_content:
                lines.append(f"Last eligible but blocked content: {blocked_content}")
        if layers.warning:
            lines.append(f"Warning: {layers.warning}")
        return "\n".join(lines)

    def _view_workspace_memory(
        self,
        context: CommandContext,
        *,
        memory_type: str | None = None,
    ) -> str:
        layers = self._workspace_layers(context)
        provider = self._provider()
        instruction = provider.get_instruction_context(context.cwd)
        durable_records = provider.get_durable_memory_records(context.cwd, memory_type=memory_type)
        if not instruction.texts and not durable_records:
            return (
                "No workspace memory instructions found.\n"
                f"Create one with /memory at: {layers.canonical_write_file}"
            )

        lines = ["Memory instruction view"]
        if instruction.warning:
            lines.append(f"Warning: {instruction.warning}")
        if instruction.texts:
            for source_file, text in zip(instruction.source_files, instruction.texts):
                lines.append(f"--- {source_file} ---")
                lines.append(text.strip())
        else:
            lines.append("No instruction files found.")
        if durable_records:
            label = f"Explicit durable memory ({memory_type})" if memory_type else "Explicit durable memory"
            lines.append(label)
            for record in durable_records:
                lines.append(f"- [{record.memory_type}] {record.content}")
        return "\n".join(lines)

    def _clear_workspace_memory(self, context: CommandContext, *, scope: str | None = None) -> str:
        workspace = Path(context.cwd).resolve()
        memory_root = workspace / ".aworld" / "memory"
        sessions_dir = memory_root / "sessions"
        durable_path = durable_memory_file(workspace)
        metrics_path = summarize_promotion_metrics(workspace).metrics_path

        effective_scope = scope or "sessions"
        cleared_session_logs = 0
        cleared_durable = False
        cleared_metrics = False

        if effective_scope in {"sessions", "all"} and sessions_dir.exists():
            for path in sessions_dir.glob("*.jsonl"):
                path.unlink(missing_ok=True)
                cleared_session_logs += 1

        if effective_scope in {"durable", "all"} and durable_path.exists():
            durable_path.unlink(missing_ok=True)
            cleared_durable = True

        if effective_scope in {"metrics", "all"} and metrics_path.exists():
            metrics_path.unlink(missing_ok=True)
            cleared_metrics = True

        lines = [f"Cleared session logs: {cleared_session_logs} file(s)"]
        if effective_scope in {"durable", "all"}:
            lines.append(f"Cleared durable records: {'yes' if cleared_durable else 'no'}")
        if effective_scope in {"metrics", "all"}:
            lines.append(f"Cleared promotion metrics: {'yes' if cleared_metrics else 'no'}")
        lines.append("Workspace instruction files were left unchanged.")
        return "\n".join(lines)

    def _parse_args(self, user_args: str) -> tuple[str, str | None, str | None] | str:
        try:
            tokens = shlex.split(user_args)
        except ValueError as exc:
            return f"Unable to parse /memory arguments: {exc}"

        if not tokens:
            return "", None, None

        subcommand = tokens[0]
        memory_type: str | None = None
        scope: str | None = None
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token == "--type":
                if index + 1 >= len(tokens):
                    return self._usage()
                try:
                    memory_type = normalize_durable_memory_type(tokens[index + 1])
                except ValueError as exc:
                    return str(exc)
                index += 2
                continue
            if token.startswith("--type="):
                try:
                    memory_type = normalize_durable_memory_type(token.split("=", 1)[1])
                except ValueError as exc:
                    return str(exc)
                index += 1
                continue
            if token == "--scope":
                if index + 1 >= len(tokens):
                    return self._usage()
                scope = self._normalize_scope(tokens[index + 1])
                if scope is None:
                    return self._usage()
                index += 2
                continue
            if token.startswith("--scope="):
                scope = self._normalize_scope(token.split("=", 1)[1])
                if scope is None:
                    return self._usage()
                index += 1
                continue
            return self._usage()

        return subcommand, memory_type, scope

    def _normalize_scope(self, value: str) -> str | None:
        normalized = str(value or "").strip().lower()
        if normalized in {"session", "sessions", "session_logs", "session-log", "session-logs"}:
            return "sessions"
        if normalized in {"durable", "durable_records", "durable-records"}:
            return "durable"
        if normalized in {"metrics", "promotion_metrics", "promotion-metrics"}:
            return "metrics"
        if normalized == "all":
            return "all"
        return None

    def _reload_workspace_memory(self) -> str:
        return (
            "Workspace memory instructions are read from disk on demand.\n"
            "No manual reload or agent restart is required."
        )

    def _usage(self) -> str:
        return (
            "Usage: /memory [view|status|reload|clear] [--type <memory-type>] [--scope sessions|durable|metrics|all]\n"
            "Default clear scope is session logs only.\n"
            "Default action opens the workspace AWORLD.md file in your editor."
        )


def build_command(plugin, entrypoint):
    return MemoryCommand(plugin, entrypoint)


def _one_line(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    return text or None
