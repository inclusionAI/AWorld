from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aworld.self_evolve.constitution import (
    default_self_evolve_constitution,
)
from aworld.self_evolve.evaluation_plan import QualificationStatus
from aworld.self_evolve.ingestion import AgenticDatasetIngestor
from aworld.self_evolve.ingestion.types import (
    DatasetIngestionRequest,
    IngestionMode,
)
from aworld.self_evolve.runner import (
    _IngestionSemanticModelProvider,
)
from aworld.self_evolve.semantic_qualification import (
    SemanticQualificationSnapshotDeploymentRunnerV1,
    load_semantic_qualification_corpus,
    run_semantic_snapshot_model_qualification,
)


pytestmark = pytest.mark.semantic_model_live

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "semantic_ingestion"
    / "qualification_corpus.json"
)


@pytest.mark.requires_llm
async def test_exact_semantic_deployment_qualification(
    tmp_path: Path,
) -> None:
    profile = os.environ.get("AWORLD_SEMANTIC_MODEL_PROFILE")
    if not profile:
        pytest.skip("AWORLD_SEMANTIC_MODEL_PROFILE is not configured")

    from aworld_cli.core.model_profiles import resolve_model_profile

    model_config = resolve_model_profile(profile)
    semantic_provider = _IngestionSemanticModelProvider(
        model_config=model_config
    )
    corpus = load_semantic_qualification_corpus(_FIXTURE)

    async def snapshot_runner(source_input):
        source_root = tmp_path / source_input.run_token
        source_root.mkdir()
        for relative_path, content in (
            source_input.source_documents.items()
        ):
            target = source_root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return await AgenticDatasetIngestor(
            semantic_provider=semantic_provider,
            semantic_provider_fingerprint=(
                semantic_provider.provider_fingerprint
            ),
            semantic_model_profile_fingerprint=(
                semantic_provider.model_profile_fingerprint
            ),
            semantic_protocol_fingerprint=(
                semantic_provider.protocol_fingerprint
            ),
        ).prepare(
            DatasetIngestionRequest(
                source_path=source_root,
                mode=IngestionMode.INGESTION_ONLY,
            )
        )

    report = await run_semantic_snapshot_model_qualification(
        corpus,
        SemanticQualificationSnapshotDeploymentRunnerV1(
            model_profile_fingerprint=(
                semantic_provider.model_profile_fingerprint
            ),
            provider_fingerprint=(
                semantic_provider.provider_fingerprint
            ),
            semantic_protocol_fingerprint=(
                semantic_provider.protocol_fingerprint
            ),
            constitution_fingerprint=(
                default_self_evolve_constitution().fingerprint
            ),
            snapshot_runner=snapshot_runner,
        ),
    )
    output_path = Path(
        os.environ.get(
            "AWORLD_SEMANTIC_QUALIFICATION_REPORT",
            tmp_path / "semantic-qualification-report.json",
        )
    )
    output_path.write_text(
        json.dumps(
            report.to_dict(),
            sort_keys=True,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    assert report.false_authority_elevation_count == 0
    assert report.status is QualificationStatus.QUALIFIED, (
        f"qualification failed; inspect public-safe report at {output_path}"
    )
