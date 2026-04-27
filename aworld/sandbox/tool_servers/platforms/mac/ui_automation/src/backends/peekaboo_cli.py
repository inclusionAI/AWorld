import json
from typing import Any

from ..errors import MacUIError


def build_peekaboo_command(action: str, params: dict[str, object]) -> list[str]:
    if action == "launch_app":
        return ["peekaboo", "app", "launch", str(params["app"])]
    raise ValueError(f"Unsupported action: {action}")


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

    if action != "see":
        return payload

    result: dict[str, Any] = {"targets": []}
    for target in payload.get("targets", []):
        normalized = {"target_id": target.get("id") or target.get("target_id")}
        for key in ("role", "text", "x", "y", "width", "height"):
            if key in target and target[key] is not None:
                normalized[key] = target[key]
        result["targets"].append(normalized)

    if "text" in payload:
        result["visible_text"] = payload["text"]
    if "observation_id" in payload:
        result["observation_id"] = payload["observation_id"]
    if "artifact_reference" in payload:
        result["artifact_reference"] = payload["artifact_reference"]
    return result


def normalize_backend_failure(action: str, returncode: int, stderr: str) -> MacUIError:
    return MacUIError(
        code="BACKEND_EXECUTION_FAILED",
        message=f"Peekaboo backend failed for action '{action}'",
        details={
            "action": action,
            "returncode": returncode,
            "stderr": stderr,
        },
    )
