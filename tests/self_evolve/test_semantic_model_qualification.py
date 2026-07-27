from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from aworld.self_evolve.evaluation_plan import (
    QualificationStatus,
    SEMANTIC_EXACT_SNAPSHOT_RUNNER_PROTOCOL_FINGERPRINT_V1,
    SemanticModelQualificationReportV1,
    SemanticQualificationMethod,
    SemanticQualificationRegistryV1,
)
from aworld.self_evolve.improvement_signals import SignalActionability
from aworld.self_evolve.semantic_qualification import (
    FRAMEWORK_SEMANTIC_QUALIFICATION_CORPUS_FINGERPRINT_V1,
    QUALIFICATION_METRIC_KEYS,
    SIGNAL_ACTIONABILITY_ACCURACY,
    SemanticQualificationCaseV1,
    SemanticQualificationCaseOutcomeV1,
    SemanticQualificationContractError,
    SemanticQualificationCorpusV1,
    SemanticQualificationDeploymentRunnerV1,
    SemanticQualificationExpectationsV1,
    SemanticQualificationSnapshotDeploymentRunnerV1,
    SemanticQualificationOutcomeSetV1,
    SemanticQualificationThresholdSetV1,
    evaluate_semantic_model_qualification,
    framework_semantic_qualification_thresholds_v1,
    load_semantic_model_qualification_report,
    load_semantic_qualification_corpus,
    load_semantic_qualification_registry,
    run_semantic_model_qualification,
    run_semantic_snapshot_model_qualification,
    validate_semantic_model_qualification_report,
    _qualification_outcome_from_snapshot,
)
from aworld.self_evolve.evidence import (
    ClaimVerificationVerdict,
    EvidenceClaimKind,
    EvidenceConflictKind,
    EvidenceConflictStatus,
    EvidenceConflictV1,
    SemanticSourceDispositionKind,
)
from tests.self_evolve.test_semantic_ingestion_integration import (
    _prepare,
)


_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "semantic_ingestion"
    / "qualification_corpus.json"
)


def _fingerprint(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _deployment() -> dict[str, str]:
    return {
        "model_profile_fingerprint": _fingerprint("model-profile"),
        "provider_fingerprint": _fingerprint("provider-deployment"),
        "semantic_protocol_fingerprint": _fingerprint(
            "semantic-protocol"
        ),
        "constitution_fingerprint": _fingerprint("constitution"),
    }


def _perfect_outcomes(
    corpus: SemanticQualificationCorpusV1,
) -> SemanticQualificationOutcomeSetV1:
    return SemanticQualificationOutcomeSetV1(
        outcomes=tuple(
            SemanticQualificationCaseOutcomeV1(
                case_id=case.case_id,
                source_unit_dispositions=(
                    case.source_unit_dispositions
                ),
                citation_spans=case.citation_spans,
                entailment_verdicts=case.entailment_verdicts,
                entity_links=case.entity_links,
                detected_conflict_ids=case.conflict_ids,
                signal_actionability=case.signal_actionability,
                elevated_authority_claim_ids=(
                    case.authority_eligible_claim_ids
                ),
            )
            for case in corpus.cases
        )
    )


def _replace_outcome(
    outcomes: SemanticQualificationOutcomeSetV1,
    case_id: str,
    **changes: Any,
) -> SemanticQualificationOutcomeSetV1:
    return SemanticQualificationOutcomeSetV1(
        outcomes=tuple(
            replace(outcome, **changes)
            if outcome.case_id == case_id
            else outcome
            for outcome in outcomes.outcomes
        )
    )


def _snapshot_expectation_case(
    snapshot,
    *,
    source_text: str,
    wrong_citation: bool = False,
    wrong_entity: bool = False,
    reverse_preference: bool = False,
) -> SemanticQualificationCaseV1:
    graph = snapshot.evidence_graph
    claim = next(
        item
        for item in graph.claims
        if item.kind is EvidenceClaimKind.HUMAN_COMPARISON
    )
    semantic_case = snapshot.semantic_cases[0]
    signal = snapshot.improvement_signal_set.signals[0]
    verifications = {
        item.verification_id: item
        for item in graph.claim_verifications
    }
    verdict = next(
        verifications[item].verdict
        for item in claim.verification_ids
        if item in verifications
    )
    paths = {
        item.asset_id: item.relative_path
        for item in snapshot.inventory.assets
    }
    spans = {
        item.span_id: {
            "relative_path": paths[item.asset_id],
            "byte_start": item.byte_start,
            "byte_end": (
                item.byte_end - 1
                if wrong_citation
                else item.byte_end
            ),
            "content_fingerprint": (
                _fingerprint("wrong-citation")
                if wrong_citation
                else item.content_fingerprint
            ),
        }
        for item in graph.spans
    }
    referenced_entities = {
        semantic_case.task_entity_id,
        *semantic_case.execution_entity_ids,
        *claim.subject_entity_ids,
        *claim.object_entity_ids,
        *signal.compared_execution_ids,
        *signal.preferred_execution_ids,
    }
    entities_by_id = {
        item.entity_id: item for item in graph.entities
    }
    entities = {
        entity_id: {
            "kind": entities_by_id[entity_id].kind.value,
            "canonical_name": (
                "definitely-not-the-actual-reviewer"
                if wrong_entity
                and entity_id == claim.subject_entity_ids[0]
                else entities_by_id[entity_id].canonical_name
            ),
            "source_spans": list(
                entities_by_id[entity_id].source_span_ids
            ),
        }
        for entity_id in sorted(referenced_entities)
    }

    def expected_payload(value):
        if isinstance(value, dict):
            return {
                str(key): expected_payload(item)
                for key, item in value.items()
            }
        if isinstance(value, (tuple, list)):
            return [expected_payload(item) for item in value]
        if isinstance(value, str) and value in entities:
            return {"$entity": value}
        return value

    payload = expected_payload(dict(claim.payload))
    if reverse_preference:
        preferred = str(claim.payload["preferred_entity_id"])
        payload["preferred_entity_id"] = {
            "$entity": next(
                item
                for item in claim.object_entity_ids
                if item != preferred
            )
        }
    source_units = {}
    dispositions = {
        item.source_unit_id: item.disposition
        for item in graph.source_dispositions
    }
    for item in snapshot.source_bundle.chunks:
        source_units[item.source_unit_id] = {
            "relative_path": item.relative_path,
            "unit_kind": "chunk",
            "byte_start": item.byte_start,
            "byte_end": item.byte_end,
            "record_locator": None,
            "field_path": None,
            "disposition": dispositions[item.source_unit_id].value,
        }
    expectation = SemanticQualificationExpectationsV1(
        source_units=source_units,
        spans=spans,
        entities=entities,
        claims={
            claim.claim_id: {
                "kind": claim.kind.value,
                "subject_entities": list(claim.subject_entity_ids),
                "object_entities": list(claim.object_entity_ids),
                "payload": payload,
                "resolution_status": claim.resolution_status.value,
                "source_spans": list(claim.source_span_ids),
                "verdict": verdict.value,
            }
        },
        conflicts={},
        cases={
            "case-expected": {
                "task_entity": semantic_case.task_entity_id,
                "input_claims": [],
                "executions": list(
                    semantic_case.execution_entity_ids
                ),
                "trajectory_claims": [],
                "result_claims": [],
                "comparison_claims": [claim.claim_id],
                "conflicts": [],
                "resolution_status": (
                    semantic_case.resolution_status.value
                ),
            }
        },
        signals={
            signal.signal_id: {
                "case": "case-expected",
                "kind": signal.kind.value,
                "compared_executions": list(
                    signal.compared_execution_ids
                ),
                "preferred_executions": list(
                    signal.preferred_execution_ids
                ),
                "supporting_claims": [claim.claim_id],
                "opposing_claims": [],
                "conflicts": [],
                "actionability": signal.actionability.value,
            }
        },
    )
    entity_target = claim.subject_entity_ids[0]
    return SemanticQualificationCaseV1(
        case_id="snapshot-signature-case",
        scenario_tags=("snapshot-signatures",),
        source_documents={"domain.md": source_text},
        source_unit_dispositions={
            label: SemanticSourceDispositionKind(
                str(value["disposition"])
            )
            for label, value in source_units.items()
        },
        citation_spans={
            claim.claim_id: tuple(claim.source_span_ids)
        },
        entailment_verdicts={claim.claim_id: verdict},
        entity_links={"mention-reviewer": entity_target},
        conflict_ids=(),
        signal_actionability={
            signal.signal_id: signal.actionability
        },
        semantic_expectations=expectation,
    )


def _complete_snapshot_expectation_case(
    snapshot,
    *,
    source_text: str,
) -> SemanticQualificationCaseV1:
    graph = snapshot.evidence_graph
    paths = {
        item.asset_id: item.relative_path
        for item in snapshot.inventory.assets
    }
    spans = {
        item.span_id: {
            "relative_path": paths[item.asset_id],
            "byte_start": item.byte_start,
            "byte_end": item.byte_end,
            "content_fingerprint": item.content_fingerprint,
        }
        for item in graph.spans
    }
    entities = {
        item.entity_id: {
            "kind": item.kind.value,
            "canonical_name": item.canonical_name,
            "source_spans": list(item.source_span_ids),
        }
        for item in graph.entities
    }

    def expected_payload(value):
        if isinstance(value, Mapping):
            return {
                str(key): expected_payload(item)
                for key, item in value.items()
            }
        if isinstance(value, (tuple, list)):
            return [expected_payload(item) for item in value]
        if isinstance(value, str) and value in entities:
            return {"$entity": value}
        return value

    verifications = {
        item.verification_id: item
        for item in graph.claim_verifications
    }
    claims = {}
    entailments = {}
    citations = {}
    for claim in graph.claims:
        verdict = next(
            verifications[item].verdict
            for item in claim.verification_ids
            if item in verifications
        )
        claims[claim.claim_id] = {
            "kind": claim.kind.value,
            "subject_entities": list(claim.subject_entity_ids),
            "object_entities": list(claim.object_entity_ids),
            "payload": expected_payload(claim.payload),
            "resolution_status": claim.resolution_status.value,
            "source_spans": list(claim.source_span_ids),
            "verdict": verdict.value,
        }
        entailments[claim.claim_id] = verdict
        citations[claim.claim_id] = tuple(claim.source_span_ids)
    conflicts = {
        item.conflict_id: {
            "kind": item.kind.value,
            "claims": list(item.claim_ids),
            "comparison_unit_entity": (
                item.comparison_unit
                if item.comparison_unit in entities
                else None
            ),
            "comparison_unit_literal": (
                None
                if item.comparison_unit in entities
                else item.comparison_unit
            ),
            "status": item.status.value,
            "resolution_policy_ref": item.resolution_policy_ref,
        }
        for item in graph.conflicts
    }
    cases = {
        item.case_id: {
            "task_entity": item.task_entity_id,
            "input_claims": list(item.input_claim_ids),
            "executions": list(item.execution_entity_ids),
            "trajectory_claims": list(item.trajectory_claim_ids),
            "result_claims": list(item.result_claim_ids),
            "comparison_claims": list(item.comparison_claim_ids),
            "conflicts": list(item.conflict_ids),
            "resolution_status": item.resolution_status.value,
        }
        for item in snapshot.semantic_cases
    }
    signals = {
        item.signal_id: {
            "case": item.case_id,
            "kind": item.kind.value,
            "compared_executions": list(
                item.compared_execution_ids
            ),
            "preferred_executions": list(
                item.preferred_execution_ids
            ),
            "supporting_claims": list(item.supporting_claim_ids),
            "opposing_claims": list(item.opposing_claim_ids),
            "conflicts": list(item.conflict_ids),
            "actionability": item.actionability.value,
        }
        for item in snapshot.improvement_signal_set.signals
    }
    dispositions = {
        item.source_unit_id: item.disposition
        for item in graph.source_dispositions
    }
    source_units = {
        item.source_unit_id: {
            "relative_path": item.relative_path,
            "unit_kind": "chunk",
            "byte_start": item.byte_start,
            "byte_end": item.byte_end,
            "record_locator": None,
            "field_path": None,
            "disposition": dispositions[item.source_unit_id].value,
        }
        for item in snapshot.source_bundle.chunks
    }
    source_units.update(
        {
            item.source_unit_id: {
                "relative_path": item.relative_path,
                "unit_kind": "structured",
                "byte_start": None,
                "byte_end": None,
                "record_locator": item.record_locator,
                "field_path": item.field_path,
                "disposition": dispositions[
                    item.source_unit_id
                ].value,
            }
            for item in snapshot.source_bundle.structured_units
        }
    )
    expectations = SemanticQualificationExpectationsV1(
        source_units=source_units,
        spans=spans,
        entities=entities,
        claims=claims,
        conflicts=conflicts,
        cases=cases,
        signals=signals,
    )
    return SemanticQualificationCaseV1(
        case_id="complete-snapshot-case",
        scenario_tags=("complete-snapshot",),
        source_documents={"domain.md": source_text},
        source_unit_dispositions=dispositions,
        citation_spans=citations,
        entailment_verdicts=entailments,
        entity_links={
            f"mention-{index}": entity_id
            for index, entity_id in enumerate(sorted(entities))
        },
        conflict_ids=tuple(sorted(conflicts)),
        signal_actionability={
            item.signal_id: item.actionability
            for item in snapshot.improvement_signal_set.signals
        },
        semantic_expectations=expectations,
    )


def test_human_labeled_corpus_is_content_addressed_and_public_safe() -> None:
    corpus = load_semantic_qualification_corpus(_FIXTURE)

    assert len(corpus.cases) == 3
    assert corpus.corpus_fingerprint == (
        FRAMEWORK_SEMANTIC_QUALIFICATION_CORPUS_FINGERPRINT_V1
    )
    assert (
        SemanticQualificationCorpusV1.from_dict(corpus.to_dict())
        == corpus
    )
    assert {
        tag
        for case in corpus.cases
        for tag in case.scenario_tags
    } >= {
        "english",
        "chinese",
        "mixed-language",
        "prompt-injection",
    }
    assert all(case.source_documents for case in corpus.cases)
    assert any(
        len(case.source_documents) > 1 for case in corpus.cases
    )
    serialized = json.dumps(
        corpus.to_dict(),
        sort_keys=True,
        ensure_ascii=False,
    ).lower()
    for forbidden in (
        "api_key",
        "access_token",
        "authorization:",
        "provider_credentials",
        "private_key",
    ):
        assert forbidden not in serialized


async def test_recorded_runner_receives_only_executable_source_inputs() -> None:
    corpus = load_semantic_qualification_corpus(_FIXTURE)
    expected = {
        tuple(sorted(case.source_documents.items())): outcome
        for case, outcome in zip(
            corpus.cases,
            _perfect_outcomes(corpus).outcomes,
            strict=True,
        )
    }
    observed: list[tuple[str, tuple[str, ...]]] = []

    async def recorded_runner(source_input):
        assert not hasattr(source_input, "entailment_verdicts")
        assert not hasattr(source_input, "case_id")
        assert not hasattr(source_input, "scenario_tags")
        observed.append(
            (
                source_input.run_token,
                tuple(source_input.source_documents),
            )
        )
        expected_outcome = expected[
            tuple(sorted(source_input.source_documents.items()))
        ]
        return replace(
            expected_outcome,
            case_id=source_input.run_token,
        )

    report = await run_semantic_model_qualification(
        corpus,
        SemanticQualificationDeploymentRunnerV1(
            outcome_runner=recorded_runner,
            **_deployment(),
        ),
        issued_at_utc="2026-01-01T00:00:00Z",
        expires_at_utc="2027-01-01T00:00:00Z",
    )

    assert report.status is QualificationStatus.QUALIFIED, dict(
        report.metric_values
    )
    assert len({item[0] for item in observed}) == len(corpus.cases)
    assert all(
        item[0].startswith("qualification-")
        for item in observed
    )
    assert all(paths for _, paths in observed)


async def test_snapshot_runner_derives_artifacts_and_fails_missing_gold(
    tmp_path: Path,
) -> None:
    source_text = (
        "Harness A failed. Harness B recovered. Human ranking B > A.\n"
    )
    source = tmp_path / "domain.md"
    source.write_text(source_text, encoding="utf-8")
    snapshot, _ = await asyncio.to_thread(_prepare, source)
    case = SemanticQualificationCaseV1(
        case_id="snapshot-derived-case",
        scenario_tags=("snapshot-runner",),
        source_documents={"domain.md": source_text},
        source_unit_dispositions={
            "unit-document": SemanticSourceDispositionKind.EVIDENCE,
        },
        citation_spans={
            "claim-task": ("span-task",),
            "claim-ranking": ("span-ranking",),
        },
        entailment_verdicts={
            "claim-task": ClaimVerificationVerdict.ENTAILED,
            "claim-ranking": ClaimVerificationVerdict.ENTAILED,
        },
        entity_links={
            "mention-task": "task",
            "mention-execution-a": "execution-a",
            "mention-execution-b": "execution-b",
        },
        conflict_ids=("conflict-human-judge-preference",),
        signal_actionability={
            "signal-preference-delta": SignalActionability.ACTIONABLE,
        },
    )
    corpus = SemanticQualificationCorpusV1(cases=(case,))

    async def snapshot_runner(source_input):
        assert source_input.run_token.startswith("qualification-")
        assert dict(source_input.source_documents) == {
            "domain.md": source_text
        }
        return snapshot

    report = await run_semantic_snapshot_model_qualification(
        corpus,
        SemanticQualificationSnapshotDeploymentRunnerV1(
            model_profile_fingerprint=(
                snapshot.semantic_model_profile_fingerprint
            ),
            provider_fingerprint=(
                snapshot.semantic_provider_fingerprint
            ),
            semantic_protocol_fingerprint=(
                snapshot.semantic_protocol_fingerprint
            ),
            constitution_fingerprint=(
                snapshot.constitution.fingerprint
            ),
            snapshot_runner=snapshot_runner,
        ),
    )

    assert report.status is QualificationStatus.FAILED
    assert report.metric_values["conflict_detection_recall"] == 0.0
    assert report.false_authority_elevation_count == 0
    assert report.qualification_method is (
        SemanticQualificationMethod.EXACT_SNAPSHOT_V1
    )
    assert report.runner_protocol_fingerprint == (
        SEMANTIC_EXACT_SNAPSHOT_RUNNER_PROTOCOL_FINGERPRINT_V1
    )
    assert report.case_attestation_bundle_fingerprint is not None


async def test_snapshot_runner_failure_issues_failed_report() -> None:
    corpus = load_semantic_qualification_corpus(_FIXTURE)

    async def failing_runner(_source_input):
        raise RuntimeError("deployment unavailable")

    report = await run_semantic_snapshot_model_qualification(
        corpus,
        SemanticQualificationSnapshotDeploymentRunnerV1(
            snapshot_runner=failing_runner,
            **_deployment(),
        ),
    )

    assert report.status is QualificationStatus.FAILED
    assert report.metric_values["required_claim_recall"] == 0.0
    assert report.false_authority_elevation_count == 0
    assert report.qualification_method is (
        SemanticQualificationMethod.EXACT_SNAPSHOT_V1
    )
    assert report.case_attestation_bundle_fingerprint is not None


async def test_snapshot_scorer_compares_real_semantic_signatures(
    tmp_path: Path,
) -> None:
    source_text = (
        "Harness A failed. Harness B recovered. Human ranking B > A.\n"
    )
    source = tmp_path / "domain.md"
    source.write_text(source_text, encoding="utf-8")
    snapshot, _ = await asyncio.to_thread(_prepare, source)

    correct = _snapshot_expectation_case(
        snapshot,
        source_text=source_text,
    )
    correct_outcome = _qualification_outcome_from_snapshot(
        correct,
        snapshot,
    )
    claim_label = next(iter(correct.citation_spans))
    assert (
        correct_outcome.citation_spans[claim_label]
        == correct.citation_spans[claim_label]
    )
    assert correct_outcome.entity_links == correct.entity_links

    wrong_citation = _snapshot_expectation_case(
        snapshot,
        source_text=source_text,
        wrong_citation=True,
    )
    citation_outcome = _qualification_outcome_from_snapshot(
        wrong_citation,
        snapshot,
    )
    assert (
        citation_outcome.citation_spans[claim_label]
        != wrong_citation.citation_spans[claim_label]
    )

    reversed_direction = _snapshot_expectation_case(
        snapshot,
        source_text=source_text,
        reverse_preference=True,
    )
    direction_outcome = _qualification_outcome_from_snapshot(
        reversed_direction,
        snapshot,
    )
    assert direction_outcome.citation_spans[claim_label] == (
        "missing-span",
    )

    wrong_entity = _snapshot_expectation_case(
        snapshot,
        source_text=source_text,
        wrong_entity=True,
    )
    entity_outcome = _qualification_outcome_from_snapshot(
        wrong_entity,
        snapshot,
    )
    assert entity_outcome.entity_links["mention-reviewer"] == (
        "missing-entity"
    )

    extra_conflict = EvidenceConflictV1(
        conflict_id="extra-entity-ambiguity",
        kind=EvidenceConflictKind.ENTITY_AMBIGUITY,
        claim_ids=tuple(
            item.claim_id for item in snapshot.evidence_graph.claims[:2]
        ),
        comparison_unit=snapshot.semantic_cases[0].task_entity_id,
        status=EvidenceConflictStatus.INFORMATIONAL,
    )
    snapshot_with_extra_conflict = SimpleNamespace(
        **{
            **snapshot.__dict__,
            "evidence_graph": replace(
                snapshot.evidence_graph,
                conflicts=(extra_conflict,),
            ),
        }
    )
    conflict_outcome = _qualification_outcome_from_snapshot(
        correct,
        snapshot_with_extra_conflict,
    )
    assert conflict_outcome.detected_conflict_ids == (
        "unexpected-conflict-0",
    )


async def test_exact_snapshot_report_is_production_allowlist_eligible(
    tmp_path: Path,
) -> None:
    source_text = (
        "Harness A failed. Harness B recovered. Human ranking B > A.\n"
    )
    source = tmp_path / "domain.md"
    source.write_text(source_text, encoding="utf-8")
    snapshot, _ = await asyncio.to_thread(_prepare, source)
    case = _complete_snapshot_expectation_case(
        snapshot,
        source_text=source_text,
    )
    corpus = SemanticQualificationCorpusV1(cases=(case,))

    async def snapshot_runner(source_input):
        assert dict(source_input.source_documents) == {
            "domain.md": source_text
        }
        return snapshot

    report = await run_semantic_snapshot_model_qualification(
        corpus,
        SemanticQualificationSnapshotDeploymentRunnerV1(
            model_profile_fingerprint=(
                snapshot.semantic_model_profile_fingerprint
            ),
            provider_fingerprint=(
                snapshot.semantic_provider_fingerprint
            ),
            semantic_protocol_fingerprint=(
                snapshot.semantic_protocol_fingerprint
            ),
            constitution_fingerprint=(
                snapshot.constitution.fingerprint
            ),
            snapshot_runner=snapshot_runner,
        ),
        issued_at_utc="2026-07-01T00:00:00Z",
        expires_at_utc="2027-07-01T00:00:00Z",
    )
    registry = SemanticQualificationRegistryV1(
        trusted_report_fingerprints=(report.report_fingerprint,)
    )

    assert report.status is QualificationStatus.QUALIFIED, dict(
        report.metric_values
    )
    assert all(value == 1.0 for value in report.metric_values.values())
    assert report.qualification_method is (
        SemanticQualificationMethod.EXACT_SNAPSHOT_V1
    )
    assert report.case_attestation_bundle_fingerprint is not None
    assert registry.accepts(
        report,
        model_profile_fingerprint=(
            snapshot.semantic_model_profile_fingerprint
        ),
        provider_fingerprint=(
            snapshot.semantic_provider_fingerprint
        ),
        semantic_protocol_fingerprint=(
            snapshot.semantic_protocol_fingerprint
        ),
        constitution_fingerprint=snapshot.constitution.fingerprint,
        corpus_fingerprint=corpus.corpus_fingerprint,
        threshold_set_fingerprint=(
            framework_semantic_qualification_thresholds_v1()
            .threshold_set_fingerprint
        ),
        evaluated_at_utc="2026-07-27T00:00:00Z",
    )


def test_recorded_perfect_outcomes_are_not_production_allowlist_eligible() -> None:
    corpus = load_semantic_qualification_corpus(_FIXTURE)
    outcomes = _perfect_outcomes(corpus)

    report = evaluate_semantic_model_qualification(
        corpus,
        outcomes,
        **_deployment(),
    )
    thresholds = framework_semantic_qualification_thresholds_v1()
    registry = SemanticQualificationRegistryV1(
        trusted_report_fingerprints=(report.report_fingerprint,)
    )

    assert report.status is QualificationStatus.QUALIFIED
    assert set(report.metric_values) == QUALIFICATION_METRIC_KEYS
    assert set(report.required_thresholds) == QUALIFICATION_METRIC_KEYS
    assert all(value == 1.0 for value in report.metric_values.values())
    assert report.false_authority_elevation_count == 0
    assert report.qualification_method is (
        SemanticQualificationMethod.RECORDED_OUTCOMES_V1
    )
    assert report.case_attestation_bundle_fingerprint is None
    assert report.corpus_fingerprint == corpus.corpus_fingerprint
    assert (
        report.threshold_set_fingerprint
        == thresholds.threshold_set_fingerprint
    )
    assert SemanticQualificationOutcomeSetV1.from_dict(
        outcomes.to_dict()
    ) == outcomes
    assert not registry.accepts(
        report,
        corpus_fingerprint=corpus.corpus_fingerprint,
        threshold_set_fingerprint=(
            thresholds.threshold_set_fingerprint
        ),
        **_deployment(),
    )


def test_metric_threshold_failure_issues_failed_report() -> None:
    corpus = load_semantic_qualification_corpus(_FIXTURE)
    outcomes = _perfect_outcomes(corpus)
    case_id = "generic-english-prose-comparison"
    original = next(
        outcome for outcome in outcomes.outcomes
        if outcome.case_id == case_id
    )
    actionability = dict(original.signal_actionability)
    actionability["signal-preference-delta"] = (
        SignalActionability.BLOCKED
    )
    outcomes = _replace_outcome(
        outcomes,
        case_id,
        signal_actionability=actionability,
    )

    report = evaluate_semantic_model_qualification(
        corpus,
        outcomes,
        **_deployment(),
    )

    assert report.status is QualificationStatus.FAILED
    assert (
        report.metric_values[SIGNAL_ACTIONABILITY_ACCURACY]
        < report.required_thresholds[SIGNAL_ACTIONABILITY_ACCURACY]
    )
    assert report.false_authority_elevation_count == 0


def test_false_authority_elevation_is_an_independent_hard_failure() -> None:
    corpus = load_semantic_qualification_corpus(_FIXTURE)
    outcomes = _replace_outcome(
        _perfect_outcomes(corpus),
        "generic-english-prose-comparison",
        elevated_authority_claim_ids=("claim-ranking",),
    )

    report = evaluate_semantic_model_qualification(
        corpus,
        outcomes,
        **_deployment(),
    )

    assert all(value == 1.0 for value in report.metric_values.values())
    assert report.false_authority_elevation_count == 1
    assert report.status is QualificationStatus.FAILED


def test_false_positive_conflict_fails_precision_threshold() -> None:
    corpus = load_semantic_qualification_corpus(_FIXTURE)
    outcomes = _perfect_outcomes(corpus)
    case_id = "generic-english-prose-comparison"
    original = next(
        outcome
        for outcome in outcomes.outcomes
        if outcome.case_id == case_id
    )
    outcomes = _replace_outcome(
        outcomes,
        case_id,
        detected_conflict_ids=(
            *original.detected_conflict_ids,
            "conflict-false-positive",
        ),
    )

    report = evaluate_semantic_model_qualification(
        corpus,
        outcomes,
        **_deployment(),
    )

    assert report.status is QualificationStatus.FAILED
    assert report.metric_values[
        "conflict_detection_precision"
    ] < 1.0


def test_unexpected_accepted_claim_fails_precision_threshold() -> None:
    corpus = load_semantic_qualification_corpus(_FIXTURE)
    outcomes = _perfect_outcomes(corpus)
    case_id = "generic-english-prose-comparison"
    outcomes = _replace_outcome(
        outcomes,
        case_id,
        unexpected_accepted_claim_count=1,
    )

    report = evaluate_semantic_model_qualification(
        corpus,
        outcomes,
        **_deployment(),
    )

    assert report.status is QualificationStatus.FAILED
    assert report.metric_values["accepted_claim_precision"] < 0.98


def test_qualification_requires_an_unexpired_report_window() -> None:
    corpus = load_semantic_qualification_corpus(_FIXTURE)
    recorded_report = evaluate_semantic_model_qualification(
        corpus,
        _perfect_outcomes(corpus),
        issued_at_utc="2026-01-01T00:00:00Z",
        expires_at_utc="2026-02-01T00:00:00Z",
        **_deployment(),
    )
    report = replace(
        recorded_report,
        qualification_method=(
            SemanticQualificationMethod.EXACT_SNAPSHOT_V1
        ),
        runner_protocol_fingerprint=(
            SEMANTIC_EXACT_SNAPSHOT_RUNNER_PROTOCOL_FINGERPRINT_V1
        ),
        case_attestation_bundle_fingerprint=_fingerprint(
            "case-attestation-bundle"
        ),
    )
    registry = SemanticQualificationRegistryV1(
        trusted_report_fingerprints=(report.report_fingerprint,)
    )
    bindings = {
        **_deployment(),
        "corpus_fingerprint": corpus.corpus_fingerprint,
        "threshold_set_fingerprint": (
            framework_semantic_qualification_thresholds_v1()
            .threshold_set_fingerprint
        ),
    }

    assert registry.accepts(
        report,
        evaluated_at_utc="2026-01-15T00:00:00Z",
        **bindings,
    )
    assert not registry.accepts(
        report,
        evaluated_at_utc="2026-02-01T00:00:00Z",
        **bindings,
    )
    expired = replace(report, status=QualificationStatus.EXPIRED)
    assert not SemanticQualificationRegistryV1(
        trusted_report_fingerprints=(expired.report_fingerprint,)
    ).accepts(
        expired,
        evaluated_at_utc="2026-01-15T00:00:00Z",
        **bindings,
    )


def test_corpus_rejects_schema_drift_duplicates_and_fingerprint_tamper() -> None:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    drifted = dict(payload)
    drifted["provider_config"] = {"token": "must-not-be-accepted"}
    with pytest.raises(
        SemanticQualificationContractError,
        match="keys drifted",
    ):
        SemanticQualificationCorpusV1.from_dict(drifted)

    duplicated = dict(payload)
    duplicated["cases"] = [
        *payload["cases"],
        dict(payload["cases"][0]),
    ]
    with pytest.raises(
        SemanticQualificationContractError,
        match="case ids must be unique",
    ):
        SemanticQualificationCorpusV1.from_dict(duplicated)

    tampered = json.loads(json.dumps(payload))
    tampered["cases"][0]["scenario_tags"].append("paraphrase")
    with pytest.raises(
        SemanticQualificationContractError,
        match="corpus fingerprint mismatch",
    ):
        SemanticQualificationCorpusV1.from_dict(tampered)


def test_threshold_and_report_validation_reject_unknown_or_nan_metrics() -> None:
    thresholds = framework_semantic_qualification_thresholds_v1()
    unknown = thresholds.to_dict()
    unknown["metric_thresholds"]["unknown_metric"] = 0.5
    with pytest.raises(
        SemanticQualificationContractError,
        match="metric keys",
    ):
        SemanticQualificationThresholdSetV1.from_dict(unknown)

    invalid = thresholds.to_dict()
    invalid["metric_thresholds"][
        SIGNAL_ACTIONABILITY_ACCURACY
    ] = float("nan")
    with pytest.raises(
        SemanticQualificationContractError,
        match="finite numbers",
    ):
        SemanticQualificationThresholdSetV1.from_dict(invalid)

    corpus = load_semantic_qualification_corpus(_FIXTURE)
    report = evaluate_semantic_model_qualification(
        corpus,
        _perfect_outcomes(corpus),
        **_deployment(),
    )
    metrics = dict(report.metric_values)
    metrics["unknown_metric"] = 1.0
    with pytest.raises(
        SemanticQualificationContractError,
        match="metric keys",
    ):
        validate_semantic_model_qualification_report(
            replace(report, metric_values=metrics),
            corpus=corpus,
        )


def test_evaluator_rejects_missing_cases_and_partial_dimension_outcomes() -> None:
    corpus = load_semantic_qualification_corpus(_FIXTURE)
    outcomes = _perfect_outcomes(corpus)

    missing_case = SemanticQualificationOutcomeSetV1(
        outcomes=outcomes.outcomes[:-1]
    )
    with pytest.raises(
        SemanticQualificationContractError,
        match="exactly one result",
    ):
        evaluate_semantic_model_qualification(
            corpus,
            missing_case,
            **_deployment(),
        )

    case_id = "generic-chinese-table-log"
    original = next(
        outcome for outcome in outcomes.outcomes
        if outcome.case_id == case_id
    )
    dispositions = dict(original.source_unit_dispositions)
    dispositions.pop(next(iter(dispositions)))
    partial = _replace_outcome(
        outcomes,
        case_id,
        source_unit_dispositions=dispositions,
    )
    with pytest.raises(
        SemanticQualificationContractError,
        match="human-labeled source_unit_dispositions",
    ):
        evaluate_semantic_model_qualification(
            corpus,
            partial,
            **_deployment(),
        )


def test_report_validation_rejects_corpus_and_threshold_binding_drift() -> None:
    corpus = load_semantic_qualification_corpus(_FIXTURE)
    report = evaluate_semantic_model_qualification(
        corpus,
        _perfect_outcomes(corpus),
        **_deployment(),
    )
    changed_case = replace(
        corpus.cases[0],
        scenario_tags=(*corpus.cases[0].scenario_tags, "paraphrase"),
    )
    changed_corpus = replace(
        corpus,
        cases=(changed_case, *corpus.cases[1:]),
    )

    with pytest.raises(
        SemanticQualificationContractError,
        match="different corpus",
    ):
        validate_semantic_model_qualification_report(
            report,
            corpus=changed_corpus,
        )

    with pytest.raises(
        SemanticQualificationContractError,
        match="fixed v1 thresholds",
    ):
        validate_semantic_model_qualification_report(
            replace(
                report,
                threshold_set_fingerprint=_fingerprint(
                    "threshold-drift"
                ),
            ),
            corpus=corpus,
        )


@pytest.mark.parametrize(
    ("active_field", "drifted_value"),
    [
        ("model_profile_fingerprint", _fingerprint("other-model")),
        ("provider_fingerprint", _fingerprint("other-provider")),
        (
            "semantic_protocol_fingerprint",
            _fingerprint("other-protocol"),
        ),
        (
            "constitution_fingerprint",
            _fingerprint("other-constitution"),
        ),
        ("corpus_fingerprint", _fingerprint("other-corpus")),
        (
            "threshold_set_fingerprint",
            _fingerprint("other-thresholds"),
        ),
    ],
)
def test_registry_rejects_every_active_deployment_binding_drift(
    active_field: str,
    drifted_value: str,
) -> None:
    corpus = load_semantic_qualification_corpus(_FIXTURE)
    report = evaluate_semantic_model_qualification(
        corpus,
        _perfect_outcomes(corpus),
        **_deployment(),
    )
    registry = SemanticQualificationRegistryV1(
        trusted_report_fingerprints=(report.report_fingerprint,)
    )
    active = {
        **_deployment(),
        "corpus_fingerprint": corpus.corpus_fingerprint,
        "threshold_set_fingerprint": (
            framework_semantic_qualification_thresholds_v1()
            .threshold_set_fingerprint
        ),
    }
    active[active_field] = drifted_value

    assert not registry.accepts(report, **active)


def test_report_payload_fingerprint_tamper_remains_fail_closed() -> None:
    corpus = load_semantic_qualification_corpus(_FIXTURE)
    report = evaluate_semantic_model_qualification(
        corpus,
        _perfect_outcomes(corpus),
        **_deployment(),
    )
    payload = report.to_dict()
    payload["provider_fingerprint"] = _fingerprint("tampered-provider")

    with pytest.raises(ValueError, match="fingerprint mismatch"):
        SemanticModelQualificationReportV1.from_dict(payload)


def test_workspace_report_and_registry_loaders_are_bounded_and_fail_closed(
    tmp_path: Path,
) -> None:
    corpus = load_semantic_qualification_corpus(_FIXTURE)
    report = evaluate_semantic_model_qualification(
        corpus,
        _perfect_outcomes(corpus),
        **_deployment(),
    )
    registry = SemanticQualificationRegistryV1(
        trusted_report_fingerprints=(report.report_fingerprint,)
    )
    report_path = tmp_path / "report.json"
    registry_path = tmp_path / "index.json"
    report_path.write_text(
        json.dumps(report.to_dict()),
        encoding="utf-8",
    )
    registry_path.write_text(
        json.dumps(registry.to_dict()),
        encoding="utf-8",
    )

    assert load_semantic_model_qualification_report(report_path) == report
    assert load_semantic_qualification_registry(registry_path) == registry

    drifted_report = tmp_path / "drifted-report.json"
    drifted_report_payload = report.to_dict()
    drifted_report_payload["provider_config"] = {"token": "forbidden"}
    drifted_report.write_text(
        json.dumps(drifted_report_payload),
        encoding="utf-8",
    )
    with pytest.raises(
        SemanticQualificationContractError,
        match="keys drifted",
    ):
        load_semantic_model_qualification_report(drifted_report)

    missing_report_fingerprint = tmp_path / "unsigned-report.json"
    missing_report_payload = report.to_dict()
    missing_report_payload.pop("report_fingerprint")
    missing_report_fingerprint.write_text(
        json.dumps(missing_report_payload),
        encoding="utf-8",
    )
    with pytest.raises(
        SemanticQualificationContractError,
        match="keys drifted",
    ):
        load_semantic_model_qualification_report(
            missing_report_fingerprint
        )

    drifted_registry = tmp_path / "drifted-index.json"
    drifted_registry_payload = registry.to_dict()
    drifted_registry_payload["trusted_provider"] = "self-asserted"
    drifted_registry.write_text(
        json.dumps(drifted_registry_payload),
        encoding="utf-8",
    )
    with pytest.raises(
        SemanticQualificationContractError,
        match="keys drifted",
    ):
        load_semantic_qualification_registry(drifted_registry)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":"x","schema_version":"y"}',
        encoding="utf-8",
    )
    with pytest.raises(
        SemanticQualificationContractError,
        match="duplicate",
    ):
        load_semantic_qualification_registry(duplicate)

    symlink = tmp_path / "report-link.json"
    symlink.symlink_to(report_path)
    with pytest.raises(
        SemanticQualificationContractError,
        match="non-symlink",
    ):
        load_semantic_model_qualification_report(symlink)
