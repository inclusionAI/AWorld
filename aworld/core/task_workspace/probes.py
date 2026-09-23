"""Task-interpreter API introspection and explicit argv probes.

These are diagnostic executions, not task correctness verdicts or security
sandboxes. A process boundary contains Python state; ordinary process groups,
wall/CPU/file/output limits constrain each operation. Imports can still affect
files/network allowed to the child, so the caller chooses its cwd and env.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Mapping, Sequence

from ._process import ProcessLimits, run_bounded


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def describe_probes() -> dict:
    return {
        "schema_version": "aworld.api-probe-request/v1",
        "python": {
            "required": ["interpreter", "module"],
            "optional": [
                "object_path",
                "working_dir",
                "env",
                "call",
                "limits",
                "doc_chars",
                "result_bytes",
            ],
            "call": {"args": [], "kwargs": {}, "result": "structure"},
            "report": [
                "interpreter",
                "module_file",
                "module_version",
                "distributions",
                "signature",
                "doc",
                "call_result",
                "json_result",
                "error_type",
                "traceback",
            ],
        },
        "argv": {"required": ["argv"], "optional": ["working_dir", "env", "limits"]},
        "limits": asdict(ProcessLimits()),
        "isolation": "Fresh subprocess with explicit task env and cwd (temporary by default); operation wall/output bounds and reported platform resource limits. Imports/calls may affect their allowed files/network; no task correctness assertion.",
    }


async def probe_python(
    interpreter: str | Path,
    module: str,
    object_path: str | None = None,
    *,
    working_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    call: dict | None = None,
    limits: ProcessLimits | None = None,
    doc_chars: int = 8192,
    result_bytes: int = 16384,
) -> dict:
    """Inspect the actual selected Python; optional calls require explicit JSON args/kwargs."""
    if not isinstance(module, str) or not module:
        raise ValueError("module must be selected explicitly")
    for name in (module, object_path):
        if name is not None and (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", name)
        ):
            raise ValueError("module/object must be dotted Python identifiers")
    if not isinstance(interpreter, (str, Path)) or not str(interpreter):
        raise ValueError("the task interpreter must be selected explicitly")
    if call is not None and (
        not isinstance(call, dict)
        or not isinstance(call.get("args"), list)
        or not isinstance(call.get("kwargs"), dict)
        or any(not isinstance(k, str) for k in call["kwargs"])
        or call.get("result", "structure") not in {"structure", "json"}
    ):
        raise ValueError("minimal calls require explicit args:list and kwargs:object")
    if (
        type(doc_chars) is not int
        or type(result_bytes) is not int
        or not 0 <= doc_chars <= 32768
        or not 1 <= result_bytes <= 131072
    ):
        raise ValueError("probe report limits are out of range")
    limits = limits or ProcessLimits()
    request = {
        "module": module,
        "object_path": object_path,
        "call": call,
        "doc_chars": doc_chars,
        "result_bytes": result_bytes,
    }
    encoded = json.dumps(request, allow_nan=False)
    if len(encoded.encode()) > 131072:
        raise ValueError("probe arguments exceed the operation request limit")
    with tempfile.TemporaryDirectory(prefix="aworld-api-probe-") as directory:
        scratch = Path(directory)
        request_file, report_file = scratch / "request.json", scratch / "report.json"
        request_file.write_text(encoded)
        process = await run_bounded(
            [
                str(interpreter),
                str(Path(__file__).with_name("_probe_child.py")),
                str(request_file),
                str(report_file),
            ],
            cwd=working_dir or scratch,
            env=env,
            limits=limits,
        )
        report = None
        if report_file.is_file() and report_file.stat().st_size <= 256 * 1024:
            try:
                report = json.loads(report_file.read_text())
            except (ValueError, OSError):
                pass
        return {
            "schema_version": "aworld.api-probe/v1",
            "kind": "python",
            "request_sha256": _digest(
                {**request, "interpreter": str(interpreter), "limits": asdict(limits)}
            ),
            "success": bool(
                process["return_code"] == 0
                and not process["timed_out"]
                and isinstance(report, dict)
                and report.get("success") is True
            ),
            "report": report,
            "process": process,
            "provenance": "actual_task_interpreter_subprocess",
            "task_correctness": "not_assessed",
        }


async def probe_argv(
    argv: Sequence[str],
    *,
    working_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    limits: ProcessLimits | None = None,
) -> dict:
    """Generic non-Python diagnostic. Exit success is not a semantic validation pass."""
    with tempfile.TemporaryDirectory(prefix="aworld-argv-probe-") as directory:
        process = await run_bounded(
            argv, cwd=working_dir or Path(directory), env=env, limits=limits
        )
    return {
        "schema_version": "aworld.api-probe/v1",
        "kind": "argv",
        "success": process["return_code"] == 0 and not process["timed_out"],
        "process": process,
        "request_sha256": _digest(
            {"argv": list(argv), "limits": asdict(limits or ProcessLimits())}
        ),
        "task_correctness": "not_assessed",
    }


__all__ = ["ProcessLimits", "probe_python", "probe_argv", "describe_probes"]
