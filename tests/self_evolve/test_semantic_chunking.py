from __future__ import annotations

from pathlib import Path

import pytest

from aworld.self_evolve.ingestion.chunking import (
    SemanticChunkingLimits,
    SourceBundleV1,
    build_source_bundle,
)
from aworld.self_evolve.ingestion.scanner import scan_source
from aworld.self_evolve.ingestion.types import IngestionContractError


def _build(
    path: Path,
    *,
    limits: SemanticChunkingLimits | None = None,
) -> SourceBundleV1:
    inventory = scan_source(path)
    return build_source_bundle(
        path,
        inventory=inventory,
        chunking_limits=limits,
    )


def test_markdown_chunking_preserves_offsets_headings_and_fences(
    tmp_path: Path,
) -> None:
    source = tmp_path / "comparison.md"
    content = (
        "# Evaluation\n\n"
        "Harness A repeated a failed call.\n\n"
        "## Harness B\n\n"
        "Harness B recovered.\n\n"
        "```json\n"
        '{"winner": "B"}\n'
        "```\n"
    )
    source.write_text(content, encoding="utf-8")

    bundle = _build(source)
    raw = source.read_bytes()

    assert bundle.chunks
    assert any(
        chunk.heading_path == ("Evaluation", "Harness B")
        for chunk in bundle.chunks
    )
    assert any("winner" in chunk.raw_text for chunk in bundle.chunks)
    for chunk in bundle.chunks:
        assert (
            raw[chunk.byte_start : chunk.byte_end].decode("utf-8")
            == chunk.raw_text
        )
        assert chunk.line_start >= 1
        assert chunk.line_end >= chunk.line_start


def test_source_bundle_is_deterministic_and_round_trips(
    tmp_path: Path,
) -> None:
    (tmp_path / "notes.md").write_text(
        "# Ranking\n\nB > A\n",
        encoding="utf-8",
    )
    (tmp_path / "runs.json").write_text(
        '[{"harness": "A", "score": 0}, {"harness": "B", "score": 1}]',
        encoding="utf-8",
    )

    first = _build(tmp_path)
    second = _build(tmp_path)
    restored = SourceBundleV1.from_dict(first.to_dict())

    assert first.fingerprint == second.fingerprint
    assert restored == first
    assert restored.fingerprint == first.fingerprint
    assert len(first.source_unit_ids) == len(set(first.source_unit_ids))


def test_structured_records_and_leaf_fields_are_coverage_units(
    tmp_path: Path,
) -> None:
    source = tmp_path / "runs.json"
    source.write_text(
        '[{"harness": "A", "result": {"success": false}},'
        '{"harness": "B", "result": {"success": true}}]',
        encoding="utf-8",
    )

    bundle = _build(source)
    records = [
        unit
        for unit in bundle.structured_units
        if unit.unit_kind == "record"
    ]
    fields = [
        unit
        for unit in bundle.structured_units
        if unit.unit_kind == "field"
    ]

    assert len(records) == 2
    assert {unit.record_locator for unit in records} == {"$[0]", "$[1]"}
    assert {unit.field_path for unit in fields} == {
        "$/harness",
        "$/result/success",
    }
    assert {
        (unit.provenance_status, unit.provenance_reason)
        for unit in bundle.structured_units
    } == {
        (
            "unresolved",
            "structured_locator_without_physical_span",
        )
    }


def test_public_projection_excludes_private_content_and_absolute_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "secret-name.md"
    source.write_text("# Result\n\nB is better.\n", encoding="utf-8")

    projection = _build(source).public_projection()
    serialized = str(projection)

    assert "raw_text" not in serialized
    assert "'value':" not in serialized
    assert str(tmp_path) not in serialized
    assert "secret-name.md" in serialized


def test_utf8_long_blocks_are_bounded_with_complete_byte_coverage(
    tmp_path: Path,
) -> None:
    source = tmp_path / "long.md"
    source.write_text("# Long\n\n" + "恢复能力" * 100, encoding="utf-8")
    limits = SemanticChunkingLimits(
        max_chunk_bytes=64,
        overlap_bytes=8,
        max_semantic_prompt_bytes=100_000,
    )

    bundle = _build(source, limits=limits)
    content_chunks = [
        chunk for chunk in bundle.chunks if "恢" in chunk.raw_text
    ]
    covered: set[int] = set()
    for chunk in content_chunks:
        assert len(chunk.raw_text.encode("utf-8")) <= 64
        covered.update(range(chunk.byte_start, chunk.byte_end))
    paragraph_start = source.read_bytes().index("恢".encode("utf-8"))

    assert covered.issuperset(
        range(paragraph_start, len(source.read_bytes()))
    )


def test_semantic_prompt_limit_fails_closed_without_sampling(
    tmp_path: Path,
) -> None:
    source = tmp_path / "large.md"
    source.write_text("x" * 2_000, encoding="utf-8")

    with pytest.raises(
        IngestionContractError,
        match="exceeds its byte limit",
    ):
        _build(
            source,
            limits=SemanticChunkingLimits(
                max_chunk_bytes=256,
                overlap_bytes=16,
                max_semantic_prompt_bytes=512,
            ),
        )


def test_private_content_tampering_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "input.md"
    source.write_text("Harness B > Harness A\n", encoding="utf-8")
    payload = _build(source).to_dict()
    payload["chunks"][0]["raw_text"] = "tampered"

    with pytest.raises(
        IngestionContractError,
        match="fingerprint mismatch",
    ):
        SourceBundleV1.from_dict(payload)


def test_structured_expansion_is_bounded_by_unit_count_and_depth(
    tmp_path: Path,
) -> None:
    many = tmp_path / "many.json"
    many.write_text(
        '{"a": 1, "b": 2, "c": 3}',
        encoding="utf-8",
    )
    with pytest.raises(
        IngestionContractError,
        match="source units exceed",
    ):
        _build(
            many,
            limits=SemanticChunkingLimits(
                max_source_units=2,
                max_semantic_prompt_bytes=100_000,
            ),
        )

    deep = tmp_path / "deep.json"
    deep.write_text(
        '{"a": {"b": {"c": 1}}}',
        encoding="utf-8",
    )
    with pytest.raises(
        IngestionContractError,
        match="nesting depth",
    ):
        _build(
            deep,
            limits=SemanticChunkingLimits(
                max_structure_depth=2,
                max_semantic_prompt_bytes=100_000,
            ),
        )


def test_source_bundle_reload_enforces_count_and_private_size_limits(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.md"
    source.write_text("x" * 2_000, encoding="utf-8")
    payload = _build(
        source,
        limits=SemanticChunkingLimits(
            max_chunk_bytes=256,
            overlap_bytes=16,
            max_semantic_prompt_bytes=100_000,
        ),
    ).to_dict()

    with pytest.raises(
        IngestionContractError,
        match="source units exceed",
    ):
        SourceBundleV1.from_dict(
            payload,
            limits=SemanticChunkingLimits(max_source_units=1),
        )
    with pytest.raises(
        IngestionContractError,
        match="exceeds its byte limit",
    ):
        SourceBundleV1.from_dict(
            payload,
            limits=SemanticChunkingLimits(
                max_semantic_prompt_bytes=512,
            ),
        )
