"""Run reproducible, paired context-management experiments on packaged Tool benchmarks.

Each benchmark is a workload adapter, not an optimization target: variants may
change only AWorld context and Tool output policies.  Instructions, system
prompt, model settings, container image, verifier, and reward path are invariant.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import hashlib
import json
import math
import os
import random
import re
import shutil
import stat
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aworld.evaluations.normalized_cost import NormalizedCostPolicy  # noqa: E402
from aworld.core.llm_call_journal import read_llm_call_journal  # noqa: E402
from aworld.core.tool_action_journal import read_tool_action_journal  # noqa: E402
from examples.sandbox.docker_terminal_bench import (  # noqa: E402
    PYTHON_FUNCTION_VERIFIER_IMAGE,
    load_external_mcp_config,
    run_python_function_verifier_sidecar,
)


RUNNER = Path(__file__).with_name("docker_terminal_bench.py")
MODEL_PREFLIGHT = Path(__file__).with_name("model_preflight.py")
_VERIFIER_ENV_EXPRESSION = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_directory(path: Path) -> str:
    """Hash the complete Docker build context, including paths and permissions."""
    root = path.resolve()
    digest = hashlib.sha256()
    for entry in sorted(
        root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()
    ):
        relative = entry.relative_to(root).as_posix().encode("utf-8")
        metadata = entry.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"Docker build context cannot contain links: {entry}")
        kind = b"d" if entry.is_dir() else b"f" if entry.is_file() else None
        if kind is None:
            raise ValueError(f"Unsupported Docker build context entry: {entry}")
        digest.update(kind + b"\0" + relative + b"\0")
        digest.update(f"{stat.S_IMODE(metadata.st_mode):o}".encode("ascii") + b"\0")
        if entry.is_file():
            with entry.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def canonical_json_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def run_command(
    command: list[str], *, timeout: float | None = None, **kwargs
) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=False, timeout=timeout, text=True, **kwargs)


def require_success(result: subprocess.CompletedProcess, operation: str) -> None:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{operation} failed ({result.returncode}): {detail}")


def timeout_output(exc: subprocess.TimeoutExpired, stream: str) -> str:
    value = getattr(exc, stream, None) or ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def parse_aworld_run_failure(*streams: str) -> dict | None:
    """Recover the typed failure emitted by the direct AWorld boundary."""
    for stream in streams:
        for line in reversed(str(stream or "").splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(value, dict)
                and value.get("schema_version") == "aworld.run.failure.v1"
            ):
                return value
    return None


def parse_model_preflight(*streams: str) -> dict | None:
    for stream in streams:
        for line in reversed(str(stream or "").splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(value, dict)
                and value.get("schema_version") == "aworld.model-preflight/v1"
            ):
                return value
    return None


def call_snapshot_digest(calls: list[dict]) -> str:
    """Hash provider calls by stable identity, independent of branch merge order."""
    normalized = []
    identities = set()
    for call in calls:
        if not isinstance(call, dict):
            raise ValueError("LLM call snapshot entries must be objects")
        identity = next(
            (
                (field, value)
                for field in ("request_id", "call_id")
                if isinstance((value := call.get(field)), str) and value
            ),
            None,
        )
        if identity is None or identity in identities:
            raise ValueError("LLM call snapshot requires unique stable identities")
        identities.add(identity)
        normalized.append({"identity": list(identity), "call": call})
    normalized.sort(key=lambda item: tuple(item["identity"]))
    return canonical_json_digest(normalized)


def run_model_preflight(
    output_dir: Path, *, timeout_sec: float, model_seed: int
) -> dict:
    command = [
        sys.executable,
        str(MODEL_PREFLIGHT),
        "--timeout-sec",
        str(timeout_sec),
        "--model-seed",
        str(model_seed),
    ]
    try:
        result = run_command(
            command,
            capture_output=True,
            timeout=timeout_sec + 15,
            env=os.environ.copy(),
        )
        stdout, stderr = result.stdout or "", result.stderr or ""
        returncode = result.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = timeout_output(exc, "stdout")
        stderr = timeout_output(exc, "stderr")
        returncode = None
    (output_dir / "model-preflight.stdout.log").write_text(stdout, encoding="utf-8")
    (output_dir / "model-preflight.stderr.log").write_text(stderr, encoding="utf-8")
    receipt = parse_model_preflight(stderr, stdout) or {
        "schema_version": "aworld.model-preflight/v1",
        "status": "failed",
        "reason_code": (
            "provider_connectivity_timeout"
            if returncode is None
            else "provider_preflight_receipt_missing"
        ),
    }
    receipt["process_exit_code"] = returncode
    write_json(output_dir / "model-preflight.json", receipt)
    return receipt


class VerifierEnvironmentUnavailable(ValueError):
    def __init__(self, target_name: str, source_name: str) -> None:
        super().__init__(
            f"verifier environment {target_name!r} requires missing host variable "
            f"{source_name!r}"
        )
        self.target_name = target_name
        self.source_name = source_name


def verifier_environment_contract(config: dict) -> dict:
    """Describe packaged verifier inputs without exposing resolved values."""
    verifier = config.get("verifier", {})
    configured = verifier.get("env", {}) if isinstance(verifier, dict) else {}
    if configured is None:
        configured = {}
    if not isinstance(configured, dict):
        raise ValueError("verifier.env must be an object")
    entries = []
    for target, template in sorted(configured.items()):
        if not isinstance(target, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", target
        ):
            raise ValueError(f"Unsafe verifier environment name: {target!r}")
        if not isinstance(template, str):
            raise ValueError(f"verifier.env {target!r} must be a string")
        match = _VERIFIER_ENV_EXPRESSION.fullmatch(template)
        if match:
            entries.append(
                {
                    "target": target,
                    "source": "host_environment",
                    "source_name": match.group(1),
                    "has_default": match.group(2) is not None,
                }
            )
        elif "${" in template:
            raise ValueError(
                f"verifier.env {target!r} uses an unsupported interpolation expression"
            )
        else:
            entries.append(
                {
                    "target": target,
                    "source": "literal",
                    "has_default": False,
                }
            )
    return {
        "schema_version": "aworld.context-eval-verifier-env/v1",
        "names": [entry["target"] for entry in entries],
        "entries": entries,
    }


def resolve_verifier_environment(
    config: dict, environ: Mapping[str, str]
) -> tuple[dict[str, str], dict]:
    """Resolve task-declared verifier env while keeping secrets out of evidence."""
    contract = verifier_environment_contract(config)
    verifier = config.get("verifier", {})
    configured = verifier.get("env", {}) if isinstance(verifier, dict) else {}
    resolved: dict[str, str] = {}
    resolution = []
    for entry in contract["entries"]:
        target = entry["target"]
        template = configured[target]
        match = _VERIFIER_ENV_EXPRESSION.fullmatch(template)
        if not match:
            resolved[target] = template
            resolution.append({"target": target, "resolved_from": "literal"})
            continue
        source_name, default = match.groups()
        source_value = environ.get(source_name)
        if source_value:
            resolved[target] = source_value
            resolution.append(
                {
                    "target": target,
                    "resolved_from": "host_environment",
                    "source_name": source_name,
                }
            )
        elif default is not None:
            resolved[target] = default
            resolution.append({"target": target, "resolved_from": "default"})
        else:
            raise VerifierEnvironmentUnavailable(target, source_name)
    evidence = {
        **contract,
        "status": "available",
        "resolution": resolution,
    }
    return resolved, evidence


def safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"Unsafe task archive member: {member.name}")
        if member.issym() or member.islnk():
            raise ValueError(f"Task archives may not contain links: {member.name}")
    archive.extractall(destination, filter="data")


@dataclass(frozen=True)
class TaskFixture:
    name: str
    root: Path
    archive_sha256: str
    config: dict
    benchmark_adapter: str = "terminal-bench-2.1"
    verifier_directory: str = "tests"

    @property
    def instruction(self) -> Path:
        return self.root / "instruction.md"

    @property
    def environment(self) -> Path:
        return self.root / "environment"

    @property
    def tests(self) -> Path:
        return self.verifier

    @property
    def verifier(self) -> Path:
        return self.root / self.verifier_directory

    @property
    def skills(self) -> Path | None:
        path = self.environment / "skills"
        return path if path.is_dir() else None


def _dataset_catalog(package: zipfile.ZipFile) -> dict[str, dict]:
    try:
        rows = package.read("dataset.jsonl").decode("utf-8").splitlines()
    except KeyError:
        return {}
    catalog: dict[str, dict] = {}
    for ordinal, line in enumerate(rows, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid dataset.jsonl row {ordinal}") from exc
        task_id = row.get("task_id") if isinstance(row, dict) else None
        if not isinstance(task_id, str) or not task_id:
            raise ValueError(f"dataset.jsonl row {ordinal} has no task_id")
        if task_id in catalog:
            raise ValueError(f"dataset.jsonl contains duplicate task_id {task_id!r}")
        catalog[task_id] = row
    return catalog


def extract_task(dataset: Path, task_name: str, destination: Path) -> TaskFixture:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", task_name):
        raise ValueError(f"Unsafe task name {task_name!r}")
    archive_name = f"tasks/{task_name}.tar.gz"
    with zipfile.ZipFile(dataset) as package:
        catalog = _dataset_catalog(package)
        try:
            archive_bytes = package.read(archive_name)
        except KeyError as exc:
            available = sorted(
                Path(name).name.removesuffix(".tar.gz")
                for name in package.namelist()
                if name.startswith("tasks/") and name.endswith(".tar.gz")
            )
            raise ValueError(
                f"Unknown task {task_name!r}; dataset contains {len(available)} tasks"
            ) from exc

    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar.gz") as temporary:
        temporary.write(archive_bytes)
        temporary.flush()
        with tarfile.open(temporary.name, "r:gz") as archive:
            safe_extract_tar(archive, destination)

    task_files = list(destination.rglob("task.toml"))
    if len(task_files) == 1:
        root = task_files[0].parent
        config = tomllib.loads(task_files[0].read_text(encoding="utf-8"))
        dataset_id = str(catalog.get(task_name, {}).get("dataset_id") or "")
        benchmark_adapter = (
            "openai-browsecomp"
            if dataset_id.startswith("openai_browsecomp")
            else "terminal-bench-2.1"
        )
        verifier_directory = "tests"
    elif not task_files and task_name in catalog:
        roots = [path.parent for path in destination.rglob("task.md")]
        if len(roots) != 1:
            raise ValueError(
                f"Expected one task.md for catalog task {task_name}, found {len(roots)}"
            )
        root = roots[0]
        catalog_row = catalog[task_name]
        declared_image = catalog_row.get("prebuilt_environment_image")
        if catalog_row.get("task_dir") != archive_name:
            raise ValueError(f"Catalog task_dir mismatch for {task_name}")
        if not isinstance(declared_image, str) or not re.search(
            r"@sha256:[0-9a-f]{64}$", declared_image
        ):
            raise ValueError(
                f"SkillsBench task {task_name} requires an immutable prebuilt image"
            )
        config = {
            "task": {"name": catalog_row.get("harbor_task_name") or task_name},
            "environment": {"docker_image": declared_image},
            "agent": {"timeout_sec": 900},
            "verifier": {"timeout_sec": 900},
        }
        benchmark_adapter = "skillsbench-official-1.1"
        verifier_directory = "verifier"
    else:
        raise ValueError(
            f"Expected one task.toml for {task_name}, found {len(task_files)}"
        )
    required = [
        root / "instruction.md",
        root / "environment" / "Dockerfile",
        root / verifier_directory / "test.sh",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ValueError(f"Task {task_name} is incomplete: {', '.join(missing)}")
    return TaskFixture(
        name=task_name,
        root=root,
        archive_sha256=hashlib.sha256(archive_bytes).hexdigest(),
        config=config,
        benchmark_adapter=benchmark_adapter,
        verifier_directory=verifier_directory,
    )


def parse_registry_rewrite(value: str) -> tuple[str, str]:
    source, separator, destination = value.partition("=")
    if not separator or not source.strip() or not destination.strip():
        raise argparse.ArgumentTypeError("registry rewrite must use SOURCE=DESTINATION")
    source = source.strip().rstrip("/")
    destination = destination.strip().rstrip("/")
    if "/" in source or "/" in destination:
        raise argparse.ArgumentTypeError(
            "registry rewrite endpoints must be registry hostnames"
        )
    return source, destination


def rewrite_image_registry(
    image: str, rewrites: tuple[tuple[str, str], ...]
) -> tuple[str, str]:
    registry, separator, remainder = image.partition("/")
    if not separator:
        return image, "not_applied"
    matches = [destination for source, destination in rewrites if registry == source]
    if len(matches) > 1:
        raise ValueError(f"Multiple registry rewrites match {registry!r}")
    if not matches:
        return image, "not_applied"
    return f"{matches[0]}/{remainder}", "cli_registry_rewrite"


def load_variant(path: Path | None) -> tuple[str, Path | None, dict]:
    if path is None:
        payload = {
            "schema_version": "aworld.context-eval-variant/v1",
            "name": "baseline",
            "agent_memory_config": {},
            "context_compiler": {},
            "docker_output_policy": {},
        }
        return "baseline", None, payload
    payload = json.loads(path.read_text(encoding="utf-8"))
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError(f"Variant {path} has no name")
    # The per-run adapter performs the authoritative field allow-list check.
    return name, path.resolve(), payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--task", required=True, action="append", dest="tasks")
    parser.add_argument("--variant-config", type=Path, action="append", dest="variants")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260831)
    parser.add_argument(
        "--model-preflight-timeout-sec",
        type=float,
        default=120,
        help="Fail-fast provider connectivity timeout before Docker image work starts.",
    )
    parser.add_argument(
        "--skip-model-preflight",
        action="store_true",
        help="Explicit diagnostic-only escape hatch; makes quality claims ineligible.",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument(
        "--use-declared-image",
        action="store_true",
        help="Pull/use task.toml docker_image instead of building the packaged Dockerfile.",
    )
    parser.add_argument(
        "--build-timeout-sec",
        type=float,
        help=(
            "Override the task's packaged Docker image build timeout. When omitted, "
            "the dataset build_timeout_sec (or 600 seconds) is used."
        ),
    )
    parser.add_argument(
        "--agent-timeout-sec",
        type=float,
        help="Override the per-run AWorld agent timeout; typed timeout evidence is retained.",
    )
    parser.add_argument(
        "--verifier-timeout-sec",
        type=float,
        help="Override the per-run independent verifier timeout.",
    )
    parser.add_argument(
        "--mcp-config",
        type=Path,
        help=(
            "Invariant external MCP Tool profile shared by all variants; use this for "
            "research/browser workloads without changing benchmark or runtime projects."
        ),
    )
    parser.add_argument(
        "--image-registry-rewrite",
        action="append",
        type=parse_registry_rewrite,
        default=[],
        metavar="SOURCE=DESTINATION",
        help=(
            "Rewrite only the registry hostname while retaining the repository, tag and "
            "immutable digest. Useful when a packaged VPC registry has a public endpoint."
        ),
    )
    parser.add_argument("--keep-containers", action="store_true")
    parser.add_argument(
        "--verifier-mode",
        choices=("packaged", "python-functions"),
        default="packaged",
        help=(
            "packaged runs tests/test.sh; python-functions executes the original zero-argument "
            "test_* functions without the package install wrapper for constrained local Docker hosts"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def docker_image_for_task(
    fixture: TaskFixture,
    docker: str,
    use_declared_image: bool,
    build_timeout_sec: float | None = None,
    registry_rewrites: tuple[tuple[str, str], ...] = (),
) -> str:
    plan = docker_image_build_plan(
        fixture,
        use_declared_image=use_declared_image,
        build_timeout_sec=build_timeout_sec,
        registry_rewrites=registry_rewrites,
    )
    image = str(plan["image_ref"])
    if use_declared_image:
        inspect = run_command([docker, "image", "inspect", image], capture_output=True)
        if inspect.returncode != 0:
            pull = run_command([docker, "pull", image], capture_output=True)
            require_success(pull, f"pull image for {fixture.name}")
        return image

    inspect = run_command([docker, "image", "inspect", image], capture_output=True)
    if inspect.returncode != 0:
        timeout = float(plan["effective_build_timeout_sec"])
        try:
            build = run_command(
                [
                    docker,
                    "build",
                    "--label",
                    "aworld.context-eval=true",
                    "-t",
                    image,
                    ".",
                ],
                cwd=fixture.environment,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"build image for {fixture.name} timed out after {timeout:g} seconds"
            ) from exc
        require_success(build, f"build image for {fixture.name}")
    return image


def docker_image_build_plan(
    fixture: TaskFixture,
    *,
    use_declared_image: bool,
    build_timeout_sec: float | None,
    registry_rewrites: tuple[tuple[str, str], ...] = (),
) -> dict[str, object]:
    """Return reproducible, manifest-safe image resolution inputs."""
    environment = fixture.config.get("environment")
    if not isinstance(environment, dict):
        raise ValueError(f"Task {fixture.name} has no environment configuration")
    declared = environment.get("docker_image")
    if use_declared_image:
        if not isinstance(declared, str) or not declared.strip():
            raise ValueError(f"Task {fixture.name} has no declared docker_image")
        effective_image, rewrite_source = rewrite_image_registry(
            declared, registry_rewrites
        )
        return {
            "mode": "declared_image",
            "declared_image": declared,
            "image_ref": effective_image,
            "image_registry_source": rewrite_source,
            "task_archive_sha256": fixture.archive_sha256,
            "build_context_sha256": None,
            "effective_build_timeout_sec": None,
            "build_timeout_source": "not_applicable",
        }

    timeout_source = (
        "cli_override"
        if build_timeout_sec is not None
        else "dataset"
        if "build_timeout_sec" in environment
        else "framework_default"
    )
    timeout_value = (
        build_timeout_sec
        if build_timeout_sec is not None
        else environment.get("build_timeout_sec", 600)
    )
    if (
        isinstance(timeout_value, bool)
        or not isinstance(timeout_value, (int, float))
        or not math.isfinite(float(timeout_value))
        or float(timeout_value) <= 0
    ):
        raise ValueError(
            f"Task {fixture.name} Docker build timeout must be a positive finite number"
        )
    context_sha = sha256_directory(fixture.environment)
    return {
        "mode": "packaged_dockerfile",
        "declared_image": declared if isinstance(declared, str) else None,
        "image_ref": f"aworld-context-eval:{context_sha[:24]}",
        "image_registry_source": "not_applicable",
        "task_archive_sha256": fixture.archive_sha256,
        "build_context_sha256": context_sha,
        "effective_build_timeout_sec": float(timeout_value),
        "build_timeout_source": timeout_source,
    }


def docker_image_id(docker: str, image: str) -> str | None:
    result = run_command(
        [docker, "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def container_image_id(docker: str, target: str) -> str | None:
    result = run_command(
        [docker, "inspect", "--format", "{{.Image}}", target], capture_output=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def recover_inflight_capture(run_dir: Path) -> dict:
    """Recover runtime-stage evidence without manufacturing a trajectory.

    A checksum-valid journal can prove that an LLM request reached a provider
    boundary before an external timeout.  The independent Tool journal can prove
    only Tool phases that were actually appended; neither journal can manufacture
    a missing model response, Tool result, verifier reward, or Raw trajectory.
    """
    journal_path = run_dir / "llm_calls.journal.jsonl"
    recovery = read_llm_call_journal(journal_path)
    tool_journal_path = run_dir / "tool_actions.journal.jsonl"
    tool_recovery = read_tool_action_journal(tool_journal_path)
    tool_events = list(tool_recovery.events)
    tool_event_counts: dict[str, int] = {}
    tool_batch_phases: dict[str, set[str]] = {}
    for event in tool_events:
        event_type = str(event.get("event_type") or "unknown")
        tool_event_counts[event_type] = tool_event_counts.get(event_type, 0) + 1
        batch_id = event.get("batch_id")
        if isinstance(batch_id, str) and batch_id:
            tool_batch_phases.setdefault(batch_id, set()).add(event_type)
    unresolved_tool_batches = sorted(
        batch_id
        for batch_id, phases in tool_batch_phases.items()
        if "sandbox_call_started" in phases
        and not phases.intersection(
            {
                "sandbox_call_completed",
                "sandbox_call_failed",
                "sandbox_transaction_resolved",
                "tool_observation_recorded",
            }
        )
    )
    final_calls_path = run_dir / "llm_calls.json"
    raw_trajectory_path = run_dir / "raw_trajectory.json"
    partial_trajectory_path = run_dir / "raw_trajectory.partial.json"
    evidence = recovery.to_evidence()
    evidence.update(
        {
            "record_type": "inflight_capture_recovery",
            "journal_path": journal_path.name,
            "final_llm_calls_available": final_calls_path.exists(),
            "raw_trajectory_available": raw_trajectory_path.exists(),
            "partial_raw_trajectory_available": partial_trajectory_path.exists(),
            "raw_trajectory_authority": "runtime_events_and_finalized_trajectory_projection",
            "tool_action_journal": {
                **tool_recovery.to_evidence(),
                "journal_path": tool_journal_path.name,
                "event_type_counts": dict(sorted(tool_event_counts.items())),
                "unresolved_started_batch_count": len(unresolved_tool_batches),
                "unresolved_started_batch_ids": unresolved_tool_batches,
            },
        }
    )
    calls = list(recovery.merged_llm_calls)
    attempted = [
        call
        for call in calls
        if isinstance(call, dict)
        and (
            call.get("provider_attempt_status") == "attempted"
            or call.get("provider_invoked") is True
        )
    ]
    evidence["attempted_provider_call_count"] = len(attempted)
    active_attempts = [
        call for call in attempted if call.get("status") == "in_progress"
    ]
    successful_calls = [call for call in calls if call.get("status") == "success"]
    if final_calls_path.exists():
        try:
            final_calls = json.loads(final_calls_path.read_text(encoding="utf-8"))
            if not isinstance(final_calls, list):
                raise ValueError("final llm_calls is not a list")
            journal_ordered_hash = canonical_json_digest(calls)
            final_ordered_hash = canonical_json_digest(final_calls)
            journal_hash = call_snapshot_digest(calls)
            final_hash = call_snapshot_digest(final_calls)
            continuity = {
                "status": "available",
                "comparison_basis": "stable_provider_identity",
                "journal_count": len(calls),
                "final_count": len(final_calls),
                "journal_sha256": journal_hash,
                "final_sha256": final_hash,
                "journal_ordered_sha256": journal_ordered_hash,
                "final_ordered_sha256": final_ordered_hash,
                "sequence_match": journal_ordered_hash == final_ordered_hash,
                "snapshots_match": journal_hash == final_hash,
            }
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continuity = {
                "status": "unavailable",
                "reason_code": "final_llm_calls_malformed",
            }
        evidence["journal_final_continuity"] = continuity
        evidence.update(
            {
                "classification": (
                    "final_capture_available"
                    if continuity.get("snapshots_match") is True
                    else "final_capture_journal_mismatch"
                ),
                "trajectory_generation_state": (
                    "finalized"
                    if raw_trajectory_path.exists()
                    else "final_projection_missing"
                ),
            }
        )
    elif active_attempts and not successful_calls:
        evidence.update(
            {
                "classification": "provider_attempted_response_not_observed",
                "trajectory_generation_state": "not_produced_no_model_response",
                "trajectory_persistence_state": "not_applicable",
            }
        )
    elif active_attempts:
        evidence.update(
            {
                "classification": "provider_attempted_after_prior_model_completion",
                "trajectory_generation_state": "partial_runtime_state_possible",
                "trajectory_persistence_state": "undetermined_until_finalize",
            }
        )
    elif calls and any(call.get("status") != "in_progress" for call in calls):
        evidence.update(
            {
                "classification": "model_call_completed_final_projection_missing",
                "trajectory_generation_state": "undetermined",
                "trajectory_persistence_state": "undetermined",
            }
        )
    elif calls:
        evidence.update(
            {
                "classification": "request_captured_before_provider_attempt",
                "trajectory_generation_state": "not_produced_provider_not_attempted",
                "trajectory_persistence_state": "not_applicable",
            }
        )
    elif tool_recovery.available:
        tool_only_classification = (
            "tool_action_started_result_not_observed"
            if unresolved_tool_batches
            else "tool_action_observed_final_projection_missing"
        )
        evidence.update(
            {
                "classification": tool_only_classification,
                "trajectory_generation_state": "partial_runtime_state_possible",
                "trajectory_persistence_state": "undetermined_until_finalize",
            }
        )
    else:
        evidence.update(
            {
                "classification": "runtime_capture_unavailable",
                "trajectory_generation_state": "undetermined",
                "trajectory_persistence_state": "undetermined",
            }
        )

    if recovery.available and not final_calls_path.exists():
        write_json(run_dir / "llm_calls.partial.json", calls)
        write_json(run_dir / "provider_calls.partial.json", attempted)
    if (
        (recovery.available and calls) or tool_recovery.available
    ) and not raw_trajectory_path.exists():
        # Preserve only journal-observed data.  The explicit partial schema
        # prevents this evidence from being mistaken for finalized ATIF output.
        write_json(
            partial_trajectory_path,
            {
                "schema_version": "aworld.raw-trajectory.partial/v1",
                "completion_state": "incomplete",
                "authority": "checksum_valid_append_only_runtime_journals",
                "journal_status": evidence.get("status"),
                "valid_record_count": recovery.valid_record_count,
                "calls": calls,
                "tool_journal_status": tool_recovery.to_evidence()["status"],
                "tool_valid_record_count": tool_recovery.valid_record_count,
                "tool_events": tool_events,
            },
        )
        evidence["partial_raw_trajectory_available"] = True
        evidence["partial_raw_trajectory_path"] = partial_trajectory_path.name
        evidence["partial_raw_trajectory_sha256"] = sha256_file(partial_trajectory_path)
    write_json(run_dir / "capture_recovery.json", evidence)
    return evidence


def finalized_capture_allows_independent_verifier(recovery: dict) -> bool:
    continuity = recovery.get("journal_final_continuity") or {}
    return bool(
        recovery.get("raw_trajectory_available") is True
        and recovery.get("trajectory_generation_state") == "finalized"
        and continuity.get("snapshots_match") is True
    )


def collect_context_metrics(run_dir: Path) -> dict:
    parse_failures: list[str] = []

    def load_evidence(path: Path, default: object, reason_code: str) -> object:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            parse_failures.append(reason_code)
            return default

    provider_calls_path = run_dir / "provider_calls.json"
    partial_provider_calls_path = run_dir / "provider_calls.partial.json"
    trajectory_path = run_dir / "raw_trajectory.json"
    partial_trajectory_path = run_dir / "raw_trajectory.partial.json"
    provider_calls = load_evidence(provider_calls_path, [], "provider_calls_malformed")
    trajectory = load_evidence(trajectory_path, [], "raw_trajectory_malformed")
    partial_provider_calls = load_evidence(
        partial_provider_calls_path, [], "partial_provider_calls_malformed"
    )
    partial_trajectory = load_evidence(
        partial_trajectory_path, {}, "partial_raw_trajectory_malformed"
    )
    recovery = load_evidence(
        run_dir / "capture_recovery.json", {}, "capture_recovery_malformed"
    )
    manifest_path = run_dir / "run_manifest.json"
    manifest = load_evidence(manifest_path, {}, "run_manifest_malformed")
    capture = manifest.get("capture") if isinstance(manifest, dict) else {}
    continuity = capture.get("llm_call_continuity") if isinstance(capture, dict) else {}
    journal_continuity = (
        continuity.get("journal_reconciliation")
        if isinstance(continuity, dict)
        else {}
    )

    def provider_metrics(calls: object) -> dict[str, Any]:
        prompt_tokens = completion_tokens = cache_read_tokens = 0
        provider_request_bytes = trace_match_count = 0
        provider_prefix_hashes: list[str] = []
        values = calls if isinstance(calls, list) else []
        for call in values:
            if not isinstance(call, dict):
                continue
            provider_request = call.get("provider_request") or {}
            request = provider_request.get("payload") or call.get("request") or {}
            request_messages = (
                request.get("messages", []) if isinstance(request, dict) else []
            )
            stable_prefix = []
            for message in (
                request_messages if isinstance(request_messages, list) else []
            ):
                if not isinstance(message, dict) or message.get("role") != "system":
                    break
                stable_prefix.append(message)
            provider_prefix_hashes.append(canonical_json_digest(stable_prefix))
            provider_request_bytes += len(
                json.dumps(
                    request, ensure_ascii=False, sort_keys=True, default=str
                ).encode("utf-8")
            )
            usage = call.get("usage_normalized") or call.get("usage") or {}
            raw_usage = call.get("usage_raw") or usage
            prompt_details = raw_usage.get("prompt_tokens_details") or {}
            prompt_tokens += int(
                usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            )
            completion_tokens += int(
                usage.get("completion_tokens") or usage.get("output_tokens") or 0
            )
            cache_read_tokens += int(
                raw_usage.get("cache_hit_tokens")
                or raw_usage.get("cache_read_input_tokens")
                or prompt_details.get("cached_tokens")
                or 0
            )
            trace_match_count += int(call.get("request_trace_match") is True)
        return {
            "provider_request_bytes": provider_request_bytes,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cache_read_tokens": cache_read_tokens,
            "provider_prefix_unique_count": len(set(provider_prefix_hashes)),
            "provider_prefix_stable": bool(
                provider_prefix_hashes and len(set(provider_prefix_hashes)) == 1
            ),
            "request_trace_match_count": trace_match_count,
        }

    complete_metrics = provider_metrics(provider_calls)
    partial_metrics = provider_metrics(partial_provider_calls)

    artifact_paths = [
        path
        for path in (run_dir / "tool-output-artifacts").glob("*.bin")
        if path.is_file()
    ]
    journal_path = run_dir / "llm_calls.journal.jsonl"
    tool_journal_path = run_dir / "tool_actions.journal.jsonl"
    tool_recovery = read_tool_action_journal(tool_journal_path)
    partial_trajectory_valid = bool(
        partial_trajectory_path.exists()
        and isinstance(partial_trajectory, dict)
        and partial_trajectory.get("schema_version")
        == "aworld.raw-trajectory.partial/v1"
        and partial_trajectory.get("completion_state") == "incomplete"
        and isinstance(recovery, dict)
        and recovery.get("partial_raw_trajectory_sha256")
        == sha256_file(partial_trajectory_path)
    )
    return {
        "provider_call_count": len(provider_calls)
        if isinstance(provider_calls, list)
        else 0,
        "partial_provider_call_count": (
            len(partial_provider_calls)
            if isinstance(partial_provider_calls, list)
            else 0
        ),
        "inflight_capture_available": bool(
            isinstance(recovery, dict) and recovery.get("status") == "available"
        ),
        "inflight_capture_classification": (
            recovery.get("classification") if isinstance(recovery, dict) else None
        ),
        "trajectory_generation_state": (
            recovery.get("trajectory_generation_state")
            if isinstance(recovery, dict)
            else None
        ),
        "llm_call_journal_bytes": (
            journal_path.stat().st_size if journal_path.exists() else 0
        ),
        "llm_call_journal_valid_records": (
            int(recovery.get("valid_record_count") or 0)
            if isinstance(recovery, dict)
            else 0
        ),
        **complete_metrics,
        "partial_provider_request_bytes": partial_metrics["provider_request_bytes"],
        "partial_prompt_tokens": partial_metrics["prompt_tokens"],
        "partial_completion_tokens": partial_metrics["completion_tokens"],
        "partial_cache_read_tokens": partial_metrics["cache_read_tokens"],
        "partial_provider_prefix_unique_count": partial_metrics[
            "provider_prefix_unique_count"
        ],
        "partial_provider_prefix_stable": partial_metrics["provider_prefix_stable"],
        "partial_request_trace_match_count": partial_metrics[
            "request_trace_match_count"
        ],
        "request_trace_match_rate": (
            complete_metrics["request_trace_match_count"] / len(provider_calls)
            if isinstance(provider_calls, list) and provider_calls
            else 0.0
        ),
        "trajectory_items": len(trajectory) if isinstance(trajectory, list) else 0,
        "offloaded_artifact_count": len(artifact_paths),
        "offloaded_artifact_bytes": sum(path.stat().st_size for path in artifact_paths),
        "provider_truth_available": provider_calls_path.exists()
        and bool(provider_calls),
        "raw_trajectory_available": bool(
            trajectory_path.exists()
            and "raw_trajectory_malformed" not in parse_failures
        ),
        "partial_raw_trajectory_available": partial_trajectory_valid,
        "partial_raw_trajectory_call_count": (
            len(partial_trajectory.get("calls", [])) if partial_trajectory_valid else 0
        ),
        "partial_raw_trajectory_tool_event_count": (
            len(partial_trajectory.get("tool_events", []))
            if partial_trajectory_valid
            else 0
        ),
        "tool_action_journal_bytes": (
            tool_journal_path.stat().st_size if tool_journal_path.exists() else 0
        ),
        "tool_action_journal_valid_records": tool_recovery.valid_record_count,
        "capture_integrity_available": bool(
            isinstance(capture, dict)
            and capture.get("provider_capture_gate_passed") is True
            and isinstance(journal_continuity, dict)
            and journal_continuity.get("snapshots_match") is True
        ),
        "request_trace_match_available": bool(
            isinstance(provider_calls, list)
            and provider_calls
            and all(
                isinstance(call, dict) and call.get("request_trace_match") is True
                for call in provider_calls
            )
        ),
        "evidence_parse_error_count": len(parse_failures),
        "evidence_parse_error_reason_codes": sorted(parse_failures),
    }


def summarize_results(results: list[dict], baseline_variant: str) -> dict:
    by_variant: dict[str, list[dict]] = {}
    by_pair: dict[tuple[str, int], list[dict]] = {}
    for result in results:
        by_variant.setdefault(result["variant"], []).append(result)
        by_pair.setdefault((result["task"], result["repetition"]), []).append(result)

    aggregates = {}
    for variant, variant_results in sorted(by_variant.items()):
        numeric_rewards = [
            float(result["reward"])
            for result in variant_results
            if result.get("reward") not in (None, "")
        ]
        aggregates[variant] = {
            "run_count": len(variant_results),
            "reward_mean": statistics.fmean(numeric_rewards)
            if numeric_rewards
            else None,
            "provider_truth_rate": sum(
                bool(result["context_metrics"]["provider_truth_available"])
                for result in variant_results
            )
            / len(variant_results),
            "capture_integrity_rate": sum(
                bool(result["context_metrics"].get("capture_integrity_available"))
                for result in variant_results
            )
            / len(variant_results),
            "request_trace_match_rate": sum(
                bool(result["context_metrics"].get("request_trace_match_available"))
                for result in variant_results
            )
            / len(variant_results),
            "provider_prefix_stability_rate": sum(
                bool(result["context_metrics"].get("provider_prefix_stable"))
                for result in variant_results
            )
            / len(variant_results),
            "median_provider_request_bytes": statistics.median(
                result["context_metrics"]["provider_request_bytes"]
                for result in variant_results
            ),
            "median_prompt_tokens": statistics.median(
                result["context_metrics"]["prompt_tokens"] for result in variant_results
            ),
            "median_cache_read_tokens": statistics.median(
                result["context_metrics"].get("cache_read_tokens", 0)
                for result in variant_results
            ),
            "median_offloaded_artifact_bytes": statistics.median(
                result["context_metrics"]["offloaded_artifact_bytes"]
                for result in variant_results
            ),
            "median_llm_call_journal_bytes": statistics.median(
                result["context_metrics"].get("llm_call_journal_bytes", 0)
                for result in variant_results
            ),
            "median_tool_action_journal_bytes": statistics.median(
                result["context_metrics"].get("tool_action_journal_bytes", 0)
                for result in variant_results
            ),
            "median_partial_provider_request_bytes": statistics.median(
                result["context_metrics"].get("partial_provider_request_bytes", 0)
                for result in variant_results
            ),
            "median_partial_prompt_tokens": statistics.median(
                result["context_metrics"].get("partial_prompt_tokens", 0)
                for result in variant_results
            ),
            "median_partial_cache_read_tokens": statistics.median(
                result["context_metrics"].get("partial_cache_read_tokens", 0)
                for result in variant_results
            ),
        }

    paired = []
    seed_mismatches = []
    for (task, repetition), pair_results in sorted(by_pair.items()):
        baseline = next(
            (
                result
                for result in pair_results
                if result["variant"] == baseline_variant
            ),
            None,
        )
        if baseline is None or baseline.get("reward") in (None, ""):
            continue
        for candidate in pair_results:
            if candidate is baseline or candidate.get("reward") in (None, ""):
                continue
            if candidate.get("model_seed") != baseline.get("model_seed"):
                seed_mismatches.append(
                    {
                        "task": task,
                        "repetition": repetition,
                        "candidate": candidate["variant"],
                    }
                )
                continue
            pair_record = {
                "task": task,
                "repetition": repetition,
                "baseline": baseline_variant,
                "candidate": candidate["variant"],
                "reward_delta": float(candidate["reward"]) - float(baseline["reward"]),
                "provider_request_bytes_delta": (
                    candidate["context_metrics"]["provider_request_bytes"]
                    - baseline["context_metrics"]["provider_request_bytes"]
                ),
                "prompt_tokens_delta": (
                    candidate["context_metrics"]["prompt_tokens"]
                    - baseline["context_metrics"]["prompt_tokens"]
                ),
                "offloaded_artifact_bytes_delta": (
                    candidate["context_metrics"]["offloaded_artifact_bytes"]
                    - baseline["context_metrics"]["offloaded_artifact_bytes"]
                ),
            }
            if isinstance(candidate.get("model_seed"), int):
                pair_record["model_seed"] = candidate["model_seed"]
            paired.append(pair_record)
    complete_seeds_by_candidate: dict[str, set[int]] = {}
    for pair in paired:
        candidate = pair["candidate"]
        seed = pair.get("model_seed")
        if isinstance(seed, int):
            complete_seeds_by_candidate.setdefault(candidate, set()).add(seed)
    complete_seed_count_by_candidate = {
        candidate: len(seeds)
        for candidate, seeds in sorted(complete_seeds_by_candidate.items())
    }
    minimum_seed_gate_passed = bool(complete_seed_count_by_candidate) and all(
        count >= 3 for count in complete_seed_count_by_candidate.values()
    )
    return {
        "schema_version": "aworld.context-eval-summary/v1",
        "baseline_variant": baseline_variant,
        "aggregates": aggregates,
        "paired_deltas": paired,
        "minimum_seed_gate": {
            "required_complete_pairs_per_candidate": 3,
            "complete_seed_count_by_candidate": complete_seed_count_by_candidate,
            "passed": minimum_seed_gate_passed,
            "seed_mismatch_count": len(seed_mismatches),
        },
        "decision_note": (
            "This summary is descriptive. Apply the spec's sample-size, confidence interval, "
            "hard-gate, and cross-workload requirements before claiming framework benefit."
        ),
    }


def execute_job(
    *,
    docker: str,
    fixture: TaskFixture,
    image: str,
    variant_name: str,
    variant_path: Path | None,
    repetition: int,
    output_dir: Path,
    max_steps: int,
    keep_container: bool,
    verifier_mode: str,
    agent_timeout_sec_override: float | None = None,
    verifier_timeout_sec_override: float | None = None,
    external_mcp_config_path: Path | None = None,
    model_seed: int | None = None,
) -> dict:
    run_dir = (
        output_dir / "runs" / fixture.name / variant_name / f"repeat-{repetition:02d}"
    )
    verifier_dir = run_dir / "verifier"
    verifier_dir.mkdir(parents=True, exist_ok=True)
    try:
        verifier_environment, verifier_environment_evidence = (
            resolve_verifier_environment(fixture.config, os.environ)
        )
    except VerifierEnvironmentUnavailable as exc:
        result = {
            "schema_version": "aworld.context-eval-result/v1",
            "task": fixture.name,
            "variant": variant_name,
            "repetition": repetition,
            "agent_exit_code": None,
            "verifier_exit_code": None,
            "verifier_mode": verifier_mode,
            "reward": None,
            "failure": {
                "stage": "verifier_preflight",
                "reason_code": "verifier_environment_unavailable",
                "target_name": exc.target_name,
                "source_name": exc.source_name,
            },
            "verifier_environment": {
                **verifier_environment_contract(fixture.config),
                "status": "unavailable",
                "reason_code": "missing_host_environment",
            },
            "container_image_id": image if image.startswith("sha256:") else None,
            "task_archive_sha256": fixture.archive_sha256,
            "context_metrics": collect_context_metrics(run_dir),
        }
        write_json(run_dir / "result.json", result)
        return result
    container = f"aworld-eval-{fixture.name[:24]}-{uuid.uuid4().hex[:10]}"
    environment = fixture.config["environment"]
    command = [docker, "run", "-d", "--name", container]
    if environment.get("cpus"):
        command.extend(["--cpus", str(environment["cpus"])])
    if environment.get("memory_mb"):
        command.extend(["--memory", f"{environment['memory_mb']}m"])
    verifier_mount = (
        "/verifier" if fixture.verifier_directory == "verifier" else "/tests"
    )
    command.extend(["-v", f"{fixture.verifier.resolve()}:{verifier_mount}:ro"])
    if fixture.skills is not None:
        command.extend(["-v", f"{fixture.skills.resolve()}:/aworld-skills:ro"])
    command.extend(
        [
            "-v",
            f"{verifier_dir.resolve()}:/logs/verifier",
            image,
            "sleep",
            "infinity",
        ]
    )
    started = run_command(command, capture_output=True)
    require_success(started, f"start container for {fixture.name}")

    agent_result = None
    verifier_result = None
    try:
        agent_command = [
            sys.executable,
            str(RUNNER),
            "--container",
            container,
            "--instruction",
            str(fixture.instruction),
            "--output-dir",
            str(run_dir),
            "--max-steps",
            str(max_steps),
        ]
        if model_seed is not None:
            agent_command.extend(["--model-seed", str(model_seed)])
        if variant_path:
            agent_command.extend(["--variant-config", str(variant_path)])
        if fixture.skills is not None:
            agent_command.extend(["--skills-directory", str(fixture.skills.resolve())])
        declared_artifacts = fixture.config.get("artifacts", [])
        if declared_artifacts is None:
            declared_artifacts = []
        if not isinstance(declared_artifacts, list) or any(
            not isinstance(path, str) or not path.strip() for path in declared_artifacts
        ):
            raise ValueError("task artifacts must be a list of non-empty paths")
        for artifact_path in declared_artifacts:
            agent_command.extend(["--required-artifact", artifact_path])
        for environment_name in sorted(verifier_environment):
            agent_command.extend(["--completion-env", environment_name])
        if external_mcp_config_path is not None:
            agent_command.extend(
                ["--mcp-config", str(external_mcp_config_path.resolve())]
            )
        agent_command.extend(["--completion-verifier-mode", verifier_mode])
        configured_agent_timeout = float(
            fixture.config.get("agent", {}).get("timeout_sec", 900)
        )
        agent_timeout = float(
            agent_timeout_sec_override
            if agent_timeout_sec_override is not None
            else configured_agent_timeout + 60
        )
        agent_environment = os.environ.copy()
        repo_root = str(RUNNER.resolve().parents[2])
        existing_pythonpath = agent_environment.get("PYTHONPATH")
        agent_environment["PYTHONPATH"] = (
            repo_root
            if not existing_pythonpath
            else repo_root + os.pathsep + existing_pythonpath
        )
        agent_environment.update(verifier_environment)
        try:
            agent_result = run_command(
                agent_command,
                capture_output=True,
                timeout=agent_timeout,
                env=agent_environment,
            )
        except KeyboardInterrupt:
            recovery = recover_inflight_capture(run_dir)
            write_json(
                run_dir / "result.json",
                {
                    "schema_version": "aworld.context-eval-result/v1",
                    "task": fixture.name,
                    "variant": variant_name,
                    "repetition": repetition,
                    "model_seed": model_seed,
                    "agent_exit_code": None,
                    "verifier_exit_code": None,
                    "verifier_mode": verifier_mode,
                    "reward": None,
                    "failure": {
                        "stage": "agent",
                        "reason_code": "experiment_interrupted",
                    },
                    "capture_recovery": recovery,
                    "container_image_id": container_image_id(docker, container),
                    "task_archive_sha256": fixture.archive_sha256,
                    "verifier_environment": verifier_environment_evidence,
                    "context_metrics": collect_context_metrics(run_dir),
                },
            )
            raise
        except subprocess.TimeoutExpired as exc:
            (run_dir / "agent.stdout.log").write_text(
                timeout_output(exc, "stdout"), encoding="utf-8"
            )
            (run_dir / "agent.stderr.log").write_text(
                timeout_output(exc, "stderr"), encoding="utf-8"
            )
            recovery = recover_inflight_capture(run_dir)
            result = {
                "schema_version": "aworld.context-eval-result/v1",
                "task": fixture.name,
                "variant": variant_name,
                "repetition": repetition,
                "model_seed": model_seed,
                "agent_exit_code": None,
                "verifier_exit_code": None,
                "verifier_mode": verifier_mode,
                "reward": None,
                "failure": {
                    "stage": "agent",
                    "reason_code": "agent_timeout",
                    "timeout_sec": agent_timeout,
                },
                "capture_recovery": recovery,
                "container_image_id": container_image_id(docker, container),
                "task_archive_sha256": fixture.archive_sha256,
                "verifier_environment": verifier_environment_evidence,
                "context_metrics": collect_context_metrics(run_dir),
            }
            write_json(run_dir / "result.json", result)
            return result
        (run_dir / "agent.stdout.log").write_text(
            agent_result.stdout or "", encoding="utf-8"
        )
        (run_dir / "agent.stderr.log").write_text(
            agent_result.stderr or "", encoding="utf-8"
        )
        recovery = recover_inflight_capture(run_dir)

        agent_failure = None
        if agent_result.returncode != 0:
            aworld_failure = parse_aworld_run_failure(
                agent_result.stderr or "", agent_result.stdout or ""
            )
            agent_failure = {
                "stage": "agent",
                "reason_code": "agent_nonzero_exit",
                "aworld_failure": aworld_failure,
            }
            if not finalized_capture_allows_independent_verifier(recovery):
                result = {
                    "schema_version": "aworld.context-eval-result/v1",
                    "task": fixture.name,
                    "variant": variant_name,
                    "repetition": repetition,
                    "model_seed": model_seed,
                    "agent_exit_code": agent_result.returncode,
                    "verifier_exit_code": None,
                    "verifier_mode": verifier_mode,
                    "reward": None,
                    "failure": agent_failure,
                    "capture_recovery": recovery,
                    "container_image_id": container_image_id(docker, container),
                    "task_archive_sha256": fixture.archive_sha256,
                    "verifier_environment": verifier_environment_evidence,
                    "context_metrics": collect_context_metrics(run_dir),
                }
                write_json(run_dir / "result.json", result)
                return result

        verifier_timeout = float(
            verifier_timeout_sec_override
            if verifier_timeout_sec_override is not None
            else fixture.config.get("verifier", {}).get("timeout_sec", 900)
        )
        verifier_flags = [
            value for name in sorted(verifier_environment) for value in ("--env", name)
        ]
        if verifier_mode == "packaged":
            verifier_command = [
                docker,
                "exec",
                *verifier_flags,
                container,
                "/bin/bash",
                f"{verifier_mount}/test.sh",
            ]
        else:
            verifier_command = None
        verifier_process_environment = os.environ.copy()
        verifier_process_environment.update(verifier_environment)
        try:
            if verifier_mode == "python-functions":
                test_path = f"{verifier_mount}/test_outputs.py"
                verifier_result = run_python_function_verifier_sidecar(
                    docker_binary=docker,
                    container=container,
                    test_path=test_path,
                    timeout=verifier_timeout,
                    env_names=tuple(sorted(verifier_environment)),
                    scratch_root=verifier_dir,
                )
            else:
                verifier_result = run_command(
                    verifier_command,
                    capture_output=True,
                    timeout=verifier_timeout,
                    env=verifier_process_environment,
                )
        except subprocess.TimeoutExpired as exc:
            (verifier_dir / "stdout.log").write_text(
                timeout_output(exc, "stdout"), encoding="utf-8"
            )
            (verifier_dir / "stderr.log").write_text(
                timeout_output(exc, "stderr"), encoding="utf-8"
            )
            result = {
                "schema_version": "aworld.context-eval-result/v1",
                "task": fixture.name,
                "variant": variant_name,
                "repetition": repetition,
                "model_seed": model_seed,
                "agent_exit_code": agent_result.returncode,
                "verifier_exit_code": None,
                "verifier_mode": verifier_mode,
                "reward": None,
                "failure": {
                    "stage": "verifier",
                    "reason_code": "verifier_timeout",
                    "timeout_sec": verifier_timeout,
                },
                "agent_failure": agent_failure,
                "container_image_id": container_image_id(docker, container),
                "task_archive_sha256": fixture.archive_sha256,
                "verifier_environment": verifier_environment_evidence,
                "capture_recovery": recovery,
                "context_metrics": collect_context_metrics(run_dir),
            }
            write_json(run_dir / "result.json", result)
            return result
        (verifier_dir / "stdout.log").write_text(
            verifier_result.stdout or "", encoding="utf-8"
        )
        (verifier_dir / "stderr.log").write_text(
            verifier_result.stderr or "", encoding="utf-8"
        )
        reward_path = verifier_dir / "reward.txt"
        if verifier_mode == "python-functions":
            reward_path.write_text(
                "1\n" if verifier_result.returncode == 0 else "0\n",
                encoding="utf-8",
            )
        reward = (
            reward_path.read_text(encoding="utf-8").strip()
            if reward_path.exists()
            else None
        )
        result = {
            "schema_version": "aworld.context-eval-result/v1",
            "task": fixture.name,
            "variant": variant_name,
            "repetition": repetition,
            "model_seed": model_seed,
            "agent_exit_code": agent_result.returncode,
            "verifier_exit_code": verifier_result.returncode,
            "verifier_mode": verifier_mode,
            "reward": reward,
            "agent_failure": agent_failure,
            "container_image_id": container_image_id(docker, container),
            "task_archive_sha256": fixture.archive_sha256,
            "verifier_environment": verifier_environment_evidence,
            "capture_recovery": recovery,
            "context_metrics": collect_context_metrics(run_dir),
        }
        write_json(run_dir / "result.json", result)
        return result
    finally:
        if not keep_container:
            run_command([docker, "rm", "-f", container], capture_output=True)


def main() -> None:
    args = parse_args()
    if args.repeat < 1:
        raise ValueError("--repeat must be positive")
    if args.build_timeout_sec is not None and (
        not math.isfinite(args.build_timeout_sec) or args.build_timeout_sec <= 0
    ):
        raise ValueError("--build-timeout-sec must be positive")
    for option_name, value in (
        ("--agent-timeout-sec", args.agent_timeout_sec),
        ("--verifier-timeout-sec", args.verifier_timeout_sec),
        ("--model-preflight-timeout-sec", args.model_preflight_timeout_sec),
    ):
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError(f"{option_name} must be positive")
    dataset = args.dataset.resolve()
    if not dataset.is_file():
        raise FileNotFoundError(dataset)
    docker = shutil.which("docker")
    if not docker and not args.dry_run:
        raise RuntimeError("Docker is not installed or not on PATH")

    variants = [load_variant(path) for path in (args.variants or [None])]
    if len({name for name, _, _ in variants}) != len(variants):
        raise ValueError("Variant names must be unique")
    _, external_mcp_evidence = load_external_mcp_config(args.mcp_config)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_root = output_dir / "fixture"
    fixtures = [extract_task(dataset, name, fixture_root / name) for name in args.tasks]
    image_build_plans = {
        fixture.name: docker_image_build_plan(
            fixture,
            use_declared_image=args.use_declared_image,
            build_timeout_sec=args.build_timeout_sec,
            registry_rewrites=tuple(args.image_registry_rewrite),
        )
        for fixture in fixtures
    }

    jobs = [
        (fixture, variant_name, variant_path, repetition)
        for fixture in fixtures
        for variant_name, variant_path, _ in variants
        for repetition in range(1, args.repeat + 1)
    ]
    random.Random(args.seed).shuffle(jobs)
    experiment = {
        "schema_version": "aworld.context-eval-experiment/v1",
        "hypothesis": (
            "Changing only AWorld context/output policy improves quality, stability, or cost "
            "across tool-heavy workloads without task-specific prompting."
        ),
        "benchmark_adapter": (
            fixtures[0].benchmark_adapter
            if len({fixture.benchmark_adapter for fixture in fixtures}) == 1
            else "mixed"
        ),
        "dataset": str(dataset),
        "dataset_sha256": sha256_file(dataset),
        "tasks": [fixture.name for fixture in fixtures],
        "variants": [payload for _, _, payload in variants],
        "repeat": args.repeat,
        "seed": args.seed,
        "model_seeds": [
            args.seed + repetition - 1 for repetition in range(1, args.repeat + 1)
        ],
        "minimum_model_seed_count_configured": args.repeat >= 3,
        "job_order": [
            {"task": fixture.name, "variant": variant, "repetition": repetition}
            for fixture, variant, _, repetition in jobs
        ],
        "invariant_contract": [
            "task instruction",
            "system prompt",
            "model/provider/temperature",
            "container image",
            "tool surface",
            "verifier and reward",
        ],
        "anti_overfitting": "Variants cannot contain task prompts, names, expected answers, or verifier logic.",
        "normalized_cost_policy": NormalizedCostPolicy().to_dict(),
        "verifier_mode": args.verifier_mode,
        "python_function_verifier_image": (
            PYTHON_FUNCTION_VERIFIER_IMAGE
            if args.verifier_mode == "python-functions"
            else None
        ),
        "build_timeout_sec_override": args.build_timeout_sec,
        "agent_timeout_sec_override": args.agent_timeout_sec,
        "verifier_timeout_sec_override": args.verifier_timeout_sec,
        "external_mcp": external_mcp_evidence,
        "verifier_environment_contracts": {
            fixture.name: verifier_environment_contract(fixture.config)
            for fixture in fixtures
        },
        "image_registry_rewrites": [
            {"source": source, "destination": destination}
            for source, destination in args.image_registry_rewrite
        ],
        "use_declared_image": args.use_declared_image,
        "image_build_plans": image_build_plans,
        "image_resolution": {"status": "not_attempted", "images": {}},
        "model_preflight": {"status": "not_attempted"},
        "created_at_epoch": time.time(),
    }
    write_json(output_dir / "experiment_manifest.json", experiment)
    if args.dry_run:
        print(json.dumps(experiment, ensure_ascii=False, indent=2))
        return

    if args.skip_model_preflight:
        experiment["model_preflight"] = {
            "schema_version": "aworld.model-preflight/v1",
            "status": "skipped",
            "reason_code": "explicit_diagnostic_override",
        }
        experiment["minimum_model_seed_count_configured"] = False
    else:
        experiment["model_preflight"] = run_model_preflight(
            output_dir,
            timeout_sec=args.model_preflight_timeout_sec,
            model_seed=args.seed,
        )
    write_json(output_dir / "experiment_manifest.json", experiment)
    if experiment["model_preflight"].get("status") == "failed":
        raise RuntimeError(
            "Model connectivity preflight failed; benchmark jobs were not started"
        )

    assert docker is not None
    images: dict[str, str] = {}
    resolved_images: dict[str, dict[str, object]] = {}
    try:
        for fixture in fixtures:
            image = docker_image_for_task(
                fixture,
                docker,
                args.use_declared_image,
                args.build_timeout_sec,
                tuple(args.image_registry_rewrite),
            )
            resolved_image_id = docker_image_id(docker, image)
            if not isinstance(resolved_image_id, str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", resolved_image_id
            ):
                raise RuntimeError(
                    f"resolve immutable image id for {fixture.name} failed"
                )
            # Execute every paired variant against the resolved immutable image,
            # not a tag that can move between resolution and docker run.
            images[fixture.name] = resolved_image_id
            resolved_images[fixture.name] = {
                "status": "available",
                "image_ref": image,
                "image_id": resolved_image_id,
                "build_context_sha256": image_build_plans[fixture.name][
                    "build_context_sha256"
                ],
            }
    except Exception as exc:
        failed_task = next(
            (fixture.name for fixture in fixtures if fixture.name not in images),
            "unknown",
        )
        resolved_images[failed_task] = {
            "status": "failed",
            "reason_code": (
                "docker_build_timeout"
                if isinstance(exc.__cause__, subprocess.TimeoutExpired)
                else "docker_image_resolution_failed"
            ),
            "exception_type": type(exc).__name__,
            "build_context_sha256": image_build_plans.get(failed_task, {}).get(
                "build_context_sha256"
            ),
        }
        experiment["image_resolution"] = {
            "status": "failed",
            "images": resolved_images,
        }
        write_json(output_dir / "experiment_manifest.json", experiment)
        raise
    experiment["image_resolution"] = {
        "status": "available",
        "images": resolved_images,
    }
    write_json(output_dir / "experiment_manifest.json", experiment)
    results = []
    for fixture, variant_name, variant_path, repetition in jobs:
        results.append(
            execute_job(
                docker=docker,
                fixture=fixture,
                image=images[fixture.name],
                variant_name=variant_name,
                variant_path=variant_path,
                repetition=repetition,
                output_dir=output_dir,
                max_steps=args.max_steps,
                keep_container=args.keep_containers,
                verifier_mode=args.verifier_mode,
                agent_timeout_sec_override=args.agent_timeout_sec,
                verifier_timeout_sec_override=args.verifier_timeout_sec,
                external_mcp_config_path=args.mcp_config,
                model_seed=args.seed + repetition - 1,
            )
        )
    write_json(output_dir / "results.json", results)
    summary = summarize_results(results, variants[0][0])
    write_json(output_dir / "summary.json", summary)
    experiment["minimum_seed_gate"] = summary["minimum_seed_gate"]
    write_json(output_dir / "experiment_manifest.json", experiment)
    print(
        json.dumps(
            {"runs": len(results), "output_dir": str(output_dir)}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
