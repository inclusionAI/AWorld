from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from aworld.self_evolve.constitution import (
    AgenticRole,
    AgenticStageStatus,
    SelfEvolveStage,
    default_self_evolve_constitution,
)
from aworld.self_evolve.evidence import (
    SemanticSourceDispositionKind,
    SemanticSourceDispositionV1,
)
from aworld.self_evolve.ingestion.chunking import build_source_bundle
from aworld.self_evolve.ingestion.scanner import scan_source
from aworld.self_evolve.ingestion.semantic_workflow import (
    SEMANTIC_AGENT_CANDIDATE_SCHEMA_VERSION,
    BoundedSemanticStageExecutor,
    SemanticAgentBindingV1,
    SemanticProviderResponseV1,
    SemanticStageDecisionV1,
    evidence_source_span_from_chunk,
    validate_evidence_graph_against_source_bundle,
)
from aworld.self_evolve.ingestion.types import IngestionContractError
from tests.self_evolve.test_semantic_evidence import _graph


def _fingerprint(character: str) -> str:
    return "sha256:" + character * 64


def _binding(
    provider,
    *,
    character: str,
    group: str,
) -> SemanticAgentBindingV1:
    return SemanticAgentBindingV1(
        role=AgenticRole.SOURCE_UNDERSTANDING,
        provider_fingerprint=_fingerprint(character),
        model_fingerprint=_fingerprint(character),
        protocol_fingerprint=_fingerprint(character),
        independence_group=group,
        provider=provider,
    )


def _provider(
    summary: str,
    *,
    payload_extra: dict | None = None,
    raw_envelope: bool = False,
):
    def invoke(prompt: str, **kwargs):
        payload = {
            "schema_version": SEMANTIC_AGENT_CANDIDATE_SCHEMA_VERSION,
            "stage": SelfEvolveStage.UNDERSTAND.value,
            "artifact_schema_versions": [
                "aworld.self_evolve.source_understanding.v1"
            ],
            "payload": {
                "summary": summary,
                **(payload_extra or {}),
            },
        }
        if raw_envelope:
            return payload
        return SemanticProviderResponseV1(
            content=payload,
            input_token_count=10,
            output_token_count=5,
        )

    return invoke


@pytest.mark.asyncio
async def test_bounded_executor_uses_independent_candidates_and_one_report() -> None:
    constitution = default_self_evolve_constitution()

    def validate(candidates):
        return SemanticStageDecisionV1(
            stage=SelfEvolveStage.UNDERSTAND,
            accepted_candidate_ids=tuple(
                item.candidate_id for item in candidates
            ),
            output_fingerprints=(_fingerprint("9"),),
            output_schema_versions=(
                "aworld.self_evolve.source_understanding.v1",
            ),
            status=AgenticStageStatus.COMPLETE,
            reason_codes=("consensus_reached",),
        )

    execution = await BoundedSemanticStageExecutor(
        constitution
    ).execute(
        SelfEvolveStage.UNDERSTAND,
        input_fingerprints=(_fingerprint("8"),),
        source_data={"source_bundle": {"items": ["untrusted"]}},
        bindings=(
            _binding(
                _provider("candidate a"),
                character="1",
                group="provider-a",
            ),
            _binding(
                _provider("candidate b"),
                character="2",
                group="provider-b",
            ),
        ),
        validator=validate,
    )

    assert len(execution.candidates) == 2
    assert len(execution.reports) == 1
    assert execution.reports[0].status is AgenticStageStatus.COMPLETE
    assert execution.reports[0].model_call_count == 2
    assert execution.reports[0].token_count == 30
    assert execution.accepted_reports == execution.reports
    with pytest.raises(TypeError):
        execution.candidates[0].payload["summary"] = "mutated"


@pytest.mark.asyncio
async def test_executor_rejects_nonindependent_or_unmeasured_candidates() -> None:
    constitution = default_self_evolve_constitution()

    with pytest.raises(
        IngestionContractError,
        match="distinct independence groups",
    ):
        await BoundedSemanticStageExecutor(constitution).execute(
            SelfEvolveStage.UNDERSTAND,
            input_fingerprints=(_fingerprint("8"),),
            source_data={"source_bundle": {}},
            bindings=(
                _binding(
                    _provider("a"),
                    character="1",
                    group="same",
                ),
                _binding(
                    _provider("b"),
                    character="2",
                    group="same",
                ),
            ),
            validator=lambda candidates: None,
        )

    with pytest.raises(
        IngestionContractError,
        match="too few valid semantic candidates",
    ):
        await BoundedSemanticStageExecutor(constitution).execute(
            SelfEvolveStage.UNDERSTAND,
            input_fingerprints=(_fingerprint("8"),),
            source_data={"source_bundle": {}},
            bindings=(
                _binding(
                    _provider("a", raw_envelope=True),
                    character="1",
                    group="provider-a",
                ),
                _binding(
                    _provider("b", raw_envelope=True),
                    character="2",
                    group="provider-b",
                ),
            ),
            validator=lambda candidates: None,
        )


@pytest.mark.asyncio
async def test_agent_cannot_emit_nested_control_plane_fields() -> None:
    constitution = default_self_evolve_constitution()

    with pytest.raises(
        IngestionContractError,
        match="too few valid semantic candidates",
    ):
        await BoundedSemanticStageExecutor(constitution).execute(
            SelfEvolveStage.UNDERSTAND,
            input_fingerprints=(_fingerprint("8"),),
            source_data={"source_bundle": {}},
            bindings=(
                _binding(
                    _provider(
                        "a",
                        payload_extra={
                            "nested": {
                                "rollout_stage": "verified",
                            }
                        },
                    ),
                    character="1",
                    group="provider-a",
                ),
                _binding(
                    _provider("b"),
                    character="2",
                    group="provider-b",
                ),
            ),
            validator=lambda candidates: None,
        )


def test_chunk_span_factory_and_bundle_graph_validation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "comparison.md"
    source.write_text("Harness B > Harness A\n", encoding="utf-8")
    inventory = scan_source(source)
    bundle = build_source_bundle(source, inventory=inventory)
    chunk = bundle.chunks[0]
    span = evidence_source_span_from_chunk(
        chunk,
        span_id="span-1",
    )
    original = _graph()
    graph = replace(
        original,
        spans=(span,),
        source_dispositions=tuple(
            SemanticSourceDispositionV1(
                source_unit_id=source_unit_id,
                disposition=SemanticSourceDispositionKind.EVIDENCE,
                claim_ids=("claim-comparison",),
                reason_codes=("comparison_evidence",),
                auditor_verification_id="coverage-report-1",
            )
            for source_unit_id in bundle.source_unit_ids
        ),
    )

    result = validate_evidence_graph_against_source_bundle(
        bundle,
        graph,
    )
    assert result.valid is True

    omitted = replace(
        graph,
        source_dispositions=graph.source_dispositions[:-1],
    )
    assert (
        validate_evidence_graph_against_source_bundle(
            bundle,
            omitted,
        ).unexplained_source_unit_count
        == 1
    )

    wrong_line = replace(
        span,
        line_start=span.line_start + 1,
        line_end=span.line_end + 1,
    )
    invalid = replace(graph, spans=(wrong_line,))
    assert (
        validate_evidence_graph_against_source_bundle(
            bundle,
            invalid,
        ).invalid_source_span_count
        == 1
    )


def test_span_factory_rejects_partial_utf8_codepoint(tmp_path: Path) -> None:
    source = tmp_path / "utf8.md"
    source.write_text("恢复", encoding="utf-8")
    bundle = build_source_bundle(
        source,
        inventory=scan_source(source),
    )

    with pytest.raises(
        IngestionContractError,
        match="UTF-8 boundaries",
    ):
        evidence_source_span_from_chunk(
            bundle.chunks[0],
            span_id="span-invalid",
            local_byte_start=1,
        )
