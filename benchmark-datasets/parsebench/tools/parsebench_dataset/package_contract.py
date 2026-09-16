"""Deterministic ParseBench task-package material renderers.

These helpers are shared by the package author and the upload-side inspector so
that executable task material has one byte-level contract.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from .contracts import (
    DATASET_REVISION,
    PARSEBENCH_SCOPE_FILENAME,
    PARSEBENCH_SCOPE_RUNTIME_PATH,
    PARSEBENCH_TASK_FILENAME,
    PARSEBENCH_TASK_RUNTIME_PATH,
    PARSEBENCH_TASK_SCHEMA_VERSION,
    SCORER_REVISION,
)


def canonical_json(value: object, *, newline: bool = False) -> bytes:
    """Render canonical UTF-8 JSON used by signed package materials."""

    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return content + (b"\n" if newline else b"")


def public_task_contract(
    *,
    task_id: str,
    source_runtime_path: str,
    source_sha256: str,
    source_size: int,
    page: int | None,
) -> bytes:
    """Render the ground-truth-blind task contract mounted for the agent."""

    return canonical_json(
        {
            "schema_version": PARSEBENCH_TASK_SCHEMA_VERSION,
            "task_id": task_id,
            "source": {
                "runtime_path": source_runtime_path,
                "sha256": source_sha256,
                "size": source_size,
                "page": page,
            },
            "dataset_revision": DATASET_REVISION,
            "scorer_revision": SCORER_REVISION,
        },
        newline=True,
    )


def public_scope_contract(scope: Mapping[str, object]) -> bytes:
    """Render the public package-scope contract mounted in both environments."""

    return canonical_json(dict(scope), newline=True)


def instruction(*, source_runtime_path: str, page: int | None) -> bytes:
    """Describe an agent-neutral ParseBench submission using public artifacts."""

    scope = "the complete document" if page is None else f"one-indexed page {page}"
    return (
        "# ParseBench document parsing task\n\n"
        f"Parse {scope} from `{source_runtime_path}`. Use your available tools "
        "and parsing method. All input material is local.\n\n"
        "Write these two artifacts under `/logs/artifacts/`:\n\n"
        "1. `document.md`: UTF-8 Markdown containing the parsed text, formatting, "
        "tables, and chart content. Preserve reading order. HTML tables are allowed.\n"
        "2. `layout.json`: JSON containing the official ParseOutput `layout_pages` "
        "array. Include each page's one-based `page_number`, pixel `width`/`height`, "
        "and `items` in reading order. Each item may contain `type`, `md`/`html`, "
        "a `bbox`, and `layout_segments`. Boxes use pixel `x,y,w,h`, a canonical "
        "`label`, and confidence from 0 to 1. Coordinates must fit the page.\n\n"
        "Example structure (replace values with your document predictions):\n"
        '```json\n{"layout_pages":[{"page_number":1,"width":100,"height":100,'
        '"md":"Example","items":[{"type":"text","md":"Example",'
        '"bbox":{"x":1,"y":2,"w":50,"h":10,"label":"text","confidence":1}}]}]}\n```\n\n'
        "Canonical labels: caption, footnote, formula, list-item, page-footer, "
        "page-header, picture, section-header, table, text, title, document-index, "
        "code, checkbox-selected, checkbox-unselected, form, key-value-region.\n\n"
        "For a selected page, retain its original page number. Optional `pages` "
        "entries use zero-based `page_index` and `markdown`. Empty predictions are "
        "allowed: use empty Markdown and page objects with empty `items` when no "
        "content was recovered. Do not use test answers or verifier-only files. "
        "The independent verifier applies the fixed official ParseBench scoring rules.\n"
    ).encode()


def task_toml() -> bytes:
    """Render the deterministic Harbor task configuration."""

    return (
        b'schema_version = "1.4"\n\n'
        b'artifacts = ["/logs/artifacts"]\n\n'
        b"[metadata]\n"
        b'author_name = "ParseBench Dataset adapter"\n'
        b'difficulty = "benchmark"\n'
        b'category = "parsebench"\n'
        b'tags = ["parsebench", "document-parsing", "deterministic"]\n\n'
        b"[verifier]\n"
        b"timeout_sec = 600.0\n"
        b'environment_mode = "separate"\n\n'
        b"[verifier.environment]\n"
        b"build_timeout_sec = 600.0\n"
        b'network_mode = "no-network"\n'
        b"cpus = 2\n"
        b"memory_mb = 4096\n"
        b"storage_mb = 8192\n\n"
        b"[agent]\n"
        b"timeout_sec = 1800.0\n\n"
        b"[environment]\n"
        b'network_mode = "no-network"\n'
        b'workdir = "/workspace"\n'
        b"cpus = 4\n"
        b"memory_mb = 8192\n"
        b"storage_mb = 16384\n"
    )


def agent_dockerfile(*, runtime_image: str) -> bytes:
    """Render the deterministic agent-environment Dockerfile."""

    return (
        f"FROM {runtime_image}\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends "
        "poppler-utils && rm -rf /var/lib/apt/lists/*\n"
        "WORKDIR /workspace\n"
        "COPY input/ /workspace/input/\n"
        f"COPY {PARSEBENCH_TASK_FILENAME} {PARSEBENCH_TASK_RUNTIME_PATH}\n"
        f"COPY {PARSEBENCH_SCOPE_FILENAME} {PARSEBENCH_SCOPE_RUNTIME_PATH}\n"
        "RUN mkdir -p /logs/artifacts\n"
    ).encode()


def verifier_dockerfile(*, runtime_image: str) -> bytes:
    """Build the official scorer from public pinned sources, without an agent SDK."""

    recipe = Path(__file__).resolve().parents[2] / "resources/verifier.Dockerfile"
    prefix = recipe.read_text(encoding="utf-8").replace("{base_image}", runtime_image)
    return (
        prefix
        + "COPY input/ /workspace/input/\n"
        + f"COPY {PARSEBENCH_SCOPE_FILENAME} {PARSEBENCH_SCOPE_RUNTIME_PATH}\n"
        + f"COPY {PARSEBENCH_SCOPE_FILENAME} /tests/{PARSEBENCH_SCOPE_FILENAME}\n"
        + "COPY --chmod=755 test.sh /tests/test.sh\n"
        + "COPY --chmod=444 ground_truth.json /tests/ground_truth.json\n"
        + "COPY parsebench_runtime/ /tests/parsebench_runtime/\n"
    ).encode()


def verifier_script() -> bytes:
    """Render the deterministic verifier entrypoint."""

    return (
        "#!/bin/sh\n"
        "set -eu\n"
        'script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        "exec python -m parsebench_runtime.verifier --artifact-format parsebench \\\n"
        '  --ground-truth "$script_dir/ground_truth.json" \\\n'
        f'  --scope "$script_dir/{PARSEBENCH_SCOPE_FILENAME}" \\\n'
        "  --markdown /logs/artifacts/document.md \\\n"
        "  --layout /logs/artifacts/layout.json \\\n"
        "  --verifier-output /logs/verifier\n"
    ).encode()


def artifact_specs() -> list[dict[str, str]]:
    """Return the exact artifact allowlist published in each catalog row."""

    return [
        {
            "kind": "deliverable",
            "source": "/logs/artifacts/document.md",
            "name": "document.md",
            "content_type": "text/markdown",
        },
        {
            "kind": "deliverable",
            "source": "/logs/artifacts/layout.json",
            "name": "layout.json",
            "content_type": "application/json",
        },
    ]


__all__ = (
    "agent_dockerfile",
    "artifact_specs",
    "canonical_json",
    "instruction",
    "public_scope_contract",
    "public_task_contract",
    "task_toml",
    "verifier_dockerfile",
    "verifier_script",
)
