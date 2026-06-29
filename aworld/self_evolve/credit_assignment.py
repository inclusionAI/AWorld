from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from aworld.self_evolve.provenance import TargetProvenance
from aworld.self_evolve.trace_pack import TraceEvidenceStep, TracePack
from aworld.self_evolve.types import SelfEvolveTargetRef


@dataclass(frozen=True)
class TargetInventoryEntry:
    target: SelfEvolveTargetRef
    provenance: TargetProvenance
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetInventory:
    entries: tuple[TargetInventoryEntry, ...]

    def find(self, target_type: str, target_id: str) -> TargetInventoryEntry | None:
        for entry in self.entries:
            if entry.target.target_type == target_type and entry.target.target_id == target_id:
                return entry
        return None


@dataclass(frozen=True)
class TargetSelectionReport:
    selected_target: SelfEvolveTargetRef | None
    confidence: float
    evidence_step_ids: tuple[str, ...]
    failure_category: str
    signals: tuple[str, ...] = ()
    no_target_reason: str | None = None
    diagnostics: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class LLMTargetDiagnosis:
    target_type: str
    target_id: str | None
    confidence: float
    evidence_step_ids: tuple[str, ...]
    failure_category: str
    rationale: str


LLMTargetDiagnoser = Callable[[TracePack, TargetInventory], LLMTargetDiagnosis | None]


_WEAK_SKILL_ALIAS_TOKENS = {
    "agent",
    "assistant",
    "audio",
    "content",
    "document",
    "documents",
    "extension",
    "files",
    "image",
    "images",
    "media",
    "skill",
    "specialized",
    "video",
}


class TrajectoryCreditAssigner:
    def __init__(
        self,
        inventory: TargetInventory,
        *,
        confidence_threshold: float = 0.8,
        llm_diagnoser: LLMTargetDiagnoser | None = None,
    ) -> None:
        self.inventory = inventory
        self.confidence_threshold = confidence_threshold
        self.llm_diagnoser = llm_diagnoser

    def assign(self, trace_pack: TracePack) -> TargetSelectionReport:
        serialized = _pack_text(trace_pack)
        signal = _deterministic_signal(serialized)
        structured_signals = _extract_structured_signals(trace_pack)
        signals = _dedupe(signal.signals + structured_signals)
        evidence_ids = _matching_evidence_ids(trace_pack, signal.keywords)

        if signal.target_type == "no_target":
            llm_report = self._assign_from_llm(trace_pack, signals)
            if llm_report is not None:
                return llm_report
            skill_report = self._assign_from_skill_inventory(
                trace_pack,
                serialized=serialized,
                existing_signals=signals,
            )
            if skill_report is not None:
                return skill_report
            return TargetSelectionReport(
                selected_target=None,
                confidence=signal.confidence,
                evidence_step_ids=evidence_ids,
                failure_category="no_target",
                signals=signals,
                no_target_reason=signal.reason,
                diagnostics={"pack_id": trace_pack.pack_id},
            )

        entry = self.inventory.find(signal.target_type, signal.target_id)
        if entry is None:
            return TargetSelectionReport(
                selected_target=None,
                confidence=0.0,
                evidence_step_ids=evidence_ids,
                failure_category="no_target",
                signals=signals,
                no_target_reason=(
                    f"deterministic signal selected {signal.target_type}:{signal.target_id}, "
                    "but the target is not present in inventory"
                ),
                diagnostics={"pack_id": trace_pack.pack_id},
            )

        return TargetSelectionReport(
            selected_target=entry.target,
            confidence=signal.confidence,
            evidence_step_ids=evidence_ids,
            failure_category=signal.failure_category,
            signals=signals,
            diagnostics={"pack_id": trace_pack.pack_id},
        )

    def _assign_from_skill_inventory(
        self,
        trace_pack: TracePack,
        *,
        serialized: str,
        existing_signals: tuple[str, ...],
    ) -> TargetSelectionReport | None:
        matches: list[tuple[int, TargetInventoryEntry, tuple[str, ...]]] = []
        for entry in self.inventory.entries:
            if entry.target.target_type != "skill" or entry.provenance.protected:
                continue
            aliases = _skill_match_aliases(entry)
            matched_aliases = tuple(alias for alias in aliases if alias in serialized)
            if matched_aliases:
                matches.append((len(matched_aliases), entry, matched_aliases))

        if not matches:
            return None

        _score, entry, matched_aliases = max(
            matches,
            key=lambda item: (item[0], len(item[1].target.target_id)),
        )
        evidence_ids = _matching_evidence_ids(trace_pack, matched_aliases)
        if not evidence_ids:
            evidence_ids = tuple(step.evidence_id for step in trace_pack.steps)
        signals = _dedupe(
            existing_signals + (f"skill_alias_match:{entry.target.target_id}",)
        )
        return TargetSelectionReport(
            selected_target=entry.target,
            confidence=0.85,
            evidence_step_ids=evidence_ids,
            failure_category="skill",
            signals=signals,
            diagnostics={
                "pack_id": trace_pack.pack_id,
                "matched_aliases": matched_aliases,
            },
        )

    def _assign_from_llm(
        self,
        trace_pack: TracePack,
        existing_signals: tuple[str, ...],
    ) -> TargetSelectionReport | None:
        if self.llm_diagnoser is None:
            return None

        diagnosis = self.llm_diagnoser(trace_pack, self.inventory)
        if diagnosis is None:
            return None

        signals = _dedupe(existing_signals + ("llm_assisted_diagnosis",))
        diagnostics = {
            "pack_id": trace_pack.pack_id,
            "llm_rationale": diagnosis.rationale,
        }
        invalid_evidence = _invalid_evidence_ids(trace_pack, diagnosis.evidence_step_ids)
        if invalid_evidence:
            return TargetSelectionReport(
                selected_target=None,
                confidence=0.0,
                evidence_step_ids=diagnosis.evidence_step_ids,
                failure_category="no_target",
                signals=signals,
                no_target_reason="llm diagnosis cited evidence outside the trace pack",
                diagnostics={**diagnostics, "invalid_evidence_step_ids": invalid_evidence},
            )

        if diagnosis.confidence < self.confidence_threshold:
            return TargetSelectionReport(
                selected_target=None,
                confidence=diagnosis.confidence,
                evidence_step_ids=diagnosis.evidence_step_ids,
                failure_category="no_target",
                signals=signals,
                no_target_reason="llm diagnosis confidence is below policy",
                diagnostics=diagnostics,
            )

        if diagnosis.target_type == "no_target" or diagnosis.target_id is None:
            return TargetSelectionReport(
                selected_target=None,
                confidence=diagnosis.confidence,
                evidence_step_ids=diagnosis.evidence_step_ids,
                failure_category="no_target",
                signals=signals,
                no_target_reason=diagnosis.rationale or "llm diagnosis returned no_target",
                diagnostics=diagnostics,
            )

        entry = self.inventory.find(diagnosis.target_type, diagnosis.target_id)
        if entry is None:
            return TargetSelectionReport(
                selected_target=None,
                confidence=0.0,
                evidence_step_ids=diagnosis.evidence_step_ids,
                failure_category="no_target",
                signals=signals,
                no_target_reason=(
                    f"llm diagnosis selected {diagnosis.target_type}:{diagnosis.target_id}, "
                    "but the target is not present in inventory"
                ),
                diagnostics=diagnostics,
            )

        return TargetSelectionReport(
            selected_target=entry.target,
            confidence=diagnosis.confidence,
            evidence_step_ids=diagnosis.evidence_step_ids,
            failure_category=diagnosis.failure_category,
            signals=signals,
            diagnostics=diagnostics,
        )


def build_default_target_inventory(workspace_root: str | Path) -> TargetInventory:
    root = Path(workspace_root)
    entries: list[TargetInventoryEntry] = []
    seen: set[tuple[str, str]] = set()

    def add(entry: TargetInventoryEntry) -> None:
        key = (entry.target.target_type, entry.target.target_id)
        if key in seen:
            return
        entries.append(entry)
        seen.add(key)

    for entry in _skill_entries_from_workspace(root):
        add(entry)

    add(
        _entry(
            target_type="prompt-section",
            target_id="result-validation-anchor-policy",
            path=None,
            source_kind="prompt",
            write_origin="framework_prompt",
            trust_level="framework",
            protected=False,
            reason="Result validation policy can be proposed as a prompt-section target.",
            aliases=("result validation mismatch", "anchors"),
        )
    )
    add(
        _entry(
            target_type="tool-description",
            target_id="SKILL_tool.active_skill",
            path=None,
            source_kind="tool_schema",
            write_origin="framework_tool_description",
            trust_level="framework",
            protected=False,
            reason="Skill activation tool description can be proposed for clarity improvements.",
            aliases=("skill_tool", "active_skill"),
        )
    )
    add(
        _entry(
            target_type="workspace-artifact",
            target_id="btc_monitor.sh",
            path=str(root / "btc_monitor.sh"),
            source_kind="workspace_artifact",
            write_origin="agent_generated_artifact",
            trust_level="generated",
            protected=False,
            reason="Agent-produced workspace artifacts may be proposal-only targets.",
            aliases=("btc_monitor", "api sources timed out"),
        )
    )
    return TargetInventory(entries=tuple(entries))


@dataclass(frozen=True)
class _Signal:
    target_type: str
    target_id: str
    failure_category: str
    confidence: float
    signals: tuple[str, ...]
    keywords: tuple[str, ...]
    reason: str | None = None


def _entry(
    *,
    target_type: str,
    target_id: str,
    path: str | None,
    source_kind: str,
    write_origin: str,
    trust_level: str,
    protected: bool,
    reason: str,
    aliases: Iterable[str],
) -> TargetInventoryEntry:
    target = SelfEvolveTargetRef(target_type=target_type, target_id=target_id, path=path)
    return TargetInventoryEntry(
        target=target,
        provenance=TargetProvenance(
            target=target,
            source_kind=source_kind,
            write_origin=write_origin,
            trust_level=trust_level,
            protected=protected,
            reason=reason,
        ),
        aliases=tuple(aliases),
    )


def _deterministic_signal(serialized: str) -> _Signal:
    if "result validation mismatch" in serialized or "anchors" in serialized:
        return _Signal(
            target_type="prompt-section",
            target_id="result-validation-anchor-policy",
            failure_category="validation",
            confidence=0.9,
            signals=("result_validation_mismatch",),
            keywords=("result validation mismatch", "anchors"),
        )
    if "skill_tool" in serialized or "active_skill" in serialized:
        return _Signal(
            target_type="tool-description",
            target_id="SKILL_tool.active_skill",
            failure_category="tool_activation",
            confidence=0.9,
            signals=("skill_tool_activation",),
            keywords=("skill_tool", "active_skill"),
        )
    if "btc_monitor" in serialized or "btc price monitor" in serialized or "api sources timed out" in serialized:
        return _Signal(
            target_type="workspace-artifact",
            target_id="btc_monitor.sh",
            failure_category="artifact_failure",
            confidence=0.9,
            signals=("generated_artifact_failure",),
            keywords=("btc_monitor", "btc price monitor", "api sources timed out"),
        )
    return _Signal(
        target_type="no_target",
        target_id="deterministic_success_or_low_confidence",
        failure_category="no_target",
        confidence=0.2,
        signals=("low_confidence",),
        keywords=(),
        reason="deterministic signals did not identify a supported self-evolve target",
    )


def _skill_entries_from_workspace(root: Path) -> tuple[TargetInventoryEntry, ...]:
    entries: list[TargetInventoryEntry] = []
    for base in (root / "aworld-skills", root / "skills"):
        if not base.exists():
            continue
        for skill_path in sorted(base.glob("*/SKILL.md")):
            target_id = skill_path.parent.name
            frontmatter = _skill_frontmatter(skill_path)
            name = frontmatter.get("name") or target_id
            description = frontmatter.get("description", "")
            aliases = tuple(
                item
                for item in (target_id, name, description)
                if isinstance(item, str) and item.strip()
            )
            entries.append(
                _entry(
                    target_type="skill",
                    target_id=target_id,
                    path=str(skill_path),
                    source_kind="skill",
                    write_origin="installed_skill",
                    trust_level="local",
                    protected=target_id in {"app_evaluator", "self_evolve"},
                    reason="Installed skill text can be proposed as a self-evolve target.",
                    aliases=aliases,
                )
            )
    return tuple(entries)


def _skill_match_aliases(entry: TargetInventoryEntry) -> tuple[str, ...]:
    aliases = [entry.target.target_id]
    aliases.extend(alias for alias in entry.aliases[:2] if alias)
    normalized: list[str] = []
    for alias in aliases:
        lowered = alias.strip().lower()
        if not lowered:
            continue
        normalized.append(lowered)
        normalized.extend(
            token
            for token in re.findall(r"[a-z0-9]+", lowered)
            if len(token) >= 5 and token not in _WEAK_SKILL_ALIAS_TOKENS
        )
    return tuple(dict.fromkeys(normalized))


def _skill_frontmatter(path: Path) -> dict[str, str]:
    content = path.read_text(encoding="utf-8", errors="replace")
    if not content.startswith("---\n"):
        return {}
    end = content.find("\n---", 4)
    if end < 0:
        return {}
    values: dict[str, str] = {}
    for line in content[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _pack_text(trace_pack: TracePack) -> str:
    payload = {
        "task_id": trace_pack.task_id,
        "steps": [
            _target_evidence_payload(step)
            for step in trace_pack.steps
        ],
        "compression_summary": trace_pack.compression_summary,
    }
    return json.dumps(payload, ensure_ascii=False).lower()


def _matching_evidence_ids(trace_pack: TracePack, keywords: tuple[str, ...]) -> tuple[str, ...]:
    if not keywords:
        return tuple(step.evidence_id for step in trace_pack.steps[:1])

    matches: list[str] = []
    for step in trace_pack.steps:
        step_text = _step_text(step)
        if any(keyword in step_text for keyword in keywords):
            matches.append(step.evidence_id)
    if matches:
        return tuple(matches)
    return tuple(step.evidence_id for step in trace_pack.steps[-2:])


def _invalid_evidence_ids(
    trace_pack: TracePack,
    evidence_step_ids: tuple[str, ...],
) -> tuple[str, ...]:
    available_ids = {step.evidence_id for step in trace_pack.steps}
    return tuple(
        evidence_id
        for evidence_id in evidence_step_ids
        if evidence_id not in available_ids
    )


def _extract_structured_signals(trace_pack: TracePack) -> tuple[str, ...]:
    signals: list[str] = []
    tool_counts: dict[str, int] = {}
    for step in trace_pack.steps:
        for tool_name in step.tool_names:
            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
            if _reward_failed(step.reward):
                signals.append(f"tool_call_failure:{tool_name}")
        if _reward_failed(step.reward):
            signals.append("task_failed")
        if _score_failed(step.reward):
            signals.append("trajectory_score_failure")
        if _has_llm_usage_metadata(step.state) or _has_llm_usage_metadata(step.action):
            signals.append("llm_usage_metadata")
        for artifact_path in _generated_artifact_paths(step):
            signals.append(f"generated_artifact_reference:{artifact_path}")

    for tool_name, count in tool_counts.items():
        if count > 1:
            signals.append(f"repeated_action:{tool_name}")
    return _dedupe(tuple(signals))


def _reward_failed(reward: Mapping[str, Any]) -> bool:
    status = str(reward.get("status", "")).lower()
    if status in {"failed", "failure", "error"}:
        return True
    score = reward.get("score")
    if isinstance(score, (int, float)) and score <= 0:
        return True
    for output in reward.get("tool_outputs", []) if isinstance(reward.get("tool_outputs"), list) else []:
        if isinstance(output, Mapping) and str(output.get("status", "")).lower() in {"failed", "failure", "error"}:
            return True
    return False


def _score_failed(reward: Mapping[str, Any]) -> bool:
    score = reward.get("score")
    return isinstance(score, (int, float)) and score <= 0


def _has_llm_usage_metadata(value: Any) -> bool:
    if isinstance(value, Mapping):
        keys = {str(key).lower() for key in value}
        if keys & {"llm_calls", "llm_usage", "usage", "total_tokens", "prompt_tokens", "completion_tokens", "cost"}:
            return True
        return any(_has_llm_usage_metadata(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_llm_usage_metadata(item) for item in value)
    return False


def _generated_artifact_paths(step: TraceEvidenceStep) -> tuple[str, ...]:
    paths: list[str] = []
    _collect_generated_artifact_paths(step.state, paths)
    _collect_generated_artifact_paths(step.action, paths)
    _collect_generated_artifact_paths(step.reward, paths)
    return _dedupe(tuple(paths))


def _collect_generated_artifact_paths(value: Any, paths: list[str]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in {"generated_artifacts", "artifacts"}:
                _collect_artifact_path_values(item, paths)
            else:
                _collect_generated_artifact_paths(item, paths)
    elif isinstance(value, list):
        for item in value:
            _collect_generated_artifact_paths(item, paths)


def _collect_artifact_path_values(value: Any, paths: list[str]) -> None:
    if isinstance(value, Mapping):
        path = value.get("path") or value.get("file") or value.get("name")
        if isinstance(path, str) and path:
            paths.append(path)
        for item in value.values():
            _collect_artifact_path_values(item, paths)
    elif isinstance(value, list):
        for item in value:
            _collect_artifact_path_values(item, paths)


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return tuple(deduped)


def _step_text(step: TraceEvidenceStep) -> str:
    return json.dumps(_target_evidence_payload(step), ensure_ascii=False).lower()


def _target_evidence_payload(step: TraceEvidenceStep) -> Mapping[str, Any]:
    return {
        "state": _target_evidence_state(step),
        "action": step.action,
        "reward": step.reward,
        "tool_names": step.tool_names,
    }


def _target_evidence_state(step: TraceEvidenceStep) -> Mapping[str, Any]:
    filtered: dict[str, Any] = {}
    for key, value in step.state.items():
        if key == "messages":
            messages = _target_evidence_messages(value)
            if messages:
                filtered[key] = messages
            continue
        if key == "input" and _is_tool_result_step(step):
            input_meta = _target_evidence_input_metadata(value)
            if input_meta:
                filtered[key] = input_meta
            continue
        filtered[key] = value
    return filtered


def _target_evidence_messages(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    messages: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role", "")).lower()
        if role in {"system", "developer", "tool"}:
            continue
        messages.append(item)
    return tuple(messages)


def _is_tool_result_step(step: TraceEvidenceStep) -> bool:
    pre_agent = (step.pre_agent or "").lower()
    agent_id = (step.agent_id or "").lower()
    if not pre_agent or pre_agent == "runner":
        return False
    if agent_id and pre_agent == agent_id:
        return False
    return True


def _target_evidence_input_metadata(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if key in {"from_agent_name", "to_agent_name"}
    }
