"""Dataset projection and support identity policy for paired replay."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Callable, Mapping

from aworld.self_evolve.controllers.screening_execution import _non_negative_int
from aworld.self_evolve.controllers.screening_helpers import (
    _control_qualification_identity as _screening_control_qualification_identity,
)
from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.replay import (
    CandidateReplayRequest,
    CandidateReplayResult,
    NormalizedReplayMembers,
    _replay_member_pair_is_comparable,
    build_paired_replay_dataset,
    candidate_replay_is_comparable,
    normalize_replay_members,
    replay_support_fingerprint,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayAdaptationBundle,
    replay_adaptation_semantic_fingerprint,
)
from aworld.self_evolve.replay_capability import (
    replay_capability_semantic_fingerprint,
)
from aworld.self_evolve.sanitization import sanitize_text
from aworld.self_evolve.types import CandidateVariant


_AUTHORITATIVE_CONTEXT_COMPACTION_CHARS = 3_500
_AUTHORITATIVE_CONTEXT_USER_TURN_CHARS = 512
_AUTHORITATIVE_CONTEXT_USER_TURNS = 4
_AUTHORITATIVE_CONTEXT_ASSISTANT_TURN_CHARS = 1_000
_AUTHORITATIVE_CONTEXT_ASSISTANT_TURNS = 2
_AUTHORITATIVE_SHORT_CONTINUATION_CHARS = 16
_AUTHORITATIVE_SHORT_CONTINUATION_TURNS = 4


def _control_qualification_identity(
    *,
    case_id: str,
    baseline_skill_fingerprint: str,
    replay_adaptation: ReplayAdaptationBundle,
    timeout_seconds: float,
    max_steps: int | None,
    max_tool_calls: int | None,
    capability_fingerprint: Callable[[object], str] = (
        replay_capability_semantic_fingerprint
    ),
    adaptation_fingerprint: Callable[[object], str] = (
        replay_adaptation_semantic_fingerprint
    ),
    support_fingerprint: Callable[[object], str] = replay_support_fingerprint,
) -> dict[str, object]:
    """Preserve the legacy Runner fingerprint injection seams."""

    return _screening_control_qualification_identity(
        case_id=case_id,
        baseline_skill_fingerprint=baseline_skill_fingerprint,
        replay_adaptation=replay_adaptation,
        timeout_seconds=timeout_seconds,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        replay_capability_fingerprint=capability_fingerprint,
        replay_adaptation_fingerprint=adaptation_fingerprint,
        support_fingerprint=support_fingerprint,
    )


def _partial_replay_evaluator_dataset(
    *,
    dataset: SelfEvolveDataset,
    replay_result: CandidateReplayResult,
    candidate: CandidateVariant,
    normalized: NormalizedReplayMembers,
    minimum_independent_cases: int,
    replay_comparable: Callable[..., bool] = candidate_replay_is_comparable,
) -> tuple[SelfEvolveDataset | None, tuple[str, ...]]:
    """Project a diagnostic evaluator panel without relaxing replay admission.

    Measurement v2 can retain a statistically usable set of paired members
    even when other scheduled members time out or fail. The verified replay
    gate must still reject that incomplete panel, but blocking the evaluator
    entirely also prevents a configured judge metric such as ``score`` from
    being materialized. Return only members whose baseline and candidate are
    already strictly comparable; the original failed gate remains the release
    authority.
    """

    comparable_case_ids = tuple(
        member.case_id
        for member in normalized.members
        if _replay_member_pair_is_comparable(
            member.case,
            member.baseline,
            member.candidate,
        )
    )
    if len(comparable_case_ids) < max(1, minimum_independent_cases):
        return None, ()
    admitted = set(comparable_case_ids)
    projected = replace(
        dataset,
        cases=tuple(case for case in dataset.cases if case.case_id in admitted),
        recipe=replace(
            dataset.recipe,
            source={
                **dict(dataset.recipe.source),
                "evaluator_partial_panel": {
                    "role": "diagnostic_only",
                    "case_count": len(comparable_case_ids),
                    "verified_replay_gate_relaxed": False,
                },
            },
            splits={
                split: [case_id for case_id in case_ids if case_id in admitted]
                for split, case_ids in dataset.recipe.splits.items()
            },
            trainable_case_ids=tuple(
                case_id
                for case_id in dataset.recipe.trainable_case_ids
                if case_id in admitted
            ),
            held_out_case_ids=tuple(
                case_id
                for case_id in dataset.recipe.held_out_case_ids
                if case_id in admitted
            ),
        ),
    )
    projected_replay_result = replace(
        replay_result,
        member_results=tuple(
            member
            for member in (replay_result.member_results or ())
            if member.case_id in admitted
        ),
    )
    projected_normalized = normalize_replay_members(
        dataset=projected,
        replay_result=projected_replay_result,
    )
    if not replay_comparable(
        dataset=projected,
        replay_result=projected_replay_result,
        require_adapted=True,
        normalized=projected_normalized,
    ):
        return None, ()
    return (
        build_paired_replay_dataset(
            dataset=projected,
            replay_result=projected_replay_result,
            candidate=candidate,
            normalized=projected_normalized,
        ),
        comparable_case_ids,
    )


def _control_qualification_identity_from_request(
    request: CandidateReplayRequest,
) -> dict[str, object] | None:
    adaptation = request.replay_adaptation
    if (
        adaptation is None
        or request.baseline_skill_fingerprint is None
        or request.timeout_seconds is None
        or request.support_fingerprint is None
        or request.timeout_envelope_fingerprint is None
    ):
        return None
    identity = _control_qualification_identity(
        case_id=request.task_id,
        baseline_skill_fingerprint=request.baseline_skill_fingerprint,
        replay_adaptation=adaptation,
        timeout_seconds=request.timeout_seconds,
        max_steps=request.max_steps,
        max_tool_calls=request.max_tool_calls,
    )
    compatible_support_fingerprints = {identity["support_fingerprint"]}
    legacy_support_fingerprint = _legacy_path_sensitive_support_fingerprint(
        adaptation
    )
    if legacy_support_fingerprint is not None:
        compatible_support_fingerprints.add(legacy_support_fingerprint)
    if (
        request.support_fingerprint not in compatible_support_fingerprints
        or identity["timeout_envelope_fingerprint"]
        != request.timeout_envelope_fingerprint
    ):
        return None
    return identity


def _legacy_path_sensitive_support_fingerprint(
    replay_adaptation: ReplayAdaptationBundle,
) -> str | None:
    """Recognize persisted v1 requests whose support identity included paths."""

    capability = replay_adaptation.replay_capability
    payload = {
        "schema_version": "aworld.self_evolve.replay_support_identity.v1",
        "capability_package_fingerprint": (
            capability.capability_package_fingerprint
            if capability is not None
            else "framework-only"
        ),
        "replay_capability_fingerprint": (
            capability.fingerprint if capability is not None else "framework-only"
        ),
        "adaptation_fingerprint": replay_adaptation.adaptation_fingerprint,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _task_input_content(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        content = value.get("content")
        return content if isinstance(content, str) else ""
    return ""


def _authoritative_control_should_defer(
    case: EvalCase,
    observation: Mapping[str, float | int],
) -> bool:
    """Identify controls that are valid evidence but unsafe sentinels."""

    snapshot = case.context_snapshot
    current_task = _task_input_content(
        snapshot.task_input if snapshot is not None else case.input
    ).strip()
    if snapshot is not None:
        if snapshot.context_status == "incomplete":
            return True
        if (
            len(current_task) <= _AUTHORITATIVE_SHORT_CONTINUATION_CHARS
            and len(snapshot.prior_turns)
            >= _AUTHORITATIVE_SHORT_CONTINUATION_TURNS
        ):
            return True
    baseline_attempts = _non_negative_int(
        observation.get("baseline_attempt_count")
    )
    baseline_successes = _non_negative_int(
        observation.get("baseline_success_count")
    )
    baseline_timeouts = _non_negative_int(
        observation.get("baseline_timeout_count")
    )
    return bool(
        _non_negative_int(observation.get("invalid_control_count")) > 0
        or (
            baseline_attempts >= 2
            and baseline_timeouts > 0
            and baseline_timeouts >= baseline_successes
        )
    )


def _compact_authoritative_case_context(case: EvalCase) -> EvalCase:
    snapshot = case.context_snapshot
    content = _task_input_content(case.input)
    if (
        snapshot is None
        or not snapshot.prior_turns
        or len(content) <= _AUTHORITATIVE_CONTEXT_COMPACTION_CHARS
    ):
        return case
    indexed_turns = tuple(enumerate(snapshot.prior_turns))
    all_user_turns = tuple(
        item for item in indexed_turns if item[1].role == "user"
    )
    user_turns = (
        all_user_turns
        if len(all_user_turns) <= _AUTHORITATIVE_CONTEXT_USER_TURNS
        else (
            *all_user_turns[: _AUTHORITATIVE_CONTEXT_USER_TURNS // 2],
            *all_user_turns[-(_AUTHORITATIVE_CONTEXT_USER_TURNS // 2) :],
        )
    )
    assistant_turns = tuple(
        item for item in indexed_turns if item[1].role == "assistant"
    )[-_AUTHORITATIVE_CONTEXT_ASSISTANT_TURNS:]
    retained = tuple(
        turn for _index, turn in sorted((*user_turns, *assistant_turns))
    )
    transcript = "\n".join(
        f"- {turn.role} [{turn.evidence_ref}]: "
        + sanitize_text(
            turn.content,
            max_chars=(
                _AUTHORITATIVE_CONTEXT_USER_TURN_CHARS
                if turn.role == "user"
                else _AUTHORITATIVE_CONTEXT_ASSISTANT_TURN_CHARS
            ),
        )
        for turn in retained
    )
    current_task = _task_input_content(snapshot.task_input)
    compacted_content = (
        "Recorded prior task context "
        f"[{snapshot.link_strategy or 'recorded'}; structured compact; "
        f"retained {len(user_turns)} user anchors and "
        f"{len(assistant_turns)} recent assistant turns from "
        f"{len(snapshot.prior_turns)} turns]:\n"
        f"{transcript}\n\nCurrent task:\n{current_task}"
    )
    compacted_input: object
    if isinstance(case.input, str):
        compacted_input = compacted_content
    elif isinstance(case.input, Mapping):
        compacted_input = {**dict(case.input), "content": compacted_content}
    else:
        return case
    return replace(
        case,
        input=compacted_input,
        source={
            **dict(case.source),
            "authoritative_context_compacted": True,
            "authoritative_context_original_chars": len(content),
            "authoritative_context_compacted_chars": len(compacted_content),
            "authoritative_context_retained_turns": len(retained),
            "authoritative_context_retained_user_turns": len(user_turns),
            "authoritative_context_retained_assistant_turns": len(
                assistant_turns
            ),
        },
    )


def _authoritative_replay_dataset(
    dataset: SelfEvolveDataset,
    *,
    empirical_observations: Mapping[
        str, Mapping[str, float | int]
    ] | None = None,
) -> SelfEvolveDataset:
    """Order controls by observed health for authoritative execution.

    Screening is the cheap control-qualification plane.  Its result must guide
    the expensive authoritative replay: controls that already produced a
    comparable pair run first, unobserved controls remain eligible, and known
    invalid controls move to the tail.  Case membership and recipe stay fixed;
    the derived order is included in the replay fingerprint so execution is
    reproducible and cannot silently reuse evidence from another schedule.
    """

    observations = empirical_observations or {}
    compacted_cases = tuple(
        _compact_authoritative_case_context(case) for case in dataset.cases
    )
    deferred_case_ids = tuple(
        case.case_id
        for case in compacted_cases
        if _authoritative_control_should_defer(
            case,
            observations.get(case.case_id, {}),
        )
    )
    indexed_cases = tuple(enumerate(compacted_cases))

    def rank(item: tuple[int, EvalCase]) -> tuple[int, int, int, int]:
        index, case = item
        observation = observations.get(case.case_id, {})
        passed_count = _non_negative_int(observation.get("passed_count"))
        failure_count = _non_negative_int(
            observation.get("authoritative_failure_count")
        )
        invalid_count = _non_negative_int(
            observation.get("invalid_control_count")
        )
        if case.case_id in deferred_case_ids:
            health_class = 3
        elif failure_count > 0 or passed_count > 0:
            health_class = 0
        elif invalid_count > 0:
            health_class = 2
        else:
            health_class = 1
        return health_class, -failure_count, -passed_count, index

    ordered = tuple(case for _index, case in sorted(indexed_cases, key=rank))
    compacted_case_ids = tuple(
        case.case_id
        for original, case in zip(dataset.cases, compacted_cases)
        if case.input != original.input
    )
    if (
        ordered == dataset.cases
        and not compacted_case_ids
        and not deferred_case_ids
    ):
        return dataset
    return replace(
        dataset,
        cases=ordered,
        recipe=replace(
            dataset.recipe,
            source={
                **dict(dataset.recipe.source),
                "authoritative_deferred_control_case_ids": list(
                    deferred_case_ids
                ),
                "authoritative_compacted_context_case_ids": list(
                    compacted_case_ids
                ),
            },
        ),
    )
def _prioritize_candidate_intervention_cases(
    dataset: SelfEvolveDataset,
    replay_adaptation: ReplayAdaptationBundle,
) -> SelfEvolveDataset:
    """Run controls capable of exercising candidate replay support first.

    A dataset-wide capability may cover only a minority of cases. Running
    context-only follow-ups first can spend the campaign deadline without any
    candidate-owned protocol traffic, leaving the effect unidentifiable. Direct
    dependency bindings are strongest, task-input references are second, and
    unrelated controls remain available at the tail for regression evidence.
    """

    capability = replay_adaptation.replay_capability
    if capability is None or not capability.endpoint_replacements:
        return dataset
    service_sources = tuple(capability.endpoint_replacements)
    priority_by_case_id: dict[str, int] = {}
    for case_adaptation in replay_adaptation.cases:
        dependency_ids = {
            binding.dependency_id for binding in case_adaptation.bindings
        }
        if dependency_ids.intersection(service_sources):
            priority_by_case_id[case_adaptation.case_id] = 0
            continue
        serialized_input = json.dumps(
            case_adaptation.adapted_task_input,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        priority_by_case_id[case_adaptation.case_id] = (
            1
            if any(source in serialized_input for source in service_sources)
            else 2
        )
    indexed_cases = tuple(enumerate(dataset.cases))
    ordered = tuple(
        case
        for _index, case in sorted(
            indexed_cases,
            key=lambda item: (
                priority_by_case_id.get(item[1].case_id, 2),
                item[0],
            ),
        )
    )
    if ordered == dataset.cases:
        return dataset
    direct_case_ids = [
        case.case_id
        for case in ordered
        if priority_by_case_id.get(case.case_id) == 0
    ]
    referenced_case_ids = [
        case.case_id
        for case in ordered
        if priority_by_case_id.get(case.case_id) == 1
    ]
    return replace(
        dataset,
        cases=ordered,
        recipe=replace(
            dataset.recipe,
            source={
                **dict(dataset.recipe.source),
                "authoritative_intervention_first": True,
                "authoritative_direct_intervention_case_ids": direct_case_ids,
                "authoritative_referenced_intervention_case_ids": (
                    referenced_case_ids
                ),
            },
        ),
    )
