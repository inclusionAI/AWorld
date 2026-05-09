from __future__ import annotations

import os
import shlex
from collections import Counter
from pathlib import Path

from aworld_cli.core.command_system import CommandContext
from aworld_cli.plugin_capabilities.commands import PluginBoundCommand
from aworld_cli.memory.durable import (
    durable_memory_file,
    normalize_durable_memory_type,
    normalize_memory_kind,
)
from aworld_cli.memory.cache_observability import (
    format_cache_observability_summary,
    summarize_cache_observability,
)
from aworld_cli.memory.metrics import summarize_promotion_metrics
from aworld_cli.memory.discovery import discover_workspace_instruction_layers
from aworld_cli.memory.governance import governance_mode
from aworld_cli.memory.promotion import auto_promotion_enabled
from aworld_cli.memory.provider import CliDurableMemoryProvider
from aworld_cli.builtin_plugins.memory_cli.common import (
    ensure_workspace_memory_file,
    open_in_editor,
    remove_remembered_guidance,
)


class MemoryCommand(PluginBoundCommand):
    @property
    def command_type(self) -> str:
        return "tool"

    @property
    def completion_items(self) -> dict[str, str]:
        return {
            "/memory view": "View effective workspace memory instructions",
            "/memory reload": "Explain current memory reload behavior",
            "/memory status": "Show workspace memory status",
            "/memory cache": "Summarize request-linked cache observability from session logs",
            "/memory promotions": "List governed promotion decisions",
            "/memory promotions accept <decision-id>": "Confirm a reviewed promotion decision",
            "/memory promotions reject <decision-id>": "Record a declined review label",
            "/memory promotions revert <decision-id>": "Disable a previously promoted governed record",
        }

    async def execute(self, context: CommandContext) -> str:
        parsed = self._parse_args(context.user_args or "")
        if isinstance(parsed, str):
            return parsed

        subcommand, memory_type, positional_args = parsed
        if not subcommand or subcommand == "edit":
            if positional_args:
                return self._usage()
            return self._edit_workspace_memory(context)
        if subcommand == "view":
            if positional_args:
                return self._usage()
            return self._view_workspace_memory(context, memory_type=memory_type)
        if subcommand == "status":
            if positional_args:
                return self._usage()
            return self._status_workspace_memory(context)
        if subcommand == "cache":
            if positional_args:
                return self._usage()
            return self._cache_workspace_memory(context)
        if subcommand == "reload":
            if positional_args:
                return self._usage()
            return self._reload_workspace_memory()
        if subcommand == "promotions":
            if memory_type is not None:
                return self._usage()
            if not positional_args:
                return self._promotions_workspace_memory(context)
            if len(positional_args) != 2:
                return self._usage()
            action, decision_id = positional_args
            return self._promotions_workspace_memory(
                context,
                action=action,
                decision_id=decision_id,
            )
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
        durable_records = self._provider().get_active_durable_memory_records(context.cwd)
        durable_path = durable_memory_file(context.cwd)
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
        if durable_records:
            lines.append("Durable record types:")
            counts = Counter(record.memory_type for record in durable_records)
            for memory_type in sorted(counts):
                lines.append(f"- {memory_type}: {counts[memory_type]}")
            lines.append("Durable record kinds:")
            kind_counts = Counter(_memory_kind_label(record.memory_kind) for record in durable_records)
            for memory_kind in sorted(kind_counts):
                lines.append(f"- {memory_kind}: {kind_counts[memory_kind]}")
        lines.append(f"Promotion metrics file: {metrics.metrics_path}")
        lines.append(f"Promotion evaluations: {metrics.total_evaluations}")
        lines.append(f"Eligible for auto-promotion: {metrics.eligible_for_auto_promotion}")
        lines.append(f"Governance mode: {governance_mode()}")
        lines.append(
            "Governed default rollout ready: "
            f"{'yes' if metrics.default_rollout_ready else 'no'}"
        )
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
        durable_records = provider.get_active_durable_memory_records(
            context.cwd,
            memory_type=memory_type,
        )
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
                lines.append(
                    f"- [{record.memory_type}] {record.content} "
                    f"(kind={_memory_kind_label(record.memory_kind)})"
                )
        return "\n".join(lines)

    def _cache_workspace_memory(self, context: CommandContext) -> str:
        summary = summarize_cache_observability(context.cwd)
        return format_cache_observability_summary(summary)

    def _promotions_workspace_memory(
        self,
        context: CommandContext,
        *,
        action: str | None = None,
        decision_id: str | None = None,
    ) -> str:
        provider = self._provider()
        if action is not None or decision_id is not None:
            review_actions = {
                "accept": "confirmed",
                "reject": "declined",
                "revert": "reverted",
            }
            normalized_action = (action or "").strip().lower()
            normalized_decision_id = (decision_id or "").strip()
            review_action = review_actions.get(normalized_action)
            if review_action is None or not normalized_decision_id:
                return self._usage()
            decisions = provider.list_governed_decisions(context.cwd)
            decision = _find_governed_decision(decisions, normalized_decision_id)
            if decision is None:
                raise ValueError(f"Unknown governed decision: {normalized_decision_id}")
            if normalized_action == "accept":
                if not _is_accept_reviewable_decision(decision):
                    raise ValueError(
                        f"Governed decision {normalized_decision_id} is not reviewable for acceptance"
                    )
                content = str(decision.get("content") or "").strip()
                if not content:
                    raise ValueError(
                        f"Governed decision {normalized_decision_id} has no content to promote"
                    )
                memory_type = str(decision.get("memory_type") or "").strip()
                memory_kind = decision.get("memory_kind")
                source_ref = decision.get("source_ref")
                promoted = provider.append_durable_memory_record(
                    context.cwd,
                    text=content,
                    memory_type=memory_type,
                    source="governed_auto_promotion",
                    memory_kind=memory_kind if isinstance(memory_kind, str) else None,
                    decision_id=normalized_decision_id,
                    source_ref=source_ref if isinstance(source_ref, dict) else None,
                )
                provider.record_governed_review(
                    context.cwd,
                    decision_id=normalized_decision_id,
                    review_action=review_action,
                )
                lines = [
                    f"Recorded review action: {review_action} for {normalized_decision_id}",
                    f"Promoted to durable memory: {promoted.memory_type}",
                ]
                if promoted.record_created:
                    lines.append(f"Durable record file: {promoted.record_path}")
                else:
                    lines.append("Durable memory already contained this content.")
                return "\n".join(lines)
            provider.record_governed_review(
                context.cwd,
                decision_id=normalized_decision_id,
                review_action=review_action,
            )
            if normalized_action == "revert":
                content = _one_line(decision.get("content")) if isinstance(decision, dict) else None
                if content is not None:
                    active_records = provider.get_active_durable_memory_records(context.cwd)
                    if not any(record.content == content for record in active_records):
                        remove_remembered_guidance(context.cwd, content)
            return (
                f"Recorded review action: {review_action} "
                f"for {normalized_decision_id}"
            )

        decisions = provider.list_governed_decisions(context.cwd)
        lines = ["Governed promotions"]
        if not decisions:
            lines.append("No governed promotion decisions found.")
            return "\n".join(lines)

        for item in decisions[-10:]:
            review_summary = _review_summary(item.get("reviews"))
            lines.append(
                "  ".join(
                    (
                        f"decision_id={item.get('decision_id', 'unknown')}",
                        f"policy_mode={item.get('policy_mode', 'unknown')}",
                        f"policy_version={item.get('policy_version', 'unknown')}",
                        f"decision={item.get('decision', 'unknown')}",
                        f"reason={item.get('reason', 'unknown')}",
                        f"confidence={item.get('confidence', 'unknown')}",
                        f"memory_kind={_memory_kind_label(item.get('memory_kind'))}",
                    )
                )
            )
            source_ref = _source_ref_summary(item.get("source_ref"))
            lines.append(f"  source_ref={source_ref}")
            blockers = _blockers_summary(item.get("blockers"))
            if blockers is not None:
                lines.append(f"  blockers={blockers}")
            lines.append(f"  reviews={review_summary}")
            content = _one_line(item.get("content"))
            if content:
                lines.append(f"  content={content}")
        return "\n".join(lines)

    def _parse_args(
        self,
        user_args: str,
    ) -> tuple[str, str | None, tuple[str, ...]] | str:
        try:
            tokens = shlex.split(user_args)
        except ValueError as exc:
            return f"Unable to parse /memory arguments: {exc}"

        if not tokens:
            return "", None, ()

        subcommand = tokens[0]
        memory_type: str | None = None
        positional_args: list[str] = []
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
            positional_args.append(token)
            index += 1

        return subcommand, memory_type, tuple(positional_args)

    def _reload_workspace_memory(self) -> str:
        return (
            "Workspace memory instructions are read from disk on demand.\n"
            "No manual reload or agent restart is required."
        )

    def _usage(self) -> str:
        return (
            "Usage: /memory [view|status|cache|reload|promotions] [--type <memory-type>]\n"
            "       /memory promotions [accept|reject|revert] <decision-id>\n"
            "Default action opens the workspace AWORLD.md file in your editor."
        )


def build_command(plugin, entrypoint):
    return MemoryCommand(plugin, entrypoint)


def _one_line(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    return text or None


def _review_summary(value) -> str:
    if not isinstance(value, list) or not value:
        return "none"
    actions = [
        str(item.get("review_action")).strip()
        for item in value
        if isinstance(item, dict) and str(item.get("review_action") or "").strip()
    ]
    if not actions:
        return "none"
    return ",".join(actions)


def _source_ref_summary(value) -> str:
    if not isinstance(value, dict) or not value:
        return "none"
    ordered_keys = ("session_id", "task_id", "candidate_id")
    parts: list[str] = []
    for key in ordered_keys:
        if key in value:
            parts.append(f"{key}={value[key]}")
    for key in sorted(value):
        if key not in ordered_keys:
            parts.append(f"{key}={value[key]}")
    return ", ".join(parts) if parts else "none"


def _blockers_summary(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, list):
        text = str(value).strip()
        return text or None
    blockers = [str(item).strip() for item in value if str(item).strip()]
    return ",".join(blockers) if blockers else None


def _memory_kind_label(value: object) -> str:
    try:
        normalized_kind = normalize_memory_kind(value)
    except ValueError:
        normalized_kind = None
    return normalized_kind or "legacy_untyped"


def _find_governed_decision(decisions: tuple[dict, ...], decision_id: str) -> dict | None:
    return next((item for item in decisions if item.get("decision_id") == decision_id), None)


def _is_accept_reviewable_decision(decision: dict) -> bool:
    if str(decision.get("decision") or "").strip().lower() != "session_log_only":
        return False
    blockers = decision.get("blockers")
    if isinstance(blockers, (list, tuple)) and any(str(blocker).strip() for blocker in blockers):
        return False
    return bool(str(decision.get("content") or "").strip())
