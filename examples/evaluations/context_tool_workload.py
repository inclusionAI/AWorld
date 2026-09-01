"""Run a paired, non-Terminal-Bench Tool/research Context evaluation.

Cases are immutable directories containing an instruction, an agent-visible
workspace, and a host-only expected result.  Context variants never receive the
expected result and may only change fields accepted by docker_terminal_bench.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SANDBOX_EXAMPLES = REPO_ROOT / "examples" / "sandbox"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SANDBOX_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(SANDBOX_EXAMPLES))

from docker_terminal_bench import _load_variant  # noqa: E402
from terminal_bench_context_eval import (  # noqa: E402
    collect_context_metrics,
    summarize_results,
)
from aworld.evaluations.normalized_cost import NormalizedCostPolicy  # noqa: E402


RUNNER = SANDBOX_EXAMPLES / "docker_terminal_bench.py"


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def verify_case(workspace: Path, expected_path: Path) -> dict:
    """Score only the final artifact; never trust TaskResponse self-report."""
    expected_spec = json.loads(expected_path.read_text(encoding="utf-8"))
    artifact_name = expected_spec.get("artifact", "result.json")
    expected = expected_spec.get("exact")
    artifact = workspace / artifact_name
    errors: list[str] = []
    actual = None
    if not artifact.is_file():
        errors.append("artifact_missing")
    else:
        try:
            actual = json.loads(artifact.read_text(encoding="utf-8"))
        except Exception:
            errors.append("artifact_invalid_json")
    if not errors and actual != expected:
        errors.append("artifact_exact_value_mismatch")
    result = {
        "schema_version": "aworld.local-tool-verifier/v1",
        "reward": 0 if errors else 1,
        "artifact": artifact_name,
        "expected_hash": canonical_sha256(expected),
        "actual_hash": canonical_sha256(actual) if actual is not None else None,
        "errors": errors,
    }
    return result


def load_case(case_dir: Path) -> dict:
    case_dir = case_dir.resolve()
    metadata_path = case_dir / "case.json"
    instruction = case_dir / "instruction.md"
    workspace = case_dir / "workspace"
    expected = case_dir / "expected.json"
    missing = [
        str(path)
        for path in (metadata_path, instruction, workspace, expected)
        if not path.exists()
    ]
    if missing:
        raise ValueError("Incomplete local Tool workload case: " + ", ".join(missing))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    case_id = str(metadata.get("case_id") or "").strip()
    if not case_id or metadata.get("workload_kind") == "terminal_bench":
        raise ValueError("case requires a non-Terminal-Bench case_id/workload_kind")
    return {
        "case_id": case_id,
        "workload_kind": str(metadata.get("workload_kind") or "tool_research"),
        "verifier_id": str(metadata.get("verifier_id") or "exact-json-v1"),
        "root": case_dir,
        "instruction": instruction,
        "workspace": workspace,
        "expected": expected,
        "checksum": tree_sha256(case_dir),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", action="append", type=Path, required=True)
    parser.add_argument("--variant-config", action="append", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260901)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--image", default="python:3.12-slim")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def execute_job(
    *,
    docker: str,
    case: dict,
    variant_name: str,
    variant_path: Path,
    repetition: int,
    output_dir: Path,
    image: str,
    max_steps: int,
) -> dict:
    run_dir = output_dir / "runs" / case["case_id"] / variant_name / f"repeat-{repetition:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace = run_dir / "workspace"
    shutil.copytree(case["workspace"], workspace, dirs_exist_ok=True)
    container = f"aworld-tool-eval-{uuid.uuid4().hex[:12]}"
    started = subprocess.run(
        [
            docker,
            "run",
            "-d",
            "--name",
            container,
            "-v",
            f"{workspace.resolve()}:/workspace",
            "-w",
            "/workspace",
            image,
            "sleep",
            "infinity",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        raise RuntimeError(started.stderr.strip() or "failed to start Tool workload container")
    try:
        command = [
            sys.executable,
            str(RUNNER),
            "--container",
            container,
            "--instruction",
            str(case["instruction"]),
            "--output-dir",
            str(run_dir),
            "--variant-config",
            str(variant_path),
            "--max-steps",
            str(max_steps),
        ]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
        agent = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=960,
            env=environment,
        )
        (run_dir / "agent.stdout.log").write_text(agent.stdout or "", encoding="utf-8")
        (run_dir / "agent.stderr.log").write_text(agent.stderr or "", encoding="utf-8")
        verifier = verify_case(workspace, case["expected"])
        write_json(run_dir / "verifier.json", verifier)
        result = {
            "schema_version": "aworld.context-eval-result/v1",
            "task": case["case_id"],
            "workload_kind": case["workload_kind"],
            "variant": variant_name,
            "repetition": repetition,
            "agent_exit_code": agent.returncode,
            "verifier_exit_code": 0,
            "reward": verifier["reward"],
            "case_checksum": case["checksum"],
            "context_metrics": collect_context_metrics(run_dir),
        }
        write_json(run_dir / "result.json", result)
        return result
    finally:
        subprocess.run(
            [docker, "rm", "-f", container],
            capture_output=True,
            text=True,
            check=False,
        )


def main() -> None:
    args = parse_args()
    if args.repeat < 1:
        raise ValueError("--repeat must be positive")
    cases = [load_case(path) for path in args.case_dir]
    variants = [
        (_load_variant(path)["name"], path.resolve(), _load_variant(path))
        for path in args.variant_config
    ]
    if len({name for name, _, _ in variants}) != len(variants):
        raise ValueError("variant names must be unique")
    jobs = [
        (case, name, path, repetition)
        for case in cases
        for name, path, _ in variants
        for repetition in range(1, args.repeat + 1)
    ]
    random.Random(args.seed).shuffle(jobs)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "aworld.context-eval-experiment/v1",
        "hypothesis": (
            "Context and Tool-output governance improves quality, stability, or cost "
            "without case-specific prompting or verifier access."
        ),
        "benchmark_adapter": "local-tool-research/v1",
        "cases": [
            {
                "case_id": case["case_id"],
                "workload_kind": case["workload_kind"],
                "checksum": case["checksum"],
                "verifier_id": case["verifier_id"],
            }
            for case in cases
        ],
        "variants": [payload for _, _, payload in variants],
        "image": args.image,
        "repeat": args.repeat,
        "seed": args.seed,
        "job_order": [
            {"case_id": case["case_id"], "variant": name, "repetition": repetition}
            for case, name, _, repetition in jobs
        ],
        "invariant_contract": [
            "instruction and agent-visible workspace",
            "system prompt, model, provider, temperature, and Tool surface",
            "container image",
            "host-only independent verifier",
        ],
        "anti_overfitting": "Variants cannot contain case ids, prompts, expected values, or verifier logic.",
        "normalized_cost_policy": NormalizedCostPolicy().to_dict(),
        "created_at_epoch": time.time(),
    }
    write_json(output_dir / "experiment_manifest.json", manifest)
    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker is required")
    inspect = subprocess.run([docker, "image", "inspect", args.image], capture_output=True)
    if inspect.returncode != 0:
        pull = subprocess.run([docker, "pull", args.image], capture_output=True, text=True)
        if pull.returncode != 0:
            raise RuntimeError(pull.stderr.strip() or f"cannot pull {args.image}")
    results = [
        execute_job(
            docker=docker,
            case=case,
            variant_name=name,
            variant_path=path,
            repetition=repetition,
            output_dir=output_dir,
            image=args.image,
            max_steps=args.max_steps,
        )
        for case, name, path, repetition in jobs
    ]
    write_json(output_dir / "results.json", results)
    write_json(output_dir / "summary.json", summarize_results(results, variants[0][0]))
    print(json.dumps({"runs": len(results), "output_dir": str(output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
