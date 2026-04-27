import os
import json
import subprocess
import tempfile
import uuid
from typing import Any

from ..errors import MacUIError

DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_TIMEOUT_SECONDS = 300.0


def execute_peekaboo_action(action: str, params: dict[str, object]) -> dict[str, Any]:
    prepared = dict(params)
    commands = _build_peekaboo_commands(action, prepared)
    timeout_seconds = _resolve_timeout_seconds(prepared)
    last_result: dict[str, Any] = {}
    artifact_path = prepared.get("_artifact_path")

    try:
        for command in commands:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            if process.returncode != 0:
                _cleanup_artifact_file(artifact_path)
                raise normalize_backend_failure(action, process.returncode, process.stderr)

            last_result = parse_peekaboo_output(action, process.stdout)
    except FileNotFoundError as exc:
        _cleanup_artifact_file(artifact_path)
        raise MacUIError(
            code="BACKEND_NOT_AVAILABLE",
            message="Peekaboo CLI is not available on PATH",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        _cleanup_artifact_file(artifact_path)
        raise MacUIError(
            code="ACTION_TIMEOUT",
            message=f"Peekaboo action '{action}' timed out",
            details={"action": action, "timeout_seconds": timeout_seconds},
        ) from exc
    except Exception:
        _cleanup_artifact_file(artifact_path)
        raise

    if isinstance(artifact_path, str) and artifact_path and "artifact_reference" not in last_result:
        last_result["artifact_reference"] = artifact_path
    return last_result


def build_peekaboo_command(action: str, params: dict[str, object]) -> list[str]:
    prepared = dict(params)
    return _build_peekaboo_commands(action, prepared)[-1]


def parse_peekaboo_output(action: str, stdout: str) -> dict[str, Any]:
    if not stdout.strip():
        return {}

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise MacUIError(
            code="BACKEND_EXECUTION_FAILED",
            message=f"Failed to parse peekaboo output for action '{action}'",
            details={"action": action, "stdout": stdout},
        ) from exc

    data = _unwrap_data(payload)

    if action == "see":
        return _normalize_see_output(data)
    if action == "permissions":
        permissions = data if isinstance(data, list) else data.get("permissions", data.get("items", []))
        return {"permissions": permissions}
    if action == "list_apps":
        apps = data if isinstance(data, list) else data.get("apps", data.get("items", []))
        return {"apps": apps}
    if action == "list_windows":
        windows = data if isinstance(data, list) else data.get("windows", data.get("items", []))
        return {"windows": windows}
    return data if isinstance(data, dict) else {"data": data}


def normalize_backend_failure(action: str, returncode: int, stderr: str) -> MacUIError:
    lower_stderr = (stderr or "").lower()
    code = "BACKEND_EXECUTION_FAILED"
    if "permission" in lower_stderr or "screen recording" in lower_stderr or "accessibility" in lower_stderr:
        code = "PERMISSION_MISSING"
    elif "app not found" in lower_stderr or "could not find app" in lower_stderr:
        code = "APP_NOT_FOUND"
    elif "window not found" in lower_stderr:
        code = "WINDOW_NOT_FOUND"
    elif (
        "target not found" in lower_stderr
        or "element not found" in lower_stderr
        or "snapshot_not_found" in lower_stderr
    ):
        code = "TARGET_NOT_FOUND"
    elif "timeout" in lower_stderr or "timed out" in lower_stderr:
        code = "ACTION_TIMEOUT"
    elif "invalid" in lower_stderr or "missing required" in lower_stderr or "unknown option" in lower_stderr:
        code = "INVALID_ARGUMENT"

    return MacUIError(
        code=code,
        message=f"Peekaboo backend failed for action '{action}'",
        details={
            "action": action,
            "returncode": returncode,
            "stderr": stderr,
        },
    )


def _build_peekaboo_commands(action: str, params: dict[str, object]) -> list[list[str]]:
    if action == "permissions":
        return [["peekaboo", "permissions", "--json"]]
    if action == "list_apps":
        return [["peekaboo", "list", "apps", "--json"]]
    if action == "launch_app":
        return [["peekaboo", "app", "launch", "--wait-until-ready", "--json", str(params["app"])]]
    if action == "list_windows":
        command = ["peekaboo", "list", "windows", "--json"]
        _append_scope_flags(command, params)
        return [command]
    if action == "focus_window":
        command = ["peekaboo", "window", "focus", "--verify", "--json"]
        _append_scope_flags(command, params)
        return [command]
    if action == "see":
        command = ["peekaboo", "see", "--json"]
        _append_scope_flags(command, params)
        timeout_seconds = params.get("timeout_seconds")
        if timeout_seconds is not None:
            command.extend(["--timeout-seconds", str(timeout_seconds)])
        if params.get("include_artifact"):
            artifact_path = _build_artifact_path()
            params["_artifact_path"] = artifact_path
            command.extend(["--annotate", "--path", artifact_path])
        return [command]
    if action == "click":
        command = ["peekaboo", "click", "--json"]
        if params.get("target_id"):
            command.extend(["--on", str(params["target_id"])])
        elif params.get("x") is not None and params.get("y") is not None:
            command.extend(["--coords", f"{params['x']},{params['y']}"])
        _append_scope_flags(command, params)
        timeout_seconds = params.get("timeout_seconds")
        if timeout_seconds is not None:
            command.extend(["--wait-for", str(int(float(timeout_seconds) * 1000))])
        return [command]
    if action == "type":
        command = ["peekaboo", "type", str(params["text"]), "--json"]
        _append_scope_flags(command, params)
        return [command]
    if action == "press":
        keys = _normalize_keys(params.get("keys"))
        command = ["peekaboo", "press", *keys, "--json"]
        if params.get("count") is not None:
            command.extend(["--count", str(params["count"])])
        _append_scope_flags(command, params)
        return [command]
    if action == "scroll":
        commands: list[list[str]] = []
        if params.get("x") is not None and params.get("y") is not None and not params.get("target_id"):
            commands.append(["peekaboo", "move", "--coords", f"{params['x']},{params['y']}", "--json"])

        command = [
            "peekaboo",
            "scroll",
            "--direction",
            str(params["direction"]),
            "--amount",
            str(params["amount"]),
            "--json",
        ]
        if params.get("target_id"):
            command.extend(["--on", str(params["target_id"])])
        _append_scope_flags(command, params)
        commands.append(command)
        return commands
    raise ValueError(f"Unsupported action: {action}")


def _append_scope_flags(command: list[str], params: dict[str, object]) -> None:
    if params.get("app"):
        command.extend(["--app", str(params["app"])])
    if params.get("window_id"):
        command.extend(["--window-id", str(params["window_id"])])
    if params.get("window_title"):
        command.extend(["--window-title", str(params["window_title"])])


def _normalize_see_output(data: Any) -> dict[str, Any]:
    payload = data if isinstance(data, dict) else {}
    result: dict[str, Any] = {"targets": []}
    targets = payload.get("targets") or payload.get("ui_elements") or []
    for target in targets:
        target_id = target.get("target_id") or target.get("id")
        if not target_id:
            continue

        normalized: dict[str, Any] = {"target_id": target_id}
        role = target.get("role") or target.get("role_description")
        text = target.get("text") or target.get("label") or target.get("title") or target.get("description")
        if role:
            normalized["role"] = role
        if text:
            normalized["text"] = text

        bounds = target.get("bounds") or {}
        for key in ("x", "y", "width", "height"):
            value = target.get(key)
            if value is None and isinstance(bounds, dict):
                value = bounds.get(key)
            if value is not None:
                normalized[key] = value
        result["targets"].append(normalized)

    if payload.get("text") is not None:
        result["visible_text"] = payload["text"]
    if payload.get("snapshot_id") is not None:
        result["observation_id"] = payload["snapshot_id"]
    elif payload.get("observation_id") is not None:
        result["observation_id"] = payload["observation_id"]
    if payload.get("ui_map") is not None:
        result["artifact_reference"] = payload["ui_map"]
    elif payload.get("path") is not None:
        result["artifact_reference"] = payload["path"]
    return result


def _unwrap_data(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _normalize_keys(keys: object) -> list[str]:
    if isinstance(keys, str):
        return [token for token in keys.replace(",", " ").split() if token]
    if isinstance(keys, (list, tuple)):
        return [str(token) for token in keys if str(token).strip()]
    raise ValueError("keys must be a string or sequence of strings")


def _build_artifact_path() -> str:
    return tempfile.gettempdir() + f"/aworld-mac-ui-{uuid.uuid4().hex}.png"


def _resolve_timeout_seconds(params: dict[str, object]) -> float:
    timeout = params.get("timeout_seconds")
    if timeout is None:
        return DEFAULT_TIMEOUT_SECONDS
    timeout_value = float(timeout)
    if timeout_value <= 0:
        raise ValueError("timeout_seconds must be greater than 0")
    if timeout_value > MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be less than or equal to {int(MAX_TIMEOUT_SECONDS)}")
    return timeout_value


def _cleanup_artifact_file(path: object) -> None:
    if not isinstance(path, str) or not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
