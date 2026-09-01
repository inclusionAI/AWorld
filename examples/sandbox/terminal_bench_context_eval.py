"""Run reproducible, paired context-management experiments on Terminal Bench.

The benchmark is a workload adapter, not an optimization target: variants may
change only AWorld context and Tool output policies.  Instructions, system
prompt, model settings, container image, verifier, and reward path are invariant.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
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

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aworld.evaluations.normalized_cost import NormalizedCostPolicy


RUNNER = Path(__file__).with_name("docker_terminal_bench.py")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_command(command: list[str], *, timeout: float | None = None, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=False, timeout=timeout, text=True, **kwargs)


def require_success(result: subprocess.CompletedProcess, operation: str) -> None:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{operation} failed ({result.returncode}): {detail}")


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

    @property
    def instruction(self) -> Path:
        return self.root / "instruction.md"

    @property
    def environment(self) -> Path:
        return self.root / "environment"

    @property
    def tests(self) -> Path:
        return self.root / "tests"


def extract_task(dataset: Path, task_name: str, destination: Path) -> TaskFixture:
    archive_name = f"tasks/{task_name}.tar.gz"
    with zipfile.ZipFile(dataset) as package:
        try:
            archive_bytes = package.read(archive_name)
        except KeyError as exc:
            available = sorted(
                Path(name).name.removesuffix(".tar.gz")
                for name in package.namelist()
                if name.startswith("tasks/") and name.endswith(".tar.gz")
            )
            raise ValueError(f"Unknown task {task_name!r}; dataset contains {len(available)} tasks") from exc

    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar.gz") as temporary:
        temporary.write(archive_bytes)
        temporary.flush()
        with tarfile.open(temporary.name, "r:gz") as archive:
            safe_extract_tar(archive, destination)

    task_files = list(destination.rglob("task.toml"))
    if len(task_files) != 1:
        raise ValueError(f"Expected one task.toml for {task_name}, found {len(task_files)}")
    root = task_files[0].parent
    required = [root / "instruction.md", root / "environment" / "Dockerfile", root / "tests" / "test.sh"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ValueError(f"Task {task_name} is incomplete: {', '.join(missing)}")
    return TaskFixture(
        name=task_name,
        root=root,
        archive_sha256=hashlib.sha256(archive_bytes).hexdigest(),
        config=tomllib.loads(task_files[0].read_text(encoding="utf-8")),
    )


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
) -> str:
    declared = fixture.config["environment"].get("docker_image")
    if use_declared_image:
        if not declared:
            raise ValueError(f"Task {fixture.name} has no declared docker_image")
        inspect = run_command([docker, "image", "inspect", declared], capture_output=True)
        if inspect.returncode != 0:
            pull = run_command([docker, "pull", declared], capture_output=True)
            require_success(pull, f"pull image for {fixture.name}")
        return declared

    dockerfile_sha = sha256_file(fixture.environment / "Dockerfile")[:16]
    image = f"aworld-context-eval:{dockerfile_sha}"
    inspect = run_command([docker, "image", "inspect", image], capture_output=True)
    if inspect.returncode != 0:
        timeout = (
            build_timeout_sec
            if build_timeout_sec is not None
            else float(fixture.config["environment"].get("build_timeout_sec", 600))
        )
        try:
            build = run_command(
                [docker, "build", "--label", "aworld.context-eval=true", "-t", image, "."],
                cwd=fixture.environment,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.stderr or exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode(errors="replace")
            detail = str(output).strip()
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"build image for {fixture.name} timed out after {timeout:g} seconds{suffix}"
            ) from exc
        require_success(build, f"build image for {fixture.name}")
    return image


def container_image_id(docker: str, target: str) -> str | None:
    result = run_command([docker, "inspect", "--format", "{{.Image}}", target], capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else None


def collect_context_metrics(run_dir: Path) -> dict:
    provider_calls_path = run_dir / "provider_calls.json"
    trajectory_path = run_dir / "raw_trajectory.json"
    provider_calls = (
        json.loads(provider_calls_path.read_text(encoding="utf-8"))
        if provider_calls_path.exists()
        else []
    )
    trajectory = (
        json.loads(trajectory_path.read_text(encoding="utf-8"))
        if trajectory_path.exists()
        else []
    )
    manifest_path = run_dir / "run_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    capture = manifest.get("capture") if isinstance(manifest, dict) else {}
    continuity = capture.get("llm_call_continuity") if isinstance(capture, dict) else {}
    prompt_tokens = completion_tokens = cache_read_tokens = 0
    provider_request_bytes = trace_match_count = 0
    for call in provider_calls if isinstance(provider_calls, list) else []:
        if not isinstance(call, dict):
            continue
        provider_request = call.get("provider_request") or {}
        request = provider_request.get("payload") or call.get("request") or {}
        provider_request_bytes += len(
            json.dumps(request, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        )
        usage = call.get("usage_normalized") or call.get("usage") or {}
        raw_usage = call.get("usage_raw") or usage
        prompt_details = raw_usage.get("prompt_tokens_details") or {}
        prompt_tokens += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        cache_read_tokens += int(
            raw_usage.get("cache_hit_tokens")
            or raw_usage.get("cache_read_input_tokens")
            or prompt_details.get("cached_tokens")
            or 0
        )
        trace_match_count += int(call.get("request_trace_match") is True)

    artifact_paths = [
        path for path in (run_dir / "tool-output-artifacts").glob("*.bin")
        if path.is_file()
    ]
    return {
        "provider_call_count": len(provider_calls) if isinstance(provider_calls, list) else 0,
        "provider_request_bytes": provider_request_bytes,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_read_tokens": cache_read_tokens,
        "request_trace_match_count": trace_match_count,
        "request_trace_match_rate": (
            trace_match_count / len(provider_calls)
            if isinstance(provider_calls, list) and provider_calls
            else 0.0
        ),
        "trajectory_items": len(trajectory) if isinstance(trajectory, list) else 0,
        "offloaded_artifact_count": len(artifact_paths),
        "offloaded_artifact_bytes": sum(path.stat().st_size for path in artifact_paths),
        "provider_truth_available": provider_calls_path.exists() and bool(provider_calls),
        "raw_trajectory_available": trajectory_path.exists(),
        "capture_integrity_available": bool(
            isinstance(capture, dict)
            and capture.get("provider_capture_gate_passed") is True
            and isinstance(continuity, dict)
            and continuity.get("snapshots_match") is True
        ),
        "request_trace_match_available": bool(
            isinstance(provider_calls, list)
            and provider_calls
            and all(
                isinstance(call, dict) and call.get("request_trace_match") is True
                for call in provider_calls
            )
        ),
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
            "reward_mean": statistics.fmean(numeric_rewards) if numeric_rewards else None,
            "provider_truth_rate": sum(
                bool(result["context_metrics"]["provider_truth_available"])
                for result in variant_results
            ) / len(variant_results),
            "capture_integrity_rate": sum(
                bool(result["context_metrics"].get("capture_integrity_available"))
                for result in variant_results
            ) / len(variant_results),
            "request_trace_match_rate": sum(
                bool(result["context_metrics"].get("request_trace_match_available"))
                for result in variant_results
            ) / len(variant_results),
            "median_provider_request_bytes": statistics.median(
                result["context_metrics"]["provider_request_bytes"] for result in variant_results
            ),
            "median_prompt_tokens": statistics.median(
                result["context_metrics"]["prompt_tokens"] for result in variant_results
            ),
            "median_cache_read_tokens": statistics.median(
                result["context_metrics"].get("cache_read_tokens", 0)
                for result in variant_results
            ),
            "median_offloaded_artifact_bytes": statistics.median(
                result["context_metrics"]["offloaded_artifact_bytes"] for result in variant_results
            ),
        }

    paired = []
    for (task, repetition), pair_results in sorted(by_pair.items()):
        baseline = next(
            (result for result in pair_results if result["variant"] == baseline_variant),
            None,
        )
        if baseline is None or baseline.get("reward") in (None, ""):
            continue
        for candidate in pair_results:
            if candidate is baseline or candidate.get("reward") in (None, ""):
                continue
            paired.append(
                {
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
            )
    return {
        "schema_version": "aworld.context-eval-summary/v1",
        "baseline_variant": baseline_variant,
        "aggregates": aggregates,
        "paired_deltas": paired,
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
) -> dict:
    run_dir = output_dir / "runs" / fixture.name / variant_name / f"repeat-{repetition:02d}"
    verifier_dir = run_dir / "verifier"
    verifier_dir.mkdir(parents=True, exist_ok=True)
    container = f"aworld-eval-{fixture.name[:24]}-{uuid.uuid4().hex[:10]}"
    environment = fixture.config["environment"]
    command = [docker, "run", "-d", "--name", container]
    if environment.get("cpus"):
        command.extend(["--cpus", str(environment["cpus"])])
    if environment.get("memory_mb"):
        command.extend(["--memory", f"{environment['memory_mb']}m"])
    command.extend(
        [
            "-v",
            f"{fixture.tests.resolve()}:/tests:ro",
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
        if variant_path:
            agent_command.extend(["--variant-config", str(variant_path)])
        agent_timeout = float(fixture.config.get("agent", {}).get("timeout_sec", 900)) + 60
        agent_environment = os.environ.copy()
        repo_root = str(RUNNER.resolve().parents[2])
        existing_pythonpath = agent_environment.get("PYTHONPATH")
        agent_environment["PYTHONPATH"] = (
            repo_root if not existing_pythonpath else repo_root + os.pathsep + existing_pythonpath
        )
        agent_result = run_command(
            agent_command,
            capture_output=True,
            timeout=agent_timeout,
            env=agent_environment,
        )
        (run_dir / "agent.stdout.log").write_text(agent_result.stdout or "", encoding="utf-8")
        (run_dir / "agent.stderr.log").write_text(agent_result.stderr or "", encoding="utf-8")

        verifier_timeout = float(fixture.config.get("verifier", {}).get("timeout_sec", 900))
        if verifier_mode == "packaged":
            verifier_command = [docker, "exec", container, "/bin/bash", "/tests/test.sh"]
        else:
            verifier_program = """
import importlib.util
import inspect

spec = importlib.util.spec_from_file_location("terminal_bench_verifier", "/tests/test_outputs.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
tests = [
    value
    for name, value in sorted(vars(module).items())
    if name.startswith("test_") and callable(value)
]
if not tests:
    raise RuntimeError("verifier contains no test_* functions")
unsupported = [test.__name__ for test in tests if inspect.signature(test).parameters]
if unsupported:
    raise RuntimeError("python-functions verifier does not support fixtures: " + ",".join(unsupported))
for test in tests:
    test()
""".strip()
            verifier_command = [docker, "exec", container, "python3", "-c", verifier_program]
        verifier_result = run_command(
            verifier_command,
            capture_output=True,
            timeout=verifier_timeout,
        )
        (verifier_dir / "stdout.log").write_text(verifier_result.stdout or "", encoding="utf-8")
        (verifier_dir / "stderr.log").write_text(verifier_result.stderr or "", encoding="utf-8")
        reward_path = verifier_dir / "reward.txt"
        if verifier_mode == "python-functions":
            reward_path.write_text(
                "1\n" if verifier_result.returncode == 0 else "0\n",
                encoding="utf-8",
            )
        reward = reward_path.read_text(encoding="utf-8").strip() if reward_path.exists() else None
        result = {
            "schema_version": "aworld.context-eval-result/v1",
            "task": fixture.name,
            "variant": variant_name,
            "repetition": repetition,
            "agent_exit_code": agent_result.returncode,
            "verifier_exit_code": verifier_result.returncode,
            "verifier_mode": verifier_mode,
            "reward": reward,
            "container_image_id": container_image_id(docker, container),
            "task_archive_sha256": fixture.archive_sha256,
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
    dataset = args.dataset.resolve()
    if not dataset.is_file():
        raise FileNotFoundError(dataset)
    docker = shutil.which("docker")
    if not docker and not args.dry_run:
        raise RuntimeError("Docker is not installed or not on PATH")

    variants = [load_variant(path) for path in (args.variants or [None])]
    if len({name for name, _, _ in variants}) != len(variants):
        raise ValueError("Variant names must be unique")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_root = output_dir / "fixture"
    fixtures = [extract_task(dataset, name, fixture_root / name) for name in args.tasks]

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
        "benchmark_adapter": "terminal-bench-2.1",
        "dataset": str(dataset),
        "dataset_sha256": sha256_file(dataset),
        "tasks": [fixture.name for fixture in fixtures],
        "variants": [payload for _, _, payload in variants],
        "repeat": args.repeat,
        "seed": args.seed,
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
        "build_timeout_sec_override": args.build_timeout_sec,
        "use_declared_image": args.use_declared_image,
        "created_at_epoch": time.time(),
    }
    write_json(output_dir / "experiment_manifest.json", experiment)
    if args.dry_run:
        print(json.dumps(experiment, ensure_ascii=False, indent=2))
        return

    assert docker is not None
    images = {
        fixture.name: docker_image_for_task(
            fixture,
            docker,
            args.use_declared_image,
            args.build_timeout_sec,
        )
        for fixture in fixtures
    }
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
            )
        )
    write_json(output_dir / "results.json", results)
    write_json(output_dir / "summary.json", summarize_results(results, variants[0][0]))
    print(json.dumps({"runs": len(results), "output_dir": str(output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
