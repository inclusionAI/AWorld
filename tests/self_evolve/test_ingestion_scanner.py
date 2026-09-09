from __future__ import annotations

import os
from pathlib import Path

import pytest

from aworld.self_evolve.ingestion import (
    IngestionLimits,
    SourceScanError,
    SourceScanner,
    scan_source,
)


def test_same_logical_directory_has_location_independent_identity(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "records.json").write_text(
        '[{"input":"alpha"}]',
        encoding="utf-8",
    )
    (second / "records.json").write_text(
        '[{"input":"alpha"}]',
        encoding="utf-8",
    )

    left = scan_source(first)
    right = scan_source(second)

    assert left.source_root_fingerprint == right.source_root_fingerprint
    assert left.assets[0].asset_id == right.assets[0].asset_id


def test_directory_order_is_stable_and_hidden_build_assets_are_excluded(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "z.json").write_text('{"input":"z"}', encoding="utf-8")
    (root / "a.json").write_text('{"input":"a"}', encoding="utf-8")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "secret.json").write_text(
        '{"input":"secret"}',
        encoding="utf-8",
    )
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dep.json").write_text(
        '{"input":"dep"}',
        encoding="utf-8",
    )

    inventory = scan_source(root)

    assert [asset.relative_path for asset in inventory.assets] == [
        "a.json",
        "z.json",
    ]
    assert len(inventory.ignored_assets) == 2
    assert "secret" not in str(inventory.public_projection())


def test_explicit_symlink_is_rejected_and_internal_symlink_is_not_followed(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "records.json").write_text('{"input":"x"}', encoding="utf-8")
    explicit = tmp_path / "explicit"
    explicit.symlink_to(target, target_is_directory=True)

    with pytest.raises(SourceScanError) as error:
        scan_source(explicit)
    assert error.value.reason_code == "source_symlink_not_allowed"

    root = tmp_path / "root"
    root.mkdir()
    (root / "inside.json").write_text('{"input":"x"}', encoding="utf-8")
    (root / "outside").symlink_to(target, target_is_directory=True)
    inventory = scan_source(root)
    assert [asset.relative_path for asset in inventory.assets] == ["inside.json"]
    assert inventory.ignored_assets[0].reason_code == "internal_symlink_ignored"


def test_file_count_file_size_and_total_size_limits(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "a.txt").write_text("1234", encoding="utf-8")
    (root / "b.txt").write_text("5678", encoding="utf-8")

    with pytest.raises(SourceScanError) as count_error:
        SourceScanner(limits=IngestionLimits(max_files=1)).scan(root)
    assert count_error.value.reason_code == "source_limit_exceeded"

    inventory = SourceScanner(
        limits=IngestionLimits(
            max_file_bytes=3,
            max_total_bytes=8,
            max_asset_sample_bytes=3,
        )
    ).scan(root / "a.txt")
    assert inventory.rejected_assets[0].reason_code == "asset_size_limit_exceeded"

    with pytest.raises(SourceScanError) as total_error:
        SourceScanner(
            limits=IngestionLimits(
                max_file_bytes=4,
                max_total_bytes=7,
                max_asset_sample_bytes=4,
            )
        ).scan(root)
    assert total_error.value.reason_code == "source_limit_exceeded"


def test_binary_asset_is_explicitly_rejected(tmp_path: Path) -> None:
    path = tmp_path / "data.bin"
    path.write_bytes(b"\x00\x01\x02")

    inventory = scan_source(path)

    assert inventory.assets[0].extractor_name is None
    assert inventory.rejected_assets[0].reason_code == "unsupported_media_type"


def test_json_jsonl_csv_tsv_yaml_and_text_extractors_are_detected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    fixtures = {
        "records.json": '[{"input":"a"}]',
        "records.jsonl": '{"input":"a"}\n{"input":"b"}\n',
        "records.csv": "input,expected_output\na,b\n",
        "records.tsv": "input\texpected_output\na\tb\n",
        "records.yaml": "- input: a\n",
        "records.txt": "a request",
    }
    for name, content in fixtures.items():
        (root / name).write_text(content, encoding="utf-8")

    inventory = scan_source(root)

    assert len(inventory.assets) == len(fixtures)
    assert all(asset.extractor_name for asset in inventory.assets)
    assert {
        asset.structural_profile.get("root_type") for asset in inventory.assets
    } >= {"array", "records", "string"}


def test_source_mutation_between_scan_and_recheck_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "records.json"
    path.write_text('{"input":"before"}', encoding="utf-8")

    def mutate() -> None:
        path.write_text('{"input":"after"}', encoding="utf-8")

    with pytest.raises(SourceScanError) as error:
        SourceScanner().scan(path, before_recheck=mutate)
    assert error.value.reason_code == "source_changed_during_ingestion"


def test_new_directory_asset_during_scan_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "first.json").write_text('{"input":"a"}', encoding="utf-8")

    def add_asset() -> None:
        (root / "second.json").write_text('{"input":"b"}', encoding="utf-8")

    with pytest.raises(SourceScanError) as error:
        SourceScanner().scan(root, before_recheck=add_asset)
    assert error.value.reason_code == "source_changed_during_ingestion"
