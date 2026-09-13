"""Materialize ParseBench as a standard, unmodified lingguang-bench project."""

from __future__ import annotations

import argparse
import json
import tarfile
import tempfile
from pathlib import Path
from zipfile import ZipFile

import yaml

from .contracts import PINNED_PARSEBENCH_CONTRACT, ParseBenchContract
from .dataset import (
    DEFAULT_RUNTIME_IMAGE,
    SmokeSelection,
    build_parsebench_executable_dataset,
)


def _extract_task(content: bytes, destination: Path, expected_task_id: str) -> None:
    archive_path = destination.parent / f".{expected_task_id}.tar.gz"
    archive_path.write_bytes(content)
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            prefix = expected_task_id + "/"
            for member in members:
                if member.issym() or member.islnk():
                    raise ValueError("ParseBench task archives must not contain links")
                if member.name != expected_task_id and not member.name.startswith(
                    prefix
                ):
                    raise ValueError("ParseBench task archive has an invalid root")
            archive.extractall(destination.parent, filter="data")
    finally:
        archive_path.unlink(missing_ok=True)


def materialize_project(
    source: Path,
    output: Path,
    *,
    runtime_image: str,
    runtime_service: str,
    gateway_base_url: str,
    model_name: str,
    smoke_per_dimension: int | None,
    smoke_seed: str,
    allow_mutable_local_image: bool = False,
    contract: ParseBenchContract = PINNED_PARSEBENCH_CONTRACT,
) -> Path:
    """Create a project consumed by stock ``lingbench validate/publish/run``."""

    output = output.expanduser().resolve()
    if output.exists():
        if any(output.iterdir()):
            raise ValueError(f"Output directory is not empty: {output}")
    else:
        output.mkdir(parents=True)
    selection = (
        None
        if smoke_per_dimension is None
        else SmokeSelection(per_dimension=smoke_per_dimension, seed=smoke_seed)
    )
    with tempfile.TemporaryDirectory(prefix="parsebench-project-") as temporary:
        package_path = Path(temporary) / "parsebench.zip"
        result = build_parsebench_executable_dataset(
            source,
            package_path,
            contract=contract,
            selection=selection,
            runtime_image=runtime_image,
            allow_mutable_local_image=allow_mutable_local_image,
        )
        dataset_directory = output / "dataset"
        tasks_directory = output / "tasks"
        dataset_directory.mkdir()
        tasks_directory.mkdir()
        with ZipFile(package_path) as package:
            descriptor = yaml.safe_load(package.read("dataset.yaml"))
            rows = [
                json.loads(line)
                for line in package.read("dataset.jsonl").decode("utf-8").splitlines()
                if line
            ]
            for row in rows:
                task_id = str(row["task_id"])
                _extract_task(
                    package.read(f"tasks/{task_id}.tar.gz"),
                    tasks_directory / task_id,
                    task_id,
                )
        client_metadata = {
            "agent_dataset": {
                "dataset_id": descriptor["dataset_id"],
                "description": "ParseBench tasks executed by AWorld with FileX",
                "params": descriptor.get("params") or {},
            }
        }
        (dataset_directory / "dataset.yaml").write_text(
            yaml.safe_dump(client_metadata, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        (dataset_directory / "samples.jsonl").write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
        dataset_id = descriptor["dataset_id"]
        service_name = descriptor.get("service_name") or runtime_service
        (output / "bench.toml").write_text(
            "\n".join(
                [
                    "[project]",
                    f'name = "{dataset_id}"',
                    "",
                    "[gateway]",
                    f'base_url = "{gateway_base_url}"',
                    'token_env = "LINGGUANG_GATEWAY_TOKEN"',
                    "",
                    "[runtime]",
                    f'service_name = "{runtime_service}"',
                    "",
                    "[dataset]",
                    f'id = "{dataset_id}"',
                    f'service_name = "{service_name}"',
                    'package_mode = "executable"',
                    'metadata_file = "dataset/dataset.yaml"',
                    'samples_file = "dataset/samples.jsonl"',
                    'tasks_dir = "tasks"',
                    "",
                    "[execution]",
                    'agent = "aworld"',
                    "timeout_s = 3600",
                    "prewarm_timeout_s = 1200",
                    "include_trajectory = true",
                    "",
                    "[capabilities]",
                    'skills = ["filex"]',
                    "",
                    "[model]",
                    f'name = "{model_name}"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (output / "BUILD.json").write_text(
            json.dumps(
                {
                    "package_sha256": result.package_sha256,
                    "task_count": result.task_count,
                    "rule_count": result.rule_count,
                    "publishable": result.publishable,
                    "selection_manifest_sha256": result.selection_manifest_sha256,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runtime-image", default=DEFAULT_RUNTIME_IMAGE)
    parser.add_argument("--runtime-service", required=True)
    parser.add_argument("--gateway-base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--model-name", default="default__gemini-3.1-pro-preview")
    parser.add_argument(
        "--allow-mutable-local-image",
        action="store_true",
        help="allow a tagged local Runtime image and mark the Dataset non-publishable",
    )
    parser.add_argument("--smoke-per-dimension", type=int)
    parser.add_argument("--smoke-seed", default="parsebench-smoke-v1")
    arguments = parser.parse_args()
    materialize_project(
        arguments.source,
        arguments.output,
        runtime_image=arguments.runtime_image,
        runtime_service=arguments.runtime_service,
        gateway_base_url=arguments.gateway_base_url,
        model_name=arguments.model_name,
        smoke_per_dimension=arguments.smoke_per_dimension,
        smoke_seed=arguments.smoke_seed,
        allow_mutable_local_image=arguments.allow_mutable_local_image,
    )


if __name__ == "__main__":
    main()
