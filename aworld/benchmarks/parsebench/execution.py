"""Local ParseBench task execution and ATIF evidence emission.

This module is intentionally ground-truth blind.  It consumes the public task
contract, delegates parsing to the FileX adapter, and commits a minimal ATIF
trajectory only after the adapter has atomically published valid artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aworld.benchmarks.parsebench.adapter import (
    DEFAULT_ARTIFACTS_ROOT,
    DEFAULT_PROVIDER,
    DEFAULT_TASK_SPEC_PATH,
    DEFAULT_VLM_MODEL_PROFILE,
    FileXAdapterError,
    FileXExecutionOptions,
    FileXRunner,
    SubprocessFileXRunner,
    execute_filex_parsebench,
    load_parsebench_task_spec,
)


ATIF_SCHEMA_VERSION = "ATIF-v1.7"
DEFAULT_PARSEBENCH_MODEL_PROFILE = DEFAULT_VLM_MODEL_PROFILE
DEFAULT_PARSEBENCH_WORKSPACE_ROOT = Path("/workspace")
DEFAULT_PARSEBENCH_TRAJECTORY_PATH = Path("/logs/agent/trajectory.json")

_PROTECTED_MODEL_ENVIRONMENT = (
    "LLM_BASE_URL",
    "LLM_MODEL_NAME",
    "LLM_API_KEY",
)


class ParseBenchExecutionError(RuntimeError):
    """Stable, secret-free failure raised by the local task executor."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ParseBenchRunOutcome:
    """Paths and non-secret identity for one completed FileX execution."""

    task_id: str
    result: Mapping[str, Any]
    artifacts_root: Path
    trajectory_path: Path


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ParseBenchExecutionError(
            "unsafe_output_path", "ParseBench output parent is not a regular directory"
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _require_protected_model_environment(
    environment: Mapping[str, str],
) -> None:
    missing = [
        name
        for name in _PROTECTED_MODEL_ENVIRONMENT
        if not str(environment.get(name) or "").strip()
    ]
    if missing:
        raise ParseBenchExecutionError(
            "protected_model_config_missing",
            "protected model configuration is missing: " + ", ".join(missing),
        )


def _invalidate_trajectory(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ParseBenchExecutionError(
            "execution_evidence_failed",
            "stale ParseBench trajectory could not be removed",
        ) from exc


def _trajectory(
    *,
    task_id: str,
    dataset_revision: str,
    scorer_revision: str,
    model_profile: str,
    result_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": ATIF_SCHEMA_VERSION,
        "trajectory_id": f"aworld-filex-parsebench-{task_id}",
        "agent": {
            "name": "aworld-filex-parsebench",
            "version": "1.0",
            "model_name": model_profile,
        },
        "steps": [
            {
                "step_id": 1,
                "source": "agent",
                "message": "FileX ParseBench artifacts emitted.",
                "llm_call_count": 0,
            }
        ],
        "final_metrics": {"total_steps": 1},
        "extra": {
            "task_id": task_id,
            "dataset_revision": dataset_revision,
            "scorer_revision": scorer_revision,
            "result_sha256": result_sha256,
        },
    }


def run_parsebench_task(
    *,
    task_spec_path: Path = DEFAULT_TASK_SPEC_PATH,
    workspace_root: Path = DEFAULT_PARSEBENCH_WORKSPACE_ROOT,
    artifacts_root: Path = DEFAULT_ARTIFACTS_ROOT,
    trajectory_path: Path = DEFAULT_PARSEBENCH_TRAJECTORY_PATH,
    model_profile: str = DEFAULT_PARSEBENCH_MODEL_PROFILE,
    provider: str = DEFAULT_PROVIDER,
    timeout_seconds: float = 600.0,
    filex_executable: str = "filex",
    environment: Mapping[str, str] | None = None,
    runner: FileXRunner | None = None,
) -> ParseBenchRunOutcome:
    """Execute one public ParseBench task and atomically emit ATIF v1.7."""

    trajectory_path = Path(trajectory_path)
    _invalidate_trajectory(trajectory_path)
    selected_environment = os.environ if environment is None else environment
    _require_protected_model_environment(selected_environment)
    try:
        spec = load_parsebench_task_spec(task_spec_path)
        options = FileXExecutionOptions(
            workspace_root=workspace_root,
            artifacts_root=artifacts_root,
            provider=provider,
            timeout_seconds=timeout_seconds,
            no_cache=True,
            vlm_model_profile=model_profile,
        )
        selected_runner = runner or SubprocessFileXRunner(
            executable=filex_executable,
            environment=selected_environment,
        )
        result = execute_filex_parsebench(
            spec,
            options=options,
            runner=selected_runner,
        )
        result_path = options.artifacts_root / "result.json"
        result_sha256 = "sha256:" + hashlib.sha256(result_path.read_bytes()).hexdigest()
        trajectory = _trajectory(
            task_id=spec.task_id,
            dataset_revision=spec.dataset_revision,
            scorer_revision=spec.scorer_revision,
            model_profile=model_profile,
            result_sha256=result_sha256,
        )
        _atomic_write(trajectory_path, _canonical_json_bytes(trajectory))
    except ParseBenchExecutionError:
        raise
    except FileXAdapterError as exc:
        raise ParseBenchExecutionError(exc.code, str(exc)) from None
    except (OSError, TypeError, ValueError) as exc:
        raise ParseBenchExecutionError(
            "execution_evidence_failed",
            "ParseBench execution evidence could not be committed",
        ) from exc
    return ParseBenchRunOutcome(
        task_id=spec.task_id,
        result=result,
        artifacts_root=options.artifacts_root,
        trajectory_path=trajectory_path,
    )


__all__ = (
    "ATIF_SCHEMA_VERSION",
    "DEFAULT_PARSEBENCH_MODEL_PROFILE",
    "DEFAULT_PARSEBENCH_TRAJECTORY_PATH",
    "DEFAULT_PARSEBENCH_WORKSPACE_ROOT",
    "ParseBenchExecutionError",
    "ParseBenchRunOutcome",
    "run_parsebench_task",
)
