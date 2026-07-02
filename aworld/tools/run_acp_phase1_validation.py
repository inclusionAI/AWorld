#!/usr/bin/env python
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ValidationStep:
    id: str
    command: list[str]
    cwd: str
    env: dict[str, str]


@dataclass(frozen=True)
class ValidationStepResult:
    id: str
    ok: bool
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def pythonpath_for_repo(root: Path) -> str:
    return f"{root / 'aworld-cli' / 'src'}:{root}"


def build_phase1_validation_steps(root: Path | None = None) -> list[ValidationStep]:
    resolved_root = (root or repo_root()).resolve()
    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath_for_repo(resolved_root)
    env.setdefault("AWORLD_ACP_SELF_TEST_BRIDGE", "1")

    aworld_main = ["python", "-m", "aworld_cli.main", "--no-banner", "acp"]

    return [
        ValidationStep(
            id="pytest_acp",
            command=["python", "-m", "pytest", "tests/acp", "-q"],
            cwd=str(resolved_root),
            env=env,
        ),
        ValidationStep(
            id="acp_self_test",
            command=[*aworld_main, "self-test"],
            cwd=str(resolved_root),
            env=env,
        ),
        ValidationStep(
            id="validate_stdio_host",
            command=[
                *aworld_main,
                "validate-stdio-host",
                "--command",
                "python -m aworld_cli.main --no-banner acp",
            ],
            cwd=str(resolved_root),
            env=env,
        ),
    ]


def run_step(step: ValidationStep) -> ValidationStepResult:
    proc = subprocess.run(
        step.command,
        cwd=step.cwd,
        env=step.env,
        capture_output=True,
        text=True,
        check=False,
    )
    return ValidationStepResult(
        id=step.id,
        ok=proc.returncode == 0,
        command=step.command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def build_summary_payload(results: list[ValidationStepResult]) -> dict[str, object]:
    passed = sum(1 for result in results if result.ok)
    failed = sum(1 for result in results if not result.ok)
    return {
        "ok": failed == 0,
        "summary": {
            "passed": passed,
            "failed": failed,
            "total": len(results),
        },
        "steps": [asdict(result) for result in results],
    }


def main() -> int:
    results: list[ValidationStepResult] = []
    for step in build_phase1_validation_steps():
        result = run_step(step)
        results.append(result)
        if not result.ok:
            break

    payload = build_summary_payload(results)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
