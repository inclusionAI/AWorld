"""Shared causal admission predicates for runner and Campaign decisions."""

from __future__ import annotations

from typing import Mapping


_CANDIDATE_PREREQUISITE_STAGES = frozenset(
    {
        "candidate_generation",
        "candidate_repair_conformance",
        "candidate_screening",
        "capability_compile",
        "capability_preflight",
    }
)
_CANDIDATE_ADMISSION_GATES = frozenset(
    {
        "candidate_replay",
        "candidate_screening",
        "replay_evaluator_admission",
    }
)


def candidate_causal_admission_blocker(
    *,
    gate_name: str,
    passed: bool,
    details: Mapping[str, object] | None,
) -> bool:
    """Return whether candidate-owned admission failed before evaluation.

    A rollout may have physically executed during qualification, yet still be
    upstream of authoritative evaluation.  ``evaluator_skipped`` and the
    frozen checkpoint stage carry that causal boundary; a downstream required
    measurement gate must not take ownership of such a failure.
    """

    if passed or not isinstance(details, Mapping):
        return False
    direct_candidate_failure = bool(
        details.get("failure_class") == "candidate"
        and details.get("failure_owner", "candidate") == "candidate"
        and details.get("failure_scope", "candidate") == "candidate"
        and details.get("repairable") is True
    )
    stage = str(details.get("failure_stage") or details.get("stage") or "")
    if direct_candidate_failure and (
        gate_name.startswith("candidate_capability_")
        or gate_name == "candidate_repair_conformance"
        or stage in _CANDIDATE_PREREQUISITE_STAGES
        or (
            gate_name in _CANDIDATE_ADMISSION_GATES
            and (
                details.get("evaluator_skipped") is True
                or details.get("checkpoint_stage") == "screening"
            )
        )
    ):
        return True

    admission_boundary = bool(
        gate_name in _CANDIDATE_ADMISSION_GATES
        and (
            details.get("evaluator_skipped") is True
            or details.get("checkpoint_stage") == "screening"
        )
    )
    for event in _failure_events(details):
        event_stage = str(event.get("stage") or "")
        if (
            event.get("owner") == "candidate"
            and event.get("scope") == "candidate"
            and event.get("repairable") is True
            and (
                event_stage in _CANDIDATE_PREREQUISITE_STAGES
                or admission_boundary
            )
        ):
            return True
    return False


def _failure_events(
    details: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    events: list[Mapping[str, object]] = []
    event = details.get("failure_event")
    if isinstance(event, Mapping):
        events.append(event)
    causal = details.get("causal_failure_events")
    if isinstance(causal, (list, tuple)):
        events.extend(item for item in causal if isinstance(item, Mapping))
    return tuple(events)
