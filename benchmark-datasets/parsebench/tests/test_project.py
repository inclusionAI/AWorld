from __future__ import annotations

import json
import os
import subprocess
import sys
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
            digest = sha1(f"blob {len(content)}\0".encode(), usedforsecurity=False)
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
        model_name="ai_cloud_Kimi_k26_pgc",
        smoke_per_dimension=1,
        smoke_seed="project-v1",
        contract=contract,
    )

    config = tomllib.loads((output / "bench.toml").read_text())
    metadata = yaml.safe_load((output / "dataset/dataset.yaml").read_text())
    samples = (output / "dataset/samples.jsonl").read_text().splitlines()
    assert config["model"]["name"] == "ai_cloud_Kimi_k26_pgc"
    assert "model_profile" not in metadata["agent_dataset"]["params"]
    assert config["dataset"]["package_mode"] == "executable"
    assert config["execution"]["agent"] == "asap"
    assert "capabilities" not in config
    assert (
        not {"agent", "required_skill", "model_profile"}
        & metadata["agent_dataset"]["params"].keys()
    )
    assert len(samples) == 4
    assert len(list((output / "tasks").iterdir())) == 4
    instructions = [
        path.read_text(encoding="utf-8")
        for path in (output / "tasks").glob("*/instruction.md")
    ]
    assert instructions
    assert all("document.md" in text and "layout.json" in text for text in instructions)
    assert all(
        "FileX" not in text and "filex" not in text and "AWorld" not in text
        for text in instructions
    )


def test_local_mutable_image_is_explicitly_non_publishable(tmp_path: Path) -> None:
    source, contract = _fixture(tmp_path)
    output = tmp_path / "local-project"

    materialize_project(
        source,
        output,
        runtime_image="aworld-filex-parsebench:local",
        runtime_service="local-docker-sandbox",
        gateway_base_url="http://127.0.0.1:8100",
        model_name="default__gemini-3.1-pro-preview",
        smoke_per_dimension=1,
        smoke_seed="local-v1",
        allow_mutable_local_image=True,
        contract=contract,
    )

    rows = [
        json.loads(line)
        for line in (output / "dataset/samples.jsonl").read_text().splitlines()
    ]
    scope = rows[0]["benchmark_scope"]
    assert scope["publishable"] is False
    assert "mutable_runtime_image" in scope["non_publishable_reasons"]


def test_generated_verifier_loads_ground_truth_for_all_dimensions(
    tmp_path: Path,
) -> None:
    source, contract = _fixture(tmp_path)
    output = tmp_path / "project"
    materialize_project(
        source,
        output,
        runtime_image="aworld-filex-parsebench:local",
        runtime_service="local-docker-sandbox",
        gateway_base_url="http://127.0.0.1:8100",
        model_name="ai_cloud_Kimi_k26_pgc",
        smoke_per_dimension=1,
        smoke_seed="verifier-contract-v1",
        allow_mutable_local_image=True,
        contract=contract,
    )
    # Import only the emitted package in a fresh interpreter. Importing the
    # author's verifier would hide differences in the compact runtime contract.
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
ground_truth_files = sorted(root.glob("tasks/*/tests/ground_truth.json"))
runtime_root = ground_truth_files[0].parent
sys.path.insert(0, str(runtime_root))
from parsebench_runtime import verifier

assert Path(verifier.__file__).is_relative_to(runtime_root)
dimensions = set()
provenance = {}
for path in ground_truth_files:
    loaded = verifier._load_ground_truth(path)
    dimensions.update(dimension.value for dimension in loaded.dimensions)
    provenance.update({rule.dimension.value: rule.source_jsonl for rule in loaded.rules})
    if any(rule.dimension.value.startswith("text_") for rule in loaded.rules):
        payload = json.loads(path.read_bytes())
        for rule in payload["rules"]:
            if rule["dimension"].startswith("text_"):
                rule["provenance"]["source_jsonl"] = "text.jsonl"
        changed = path.with_name("incorrect-ground-truth.json")
        changed.write_text(json.dumps(payload))
        try:
            verifier._load_ground_truth(changed)
        except verifier.ParseBenchVerificationError as error:
            assert error.code == "ground_truth_contract_mismatch"
        else:
            raise AssertionError("unbound text provenance was accepted")
print(json.dumps({"dimensions": sorted(dimensions), "provenance": provenance,
                  "task_count": len(ground_truth_files)}))
""",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    checked = json.loads(probe.stdout)
    assert checked["task_count"] == 4
    assert checked["dimensions"] == sorted(
        dimension.value for dimension, _ in PINNED_PARSEBENCH_CONTRACT.source_files
    )
    assert checked["provenance"] == {
        dimension.value: filename
        for dimension, filename in PINNED_PARSEBENCH_CONTRACT.source_files
    }
