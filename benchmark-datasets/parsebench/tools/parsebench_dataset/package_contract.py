"""Deterministic ParseBench task-package material renderers.

These helpers are shared by the package author and the upload-side inspector so
that executable task material has one byte-level contract.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

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
    """Render the deterministic agent instruction for one source/page."""

    source_name = source_runtime_path.rsplit("/", 1)[-1]
    scope = "the complete document" if page is None else f"one-indexed page {page}"
    page_argument = "" if page is None else f" --pages {page}"
    return (
        "# FileX ParseBench task\n\n"
        f"Parse {scope} from `/workspace/input/{source_name}` using the loaded `filex` skill "
        "and its independent FileX CLI.\n\n"
        "Run exactly this generic FileX skill wrapper command:\n\n"
        "```bash\n"
        "python3 /opt/runtime-agent/skills/filex/scripts/filex.py parse "
        f"--input /workspace/input/{source_name}{page_argument} "
        "--provider paddle_ocr --no-cache "
        "--artifacts-dir /logs/artifacts\n"
        "```\n\n"
        "It writes exactly these output artifacts:\n\n"
        "- `/logs/artifacts/document.md`: parsed Markdown.\n"
        "- `/logs/artifacts/layout.json`: original FileX Document IR.\n"
        "- `/logs/artifacts/result.json`: generic FileX skill result with source/output "
        "hashes and parser evidence.\n\n"
        "The authored task is no-network. A production runtime may add only its "
        "explicit model-proxy host. Do not fetch dataset or ground-truth material; "
        "local connectivity requires an explicit non-publishable mode and an "
        "external/model proxy.\n\n"
        "Do not inspect or depend on verifier-only files. Fail explicitly if the "
        "configured provider falls back or cannot emit required output. Do not manually "
        "invent or rewrite these artifacts; the dataset verifier owns ParseBench-specific "
        "normalization and scoring.\n"
    ).encode()


def task_toml() -> bytes:
    """Render the deterministic Harbor task configuration."""

    return (
        b'schema_version = "1.4"\n\n'
        b'artifacts = ["/logs/artifacts"]\n\n'
        b"[metadata]\n"
        b'author_name = "inclusionAI/AWorld"\n'
        b'difficulty = "benchmark"\n'
        b'category = "parsebench"\n'
        b'tags = ["parsebench", "filex", "deterministic"]\n\n'
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
        "WORKDIR /workspace\n"
        "COPY input/ /workspace/input/\n"
        f"COPY {PARSEBENCH_TASK_FILENAME} {PARSEBENCH_TASK_RUNTIME_PATH}\n"
        f"COPY {PARSEBENCH_SCOPE_FILENAME} {PARSEBENCH_SCOPE_RUNTIME_PATH}\n"
        "RUN mkdir -p /logs/artifacts\n"
    ).encode()


def verifier_dockerfile(*, runtime_image: str) -> bytes:
    """Render the deterministic isolated-verifier Dockerfile."""

    return (
        f"FROM {runtime_image}\n"
        "WORKDIR /tests\n"
        "COPY input/ /workspace/input/\n"
        f"COPY {PARSEBENCH_SCOPE_FILENAME} {PARSEBENCH_SCOPE_RUNTIME_PATH}\n"
        "COPY --chmod=755 test.sh /tests/test.sh\n"
        "COPY --chmod=444 ground_truth.json /tests/ground_truth.json\n"
        "COPY parsebench_runtime/ /tests/parsebench_runtime/\n"
    ).encode()


def verifier_script() -> bytes:
    """Render the deterministic verifier entrypoint."""

    return (
        "#!/bin/sh\n"
        "set -eu\n"
        'script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        "exec python -m parsebench_runtime.verifier \\\n"
        '  --ground-truth "$script_dir/ground_truth.json" \\\n'
        f'  --scope "$script_dir/{PARSEBENCH_SCOPE_FILENAME}" \\\n'
        "  --result /logs/artifacts/result.json \\\n"
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
        {
            "kind": "deliverable",
            "source": "/logs/artifacts/result.json",
            "name": "result.json",
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
