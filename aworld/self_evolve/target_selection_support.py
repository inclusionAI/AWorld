"""Leaf target-selection report helpers shared by CLI and run lifecycle."""

from __future__ import annotations

from aworld.self_evolve.credit_assignment import TargetSelectionReport
from aworld.self_evolve.provenance import TargetSelectionOrigin
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import SelfEvolveTargetRef


def explicit_target_selection_report(
    target: SelfEvolveTargetRef,
    trace_packs: tuple[TracePack, ...],
) -> TargetSelectionReport:
    evidence_step_ids = tuple(
        step.evidence_id
        for trace_pack in trace_packs
        for step in trace_pack.steps
    )
    return TargetSelectionReport(
        selected_target=target,
        confidence=1.0,
        evidence_step_ids=evidence_step_ids,
        failure_category="explicit_target",
        signals=("explicit_target",),
        diagnostics={
            "pack_ids": [trace_pack.pack_id for trace_pack in trace_packs],
            "target_inference": "bypassed",
        },
        selection_origin=TargetSelectionOrigin.OPERATOR_EXPLICIT,
    )
