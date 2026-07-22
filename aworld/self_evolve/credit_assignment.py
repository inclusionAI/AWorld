from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from aworld.self_evolve.provenance import (
    TargetMutationIntent,
    TargetProvenance,
    TargetProvenanceResolution,
    TargetProvenanceStatus,
    TargetSelectionOrigin,
    canonical_local_target_path,
    resolve_target_provenance,
)
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
    draft_skill_root: str | None = None
    workspace_root: str | None = None

    def find(self, target_type: str, target_id: str) -> TargetInventoryEntry | None:
        matches = self.find_all(target_type, target_id)
        return matches[0] if len(matches) == 1 else None

    def find_all(
        self,
        target_type: str,
        target_id: str,
    ) -> tuple[TargetInventoryEntry, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.target.target_type == target_type
            and entry.target.target_id == target_id
        )

    def only_target_types(self, target_types: Iterable[str]) -> TargetInventory:
        allowed = frozenset(target_types)
        return TargetInventory(
            entries=tuple(
                entry for entry in self.entries if entry.target.target_type in allowed
            ),
            draft_skill_root=(
                self.draft_skill_root if "skill" in allowed else None
            ),
            workspace_root=self.workspace_root,
        )


@dataclass(frozen=True)
class TargetSelectionReport:
    selected_target: SelfEvolveTargetRef | None
    confidence: float
    evidence_step_ids: tuple[str, ...]
    failure_category: str
    signals: tuple[str, ...] = ()
    no_target_reason: str | None = None
    diagnostics: Mapping[str, Any] | None = None
    provenance_status: str | None = None
    provenance_reason: str | None = None
    selection_origin: TargetSelectionOrigin | None = None
    target_intent: TargetMutationIntent | None = None
    capability_fingerprint: str | None = None


@dataclass(frozen=True)
class TargetSelectionDecision:
    """A target-selection report coupled to its authorization classification."""

    report: TargetSelectionReport
    provenance_resolution: TargetProvenanceResolution
    selection_origin: TargetSelectionOrigin = TargetSelectionOrigin.UNKNOWN
    target_intent: TargetMutationIntent | None = None

    @property
    def provenance(self) -> TargetProvenance | None:
        return self.provenance_resolution.provenance

    @property
    def unresolved_reason(self) -> str | None:
        if self.provenance_resolution.resolved:
            return None
        return self.provenance_resolution.reason


def build_target_selection_decision(
    report: TargetSelectionReport,
    *,
    inventory: TargetInventory,
    selection_origin: TargetSelectionOrigin,
    workspace_root: str | Path | None = None,
    target_intent: TargetMutationIntent | str | None = None,
) -> TargetSelectionDecision:
    try:
        typed_origin = TargetSelectionOrigin(selection_origin)
    except ValueError:
        typed_origin = TargetSelectionOrigin.UNKNOWN
    target = report.selected_target
    effective_workspace_root = workspace_root or inventory.workspace_root
    if target is None:
        resolution = TargetProvenanceResolution(
            status=TargetProvenanceStatus.UNRESOLVED,
            provenance=None,
            reason=report.no_target_reason or "no mutation target was selected",
        )
        return TargetSelectionDecision(
            report=replace(report, selection_origin=typed_origin),
            provenance_resolution=resolution,
            selection_origin=typed_origin,
            target_intent=None,
        )

    inventory_entries = inventory.find_all(target.target_type, target.target_id)
    try:
        typed_intent = (
            TargetMutationIntent(target_intent or report.target_intent)
            if target_intent is not None or report.target_intent is not None
            else (
                TargetMutationIntent.INFERRED_DRAFT_CREATION
                if typed_origin == TargetSelectionOrigin.INFERRED
                and not inventory_entries
                else TargetMutationIntent.EXISTING_TARGET_MUTATION
            )
        )
    except ValueError:
        typed_intent = None

    intent_error = _target_intent_error(
        report,
        target=target,
        inventory_entries=inventory_entries,
        selection_origin=typed_origin,
        target_intent=typed_intent,
    )
    if intent_error is not None:
        resolution = TargetProvenanceResolution(
            status=TargetProvenanceStatus.UNRESOLVED,
            provenance=None,
            reason=intent_error,
        )
    elif len(inventory_entries) > 1:
        resolution = TargetProvenanceResolution(
            status=TargetProvenanceStatus.UNRESOLVED,
            provenance=None,
            reason="inventory contains duplicate target identity",
        )
    else:
        resolution = resolve_target_provenance(
            target,
            selection_origin=typed_origin,
            inventory_provenance=(
                inventory_entries[0].provenance if inventory_entries else None
            ),
            workspace_root=effective_workspace_root,
        )
    return TargetSelectionDecision(
        report=replace(
            report,
            provenance_status=resolution.status,
            provenance_reason=resolution.reason,
            selection_origin=typed_origin,
            target_intent=typed_intent,
        ),
        provenance_resolution=resolution,
        selection_origin=typed_origin,
        target_intent=typed_intent,
    )


def _target_intent_error(
    report: TargetSelectionReport,
    *,
    target: SelfEvolveTargetRef,
    inventory_entries: tuple[TargetInventoryEntry, ...],
    selection_origin: TargetSelectionOrigin,
    target_intent: TargetMutationIntent | None,
) -> str | None:
    if (
        isinstance(report.diagnostics, Mapping)
        and report.diagnostics.get("invalid_target_intent") is not None
    ):
        return "target mutation intent is invalid"
    if target_intent is None:
        return "target mutation intent is missing or invalid"
    if target_intent == TargetMutationIntent.INFERRED_DRAFT_CREATION:
        if selection_origin != TargetSelectionOrigin.INFERRED:
            return "draft creation intent requires inferred target selection"
        if target.target_type != "skill":
            return "draft creation intent requires a skill target"
        if inventory_entries:
            return "draft creation intent cannot replace an inventory target"
        if not report.evidence_step_ids:
            return "draft creation intent requires trajectory evidence"
        if not _valid_capability_fingerprint(report.capability_fingerprint):
            return "draft creation intent requires a valid capability fingerprint"
        if not _valid_skill_id(target.target_id):
            return "draft creation intent requires a valid skill id"
    elif selection_origin == TargetSelectionOrigin.INFERRED and len(inventory_entries) != 1:
        return "inferred existing-target mutation requires one inventory target"
    return None


@dataclass(frozen=True)
class LLMTargetDiagnosis:
    target_type: str
    target_id: str | None
    confidence: float
    evidence_step_ids: tuple[str, ...]
    failure_category: str
    rationale: str
    selection_kind: str = "existing_target"


@dataclass(frozen=True)
class NewSkillIntent:
    capability_fingerprint: str
    target_id: str
    capability_summary: str
    confidence: float
    evidence_step_ids: tuple[str, ...]
    operation_ids: tuple[str, ...] = ()
    dependency_kinds: tuple[str, ...] = ()
    failure_codes: tuple[str, ...] = ()


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
            fallback_report = self._assign_fallback_target(
                trace_pack,
                serialized=serialized,
                existing_signals=signals,
                include_llm=True,
            )
            if fallback_report is not None:
                return fallback_report
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
            unavailable_signal = f"unavailable_signaled_target:{signal.target_type}"
            fallback_signals = _dedupe(signals + (unavailable_signal,))
            fallback_report = self._assign_fallback_target(
                trace_pack,
                serialized=serialized,
                existing_signals=fallback_signals,
                include_llm=True,
            )
            if fallback_report is not None:
                diagnostics = dict(fallback_report.diagnostics or {})
                diagnostics["unavailable_signaled_target"] = {
                    "target_type": signal.target_type,
                    "target_id": signal.target_id,
                }
                return TargetSelectionReport(
                    selected_target=fallback_report.selected_target,
                    confidence=fallback_report.confidence,
                    evidence_step_ids=fallback_report.evidence_step_ids,
                    failure_category=fallback_report.failure_category,
                    signals=fallback_report.signals,
                    no_target_reason=fallback_report.no_target_reason,
                    diagnostics=diagnostics,
                )
            return TargetSelectionReport(
                selected_target=None,
                confidence=0.0,
                evidence_step_ids=evidence_ids,
                failure_category="no_target",
                signals=fallback_signals,
                no_target_reason=(
                    f"deterministic signal selected {signal.target_type}:{signal.target_id}, "
                    "but the target is not available in the active capability inventory"
                ),
                diagnostics={
                    "pack_id": trace_pack.pack_id,
                    "unavailable_signaled_target": {
                        "target_type": signal.target_type,
                        "target_id": signal.target_id,
                    },
                },
            )

        return TargetSelectionReport(
            selected_target=entry.target,
            confidence=signal.confidence,
            evidence_step_ids=evidence_ids,
            failure_category=signal.failure_category,
            signals=signals,
            diagnostics={"pack_id": trace_pack.pack_id},
        )

    def assign_decision(self, trace_pack: TracePack) -> TargetSelectionDecision:
        """Select and classify one target through a total provenance boundary."""

        return build_target_selection_decision(
            self.assign(trace_pack),
            inventory=self.inventory,
            selection_origin="inferred",
        )

    def _assign_fallback_target(
        self,
        trace_pack: TracePack,
        *,
        serialized: str,
        existing_signals: tuple[str, ...],
        include_llm: bool,
    ) -> TargetSelectionReport | None:
        if include_llm:
            llm_report = self._assign_from_llm(trace_pack, existing_signals)
            if llm_report is not None:
                return llm_report
        existing_skill_report = self._assign_from_skill_inventory(
            trace_pack,
            existing_signals=existing_signals,
        )
        if existing_skill_report is not None:
            return existing_skill_report
        capability_report = self._assign_capability_intent(
            trace_pack,
            existing_signals=existing_signals,
        )
        return capability_report

    def _assign_capability_intent(
        self,
        trace_pack: TracePack,
        *,
        existing_signals: tuple[str, ...],
        suggested_skill_id: str | None = None,
        confidence: float | None = None,
        capability_summary: str | None = None,
    ) -> TargetSelectionReport | None:
        if self.inventory.draft_skill_root is None:
            return None
        intent = _compile_new_skill_intent(
            trace_pack,
            suggested_skill_id=suggested_skill_id,
            confidence=confidence,
            capability_summary=capability_summary,
        )
        if intent is None:
            return None
        inventory_entry = self.inventory.find("skill", intent.target_id)
        if inventory_entry is not None:
            return TargetSelectionReport(
                selected_target=inventory_entry.target,
                confidence=max(0.9, intent.confidence),
                evidence_step_ids=intent.evidence_step_ids,
                failure_category="skill",
                signals=_dedupe(
                    tuple(
                        signal
                        for signal in existing_signals
                        if signal != "low_confidence"
                    )
                    + ("capability_fingerprint_inventory_match",)
                ),
                diagnostics={
                    "pack_id": trace_pack.pack_id,
                    "capability_summary": intent.capability_summary,
                    "operation_ids": intent.operation_ids,
                    "dependency_kinds": intent.dependency_kinds,
                    "failure_codes": intent.failure_codes,
                },
                target_intent=TargetMutationIntent.EXISTING_TARGET_MUTATION,
                capability_fingerprint=intent.capability_fingerprint,
            )
        signals = _dedupe(
            existing_signals
            + (
                "new_skill_candidate",
                "validated_capability_gap",
            )
        )
        return TargetSelectionReport(
            selected_target=SelfEvolveTargetRef(
                target_type="skill",
                target_id=intent.target_id,
                path=None,
            ),
            confidence=intent.confidence,
            evidence_step_ids=intent.evidence_step_ids,
            failure_category="skill",
            signals=signals,
            diagnostics={
                "pack_id": trace_pack.pack_id,
                "capability_summary": intent.capability_summary,
                "operation_ids": intent.operation_ids,
                "dependency_kinds": intent.dependency_kinds,
                "failure_codes": intent.failure_codes,
            },
            target_intent=TargetMutationIntent.INFERRED_DRAFT_CREATION,
            capability_fingerprint=intent.capability_fingerprint,
        )

    def _assign_from_skill_inventory(
        self,
        trace_pack: TracePack,
        *,
        existing_signals: tuple[str, ...],
    ) -> TargetSelectionReport | None:
        matches: list[tuple[int, TargetInventoryEntry, tuple[str, ...]]] = []
        failed_text = " ".join(
            _step_text(step)
            for step in trace_pack.steps
            if _reward_failed(step.reward)
        )
        if not failed_text and trace_pack.steps:
            failed_text = _step_text(trace_pack.steps[-1])
        for entry in self.inventory.entries:
            if entry.target.target_type != "skill" or entry.provenance.protected:
                continue
            aliases = _dedupe(
                _skill_match_aliases(entry)
                + _tool_anchored_skill_aliases(entry, trace_pack)
            )
            matched_aliases = tuple(alias for alias in aliases if alias in failed_text)
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
            tuple(signal for signal in existing_signals if signal != "low_confidence")
            + (f"skill_alias_match:{entry.target.target_id}",)
        )
        return TargetSelectionReport(
            selected_target=entry.target,
            confidence=0.9,
            evidence_step_ids=evidence_ids,
            failure_category="skill",
            signals=signals,
            diagnostics={
                "pack_id": trace_pack.pack_id,
                "matched_aliases": matched_aliases,
            },
            target_intent=TargetMutationIntent.EXISTING_TARGET_MUTATION,
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

        if (
            diagnosis.selection_kind == "new_skill"
            or diagnosis.target_type == "new_skill"
        ):
            if diagnosis.target_type not in {"skill", "new_skill"}:
                return TargetSelectionReport(
                    selected_target=None,
                    confidence=0.0,
                    evidence_step_ids=diagnosis.evidence_step_ids,
                    failure_category="no_target",
                    signals=signals,
                    no_target_reason="new-skill diagnosis requires a skill target type",
                    diagnostics=diagnostics,
                )
            if not _valid_skill_id(diagnosis.target_id):
                return TargetSelectionReport(
                    selected_target=None,
                    confidence=0.0,
                    evidence_step_ids=diagnosis.evidence_step_ids,
                    failure_category="no_target",
                    signals=signals,
                    no_target_reason="new-skill diagnosis supplied an invalid skill id",
                    diagnostics=diagnostics,
                )
            if self.inventory.find_all("skill", diagnosis.target_id):
                return TargetSelectionReport(
                    selected_target=None,
                    confidence=0.0,
                    evidence_step_ids=diagnosis.evidence_step_ids,
                    failure_category="no_target",
                    signals=signals,
                    no_target_reason=(
                        "new-skill diagnosis collides with an inventory target"
                    ),
                    diagnostics=diagnostics,
                )
            capability_report = self._assign_capability_intent(
                trace_pack,
                existing_signals=signals,
                suggested_skill_id=diagnosis.target_id,
                confidence=diagnosis.confidence,
                capability_summary=diagnosis.rationale,
            )
            if capability_report is not None:
                return capability_report
            return TargetSelectionReport(
                selected_target=None,
                confidence=diagnosis.confidence,
                evidence_step_ids=diagnosis.evidence_step_ids,
                failure_category="no_target",
                signals=signals,
                no_target_reason="new-skill diagnosis lacks a structured capability gap",
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
            target_intent=TargetMutationIntent.EXISTING_TARGET_MUTATION,
        )


def build_default_target_inventory(workspace_root: str | Path) -> TargetInventory:
    root = Path(workspace_root).resolve()
    entries: list[TargetInventoryEntry] = []

    def add(entry: TargetInventoryEntry) -> None:
        entries.append(entry)

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
    return TargetInventory(
        entries=tuple(entries),
        draft_skill_root=str(root / ".aworld" / "self_evolve" / "drafts" / "skills"),
        workspace_root=str(root),
    )


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
    if _looks_like_browser_runtime_issue(serialized):
        return _Signal(
            target_type="skill",
            target_id="agent-browser",
            failure_category="skill",
            confidence=0.9,
            signals=("browser_runtime_issue",),
            keywords=("browser", "chrome", "cdp", "login", "profile", "session"),
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


def _looks_like_browser_runtime_issue(serialized: str) -> bool:
    browser_markers = ("browser", "chrome", "cdp", "devtools", "playwright")
    runtime_markers = (
        "logged out",
        "logged-out",
        "not logged in",
        "login",
        "profile",
        "session",
        "cookie",
        "cookies",
        "connect_over_cdp",
    )
    return any(marker in serialized for marker in browser_markers) and any(
        marker in serialized for marker in runtime_markers
    )


_SKILL_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_CAPABILITY_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_RESERVED_SKILL_IDS = frozenset({"app-evaluator", "no-target", "self-evolve"})
_FAILED_STATUSES = frozenset(
    {"cancelled", "error", "failed", "failure", "rejected", "timeout"}
)


def _valid_skill_id(value: str | None) -> bool:
    return bool(
        isinstance(value, str)
        and value not in _RESERVED_SKILL_IDS
        and _SKILL_ID_PATTERN.fullmatch(value)
    )


def _valid_capability_fingerprint(value: str | None) -> bool:
    return bool(
        isinstance(value, str)
        and _CAPABILITY_FINGERPRINT_PATTERN.fullmatch(value)
    )


def _compile_new_skill_intent(
    trace_pack: TracePack,
    *,
    suggested_skill_id: str | None = None,
    confidence: float | None = None,
    capability_summary: str | None = None,
) -> NewSkillIntent | None:
    """Compile a path-free, stable capability intent from bounded trace features."""

    failed_steps = tuple(
        step for step in trace_pack.steps if _reward_failed(step.reward)
    )
    if not failed_steps:
        return None

    operation_ids = _dedupe(
        tuple(
            operation
            for step in trace_pack.steps
            for operation in (_normalize_operation_id(item) for item in step.tool_names)
            if operation
        )
    )
    dependency_kinds = _dedupe(
        tuple(
            dependency
            for step in trace_pack.steps
            for dependency in _structured_dependency_kinds(step)
        )
    )
    if not operation_ids and not dependency_kinds:
        return None

    failure_codes = _dedupe(
        tuple(
            code
            for step in failed_steps
            for code in _structured_failure_codes(step.reward)
        )
    ) or ("task_failed",)
    fingerprint_payload = {
        "schema_version": 1,
        "operation_ids": sorted(operation_ids),
        "dependency_kinds": sorted(dependency_kinds),
        "failure_codes": sorted(failure_codes),
    }
    digest = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    fingerprint = f"sha256:{digest}"
    target_id = suggested_skill_id or _capability_skill_id(
        operation_ids=operation_ids,
        dependency_kinds=dependency_kinds,
        digest=digest,
    )
    if not _valid_skill_id(target_id):
        return None
    evidence_step_ids = _dedupe(
        tuple(
            step.evidence_id
            for step in trace_pack.steps
            if step in failed_steps or step.tool_names or _structured_dependency_kinds(step)
        )
    )[:16]
    if not evidence_step_ids:
        return None
    summary = (
        capability_summary.strip()
        if isinstance(capability_summary, str) and capability_summary.strip()
        else _capability_summary(operation_ids, dependency_kinds, failure_codes)
    )[:240]
    return NewSkillIntent(
        capability_fingerprint=fingerprint,
        target_id=target_id,
        capability_summary=summary,
        confidence=(
            min(1.0, max(0.0, float(confidence)))
            if confidence is not None
            else 0.85
        ),
        evidence_step_ids=evidence_step_ids,
        operation_ids=operation_ids,
        dependency_kinds=dependency_kinds,
        failure_codes=failure_codes,
    )


def _normalize_operation_id(value: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", ".", str(value).strip().lower()).strip(".")
    return normalized[:80] or None


def _structured_dependency_kinds(step: TraceEvidenceStep) -> tuple[str, ...]:
    text = _step_text(step)
    kinds: list[str] = []
    if re.search(r"https?://", text):
        kinds.append("http_resource")
    if _generated_artifact_paths(step):
        kinds.append("filesystem_artifact")
    return _dedupe(tuple(kinds))


def _structured_failure_codes(reward: Mapping[str, Any]) -> tuple[str, ...]:
    codes: list[str] = []
    status = str(reward.get("status") or "").strip().lower()
    if status in _FAILED_STATUSES:
        codes.append(status)
    for key in ("code", "error_code", "failure_code", "type"):
        value = reward.get(key)
        if isinstance(value, str) and value.strip():
            normalized = _normalize_operation_id(value)
            if normalized:
                codes.append(normalized)
    return _dedupe(tuple(codes))


def _capability_skill_id(
    *,
    operation_ids: tuple[str, ...],
    dependency_kinds: tuple[str, ...],
    digest: str,
) -> str:
    readable = next(
        (
            token
            for value in (*operation_ids, *dependency_kinds)
            for token in reversed(re.findall(r"[a-z0-9]+", value))
            if len(token) >= 3 and token not in {"execute", "mcp", "resource", "run", "tool"}
        ),
        "capability",
    )
    return f"{readable[:32]}-recovery-{digest[:10]}"


def _capability_summary(
    operation_ids: tuple[str, ...],
    dependency_kinds: tuple[str, ...],
    failure_codes: tuple[str, ...],
) -> str:
    operations = ", ".join(operation_ids) or "unidentified operation"
    dependencies = ", ".join(dependency_kinds) or "no external dependency"
    failures = ", ".join(failure_codes)
    return (
        f"Recover reusable workflow behavior for {operations} with {dependencies}; "
        f"observed failure classes: {failures}."
    )


def _skill_entries_from_workspace(root: Path) -> tuple[TargetInventoryEntry, ...]:
    entries: list[TargetInventoryEntry] = []
    for base in (root / "aworld-skills", root / "skills"):
        if not base.exists() or base.is_symlink() or not base.is_dir():
            continue
        for skill_dir in sorted(base.iterdir()):
            if skill_dir.is_symlink() or not skill_dir.is_dir():
                continue
            skill_path = skill_dir / "SKILL.md"
            if (
                not skill_path.is_file()
                or skill_path.is_symlink()
            ):
                continue
            canonical_path = canonical_local_target_path(
                skill_path,
                workspace_root=root,
            )
            if canonical_path is None:
                continue
            target_id = skill_dir.name
            frontmatter = _skill_frontmatter(canonical_path)
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
                    path=str(canonical_path),
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
    return tuple(dict.fromkeys(normalized))


def _tool_anchored_skill_aliases(
    entry: TargetInventoryEntry,
    trace_pack: TracePack,
) -> tuple[str, ...]:
    tool_text = " ".join(
        tool_name.lower()
        for step in trace_pack.steps
        for tool_name in step.tool_names
    )
    if not tool_text:
        return ()

    aliases = [entry.target.target_id]
    aliases.extend(alias for alias in entry.aliases[:2] if alias)
    anchored: list[str] = []
    for alias in aliases:
        lowered = alias.strip().lower()
        for token in re.findall(r"[a-z0-9]+", lowered):
            if (
                len(token) >= 5
                and token not in _WEAK_SKILL_ALIAS_TOKENS
                and token in tool_text
            ):
                anchored.append(token)
    return tuple(dict.fromkeys(anchored))


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
    if status in _FAILED_STATUSES:
        return True
    score = reward.get("score")
    if isinstance(score, (int, float)) and score <= 0:
        return True
    for output in reward.get("tool_outputs", []) if isinstance(reward.get("tool_outputs"), list) else []:
        if isinstance(output, Mapping) and str(output.get("status", "")).lower() in _FAILED_STATUSES:
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
