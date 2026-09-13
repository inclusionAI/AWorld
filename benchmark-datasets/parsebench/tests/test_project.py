from __future__ import annotations

import json
import os
from dataclasses import replace
from hashlib import sha1, sha256
from pathlib import Path

import tomllib
import yaml

from parsebench_dataset.contracts import PINNED_PARSEBENCH_CONTRACT
from parsebench_dataset.project import materialize_project


def _row(
    pdf: str,
    category: str,
    rule_id: str,
    rule_type: str,
    *,
    rule: dict[str, object] | None = None,
    page: int | None = None,
    expected_markdown: str | None = None,
) -> dict[str, object]:
    return {
        "pdf": pdf,
        "category": category,
        "id": rule_id,
        "type": rule_type,
        "rule": json.dumps(rule or {}, sort_keys=True, separators=(",", ":")),
        "page": page,
        "expected_markdown": expected_markdown,
        "tags": [],
    }


def _write_jsonl(path: Path, row: dict[str, object]) -> None:
    path.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def _fixture(tmp_path: Path) -> tuple[Path, object]:
    exported = tmp_path / "exported"
    resources = {
        "docs/chart/chart.pdf": b"%PDF chart",
        "docs/layout/layout.png": b"png-layout",
        "docs/table/table.pdf": b"%PDF table",
        "docs/text/shared.pdf": b"%PDF text",
    }
    for relative, content in resources.items():
        target = exported / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    rows = {
        "chart.jsonl": _row(
            "docs/chart/chart.pdf",
            "chart",
            "chart-rule",
            "chart_data_point",
            rule={"labels": ["Revenue"], "value": "private"},
        ),
        "layout.jsonl": _row(
            "docs/layout/layout.png",
            "layout",
            "layout-rule",
            "layout",
            rule={
                "bbox": [0.1, 0.2, 0.3, 0.4],
                "canonical_class": "Text",
                "content": {"type": "text", "text": "private"},
            },
            page=1,
        ),
        "table.jsonl": _row(
            "docs/table/table.pdf",
            "table",
            "table-rule",
            "expected_markdown",
            expected_markdown="<table><tr><td>private</td></tr></table>",
        ),
        "text_content.jsonl": _row(
            "docs/text/shared.pdf",
            "text_content",
            "content-rule",
            "missing_specific_word",
            rule={"word": "private"},
        ),
        "text_formatting.jsonl": _row(
            "docs/text/shared.pdf",
            "text_formatting",
            "formatting-rule",
            "is_bold",
            rule={"text": "private"},
        ),
    }
    for relative, row in rows.items():
        _write_jsonl(exported / relative, row)

    cache = tmp_path / "datasets--llamaindex--ParseBench"
    blobs = cache / "blobs"
    snapshot = cache / "snapshots" / PINNED_PARSEBENCH_CONTRACT.dataset_revision
    blobs.mkdir(parents=True)
    for source in sorted(path for path in exported.rglob("*") if path.is_file()):
        content = source.read_bytes()
        if source.suffix == ".jsonl":
            digest = sha1(
                f"blob {len(content)}\0".encode(), usedforsecurity=False
            )
            digest.update(content)
            blob_name = digest.hexdigest()
        else:
            blob_name = sha256(content).hexdigest()
        blob = blobs / blob_name
        blob.write_bytes(content)
        destination = snapshot / source.relative_to(exported)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.path.relpath(blob, destination.parent))
    logical_paths = set(rows) | set(resources)
    material_digests = tuple(
        (
            relative,
            (snapshot / relative).stat().st_size,
            "sha256:" + sha256((snapshot / relative).read_bytes()).hexdigest(),
        )
        for relative in sorted(logical_paths)
    )
    one_each = tuple(
        (dimension, 1)
        for dimension, _relative in PINNED_PARSEBENCH_CONTRACT.source_files
    )
    contract = replace(
        PINNED_PARSEBENCH_CONTRACT,
        rule_counts=one_each,
        dimension_execution_counts=one_each,
        unique_execution_count=4,
        material_digests=material_digests,
    )
    return snapshot, contract


def test_materializes_stock_lingguang_project(tmp_path: Path) -> None:
    source, contract = _fixture(tmp_path)
    output = tmp_path / "project"

    materialize_project(
        source,
        output,
        runtime_image="registry.example/aworld-filex@sha256:" + "4" * 64,
        runtime_service="aworld-runtime-test",
        gateway_base_url="http://127.0.0.1:8100",
        model_name="default__gemini-3.1-pro-preview",
        smoke_per_dimension=1,
        smoke_seed="project-v1",
        contract=contract,
    )

    config = tomllib.loads((output / "bench.toml").read_text())
    metadata = yaml.safe_load((output / "dataset/dataset.yaml").read_text())
    samples = (output / "dataset/samples.jsonl").read_text().splitlines()
    assert config["dataset"]["package_mode"] == "executable"
    assert config["execution"]["agent"] == "aworld"
    assert config["capabilities"]["skills"] == ["filex"]
    assert metadata["agent_dataset"]["params"]["required_skill"] == "filex"
    assert len(samples) == 4
    assert len(list((output / "tasks").iterdir())) == 4
