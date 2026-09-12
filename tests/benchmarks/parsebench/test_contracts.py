from __future__ import annotations

import json
from hashlib import sha256

from aworld.benchmarks.parsebench.contracts import (
    DATASET_REVISION,
    EXPECTED_SOURCE_FIELDS,
    PARSEBENCH_TASK_FILENAME,
    PARSEBENCH_TASK_RUNTIME_PATH,
    PARSEBENCH_TASK_SCHEMA_VERSION,
    PINNED_FULL_SELECTION_MANIFEST_SHA256,
    PINNED_MATERIAL_MANIFEST_SHA256,
    PINNED_PARSEBENCH_CONTRACT,
    PINNED_PARSEBENCH_RUNTIME_IMAGE,
    SCORER_REVISION,
    SELECTION_MANIFEST_SCHEMA_VERSION,
    ParseBenchDimension,
)


def test_pinned_parsebench_contract_models_all_upstream_dimensions() -> None:
    assert DATASET_REVISION == "2805a1d940f95a203e0ae4b88be9934f7765b3fc"
    assert SCORER_REVISION == "34b73455032797754f6ed62e14c27a8b5423d11e"
    assert PARSEBENCH_TASK_SCHEMA_VERSION == "aworld-parsebench-task/v1"
    assert PARSEBENCH_TASK_FILENAME == "parsebench-task.json"
    assert PARSEBENCH_TASK_RUNTIME_PATH == "/workspace/parsebench-task.json"
    assert (
        SELECTION_MANIFEST_SCHEMA_VERSION == "aworld-parsebench-selection-manifest/v2"
    )
    assert (
        PINNED_FULL_SELECTION_MANIFEST_SHA256
        == "sha256:66fc68ad1f912ab3b5b03239ae76f4330ebcb801a1578a53913ff3231ab18f62"
    )
    assert PINNED_FULL_SELECTION_MANIFEST_SHA256 != "sha256:" + "0" * 64
    assert PINNED_PARSEBENCH_RUNTIME_IMAGE is None
    assert EXPECTED_SOURCE_FIELDS == frozenset(
        {
            "pdf",
            "category",
            "id",
            "type",
            "rule",
            "page",
            "expected_markdown",
            "tags",
        }
    )
    assert PINNED_PARSEBENCH_CONTRACT.source_files == (
        (ParseBenchDimension.CHART, "chart.jsonl"),
        (ParseBenchDimension.LAYOUT, "layout.jsonl"),
        (ParseBenchDimension.TABLE, "table.jsonl"),
        (ParseBenchDimension.TEXT_CONTENT, "text_content.jsonl"),
        (ParseBenchDimension.TEXT_FORMATTING, "text_formatting.jsonl"),
    )
    assert PINNED_PARSEBENCH_CONTRACT.rule_counts == (
        (ParseBenchDimension.CHART, 4_864),
        (ParseBenchDimension.LAYOUT, 16_325),
        (ParseBenchDimension.TABLE, 503),
        (ParseBenchDimension.TEXT_CONTENT, 141_322),
        (ParseBenchDimension.TEXT_FORMATTING, 5_997),
    )
    assert PINNED_PARSEBENCH_CONTRACT.dimension_execution_counts == (
        (ParseBenchDimension.CHART, 568),
        (ParseBenchDimension.LAYOUT, 500),
        (ParseBenchDimension.TABLE, 503),
        (ParseBenchDimension.TEXT_CONTENT, 506),
        (ParseBenchDimension.TEXT_FORMATTING, 476),
    )
    assert PINNED_PARSEBENCH_CONTRACT.total_rule_count == 169_011
    assert PINNED_PARSEBENCH_CONTRACT.unique_execution_count == 2_078
    material_digests = {
        path: (size, digest)
        for path, size, digest in PINNED_PARSEBENCH_CONTRACT.material_digests
    }
    assert len(material_digests) == 2_083
    assert sum(path.startswith("docs/") for path in material_digests) == 2_078
    assert material_digests["chart.jsonl"] == (
        1_591_287,
        "sha256:82eb2d660b286a5e1b8bd57f3f159a722b13849834561211dc1ccd5c5a39582b",
    )
    assert material_digests["layout.jsonl"] == (
        9_567_215,
        "sha256:97ddd0aa9b194a3082fcaa9172b562e32f4e6d1beeaa707a0fb3bf11731e68ef",
    )
    assert material_digests["table.jsonl"] == (
        3_087_333,
        "sha256:66129fcc9e68ae0bf40e7d41f540002ceff9bedf46d6dafff2321ba2b7ab5231",
    )
    assert material_digests["text_content.jsonl"] == (
        55_407_895,
        "sha256:cb16f70704fce569ee6f9ddff471735b17e3337d56e3958deaa4ebd6f93b5b37",
    )
    assert material_digests["text_formatting.jsonl"] == (
        1_785_910,
        "sha256:4f00200322cfe0bb7199f7a0a85e04e9df3964e06882e3703faf40bb1103098d",
    )
    assert all(
        digest.startswith("sha256:") for _size, digest in material_digests.values()
    )
    assert (
        PINNED_MATERIAL_MANIFEST_SHA256
        == "sha256:a8485efeeb8a82161fc449a09ed846b4fe6353d62d34457bb7f249bff6ee8d3f"
    )
    canonical_materials = json.dumps(
        PINNED_PARSEBENCH_CONTRACT.material_digests,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert PINNED_MATERIAL_MANIFEST_SHA256 == (
        "sha256:" + sha256(canonical_materials).hexdigest()
    )
