"""Host-local macOS UI automation MCP server."""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from mcp.server import FastMCP
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from pydantic import Field

if __package__ in (None, ""):
    repo_root = Path(__file__).resolve().parents[7]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.backends.peekaboo_cli import (
        execute_peekaboo_action,
    )
    from aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.errors import (
        MacUIError,
        error_payload,
    )
    from aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.preflight import (
        detect_backend_availability,
        gate_enabled,
        is_macos_host,
    )
else:
    from .backends.peekaboo_cli import execute_peekaboo_action
    from .errors import MacUIError, error_payload
    from .preflight import detect_backend_availability, gate_enabled, is_macos_host

INTERACTION_ACTIONS = {"click", "type", "press", "scroll"}
OPTIONAL_SCOPE_FIELDS = {"app", "window_id", "window_title"}
OPTIONAL_TIMEOUT_FIELD = "timeout_seconds"
REQUIRED_PERMISSION_MARKERS = ("accessibility", "screen recording")
MAX_TIMEOUT_SECONDS = 300.0
PERMISSION_CACHE_TTL_SECONDS = 60.0
_permission_preflight_cache: dict[str, Any] = {"checked_at": 0.0, "missing": None}

_log_level = os.environ.get("MCP_LOG_LEVEL") or os.environ.get("LOG_LEVEL") or os.environ.get("LOGLEVEL") or "WARNING"
mcp = FastMCP(
    "mac-ui-automation-server",
    log_level=_log_level,
    port=8085,
    instructions="Host-local macOS UI automation via Peekaboo CLI",
)


def resolve_click_target(params: dict[str, object]) -> dict[str, object]:
    if params.get("target_id"):
        return {"target_id": params["target_id"]}
    if params.get("x") is not None and params.get("y") is not None:
        return {"x": params["x"], "y": params["y"]}
    raise ValueError("click requires target_id or x/y coordinates")


def validate_action_params(action: str, params: dict[str, object]) -> None:
    _validate_timeout(params)
    _validate_coordinate_pair(params)

    if action in {"permissions", "list_apps"}:
        return
    if action == "launch_app":
        _require_fields(params, "app")
        return
    if action == "list_windows":
        _require_fields(params, "app")
        return
    if action == "focus_window":
        if not params.get("window_id") and not params.get("window_title"):
            raise ValueError("focus_window requires window_id or window_title")
        if params.get("window_title") and not params.get("app") and not params.get("window_id"):
            raise ValueError("focus_window with window_title requires app scope")
        return
    if action == "see":
        return
    if action == "click":
        resolve_click_target(params)
        return
    if action == "type":
        _require_fields(params, "text")
        return
    if action == "press":
        _require_fields(params, "keys")
        return
    if action == "scroll":
        _require_fields(params, "direction", "amount")
        return
    raise ValueError(f"Unsupported action: {action}")


def normalize_see_result(result: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {"targets": []}
    for key in ("app", "window_id", "window_title", "observation_id", "visible_text", "artifact_reference"):
        if result.get(key) is not None:
            normalized[key] = result[key]

    for target in result.get("targets", []):
        target_id = target.get("target_id")
        if not target_id:
            continue
        normalized_target = {"target_id": target_id}
        for key in ("role", "text", "x", "y", "width", "height"):
            if target.get(key) is not None:
                normalized_target[key] = target[key]
        normalized["targets"].append(normalized_target)

    return normalized


def run_action(action: str, params: dict[str, object]) -> dict[str, Any]:
    validate_action_params(action, params)
    _run_preflight(action)
    try:
        result = execute_peekaboo_action(action, params)
    except MacUIError:
        raise
    except ValueError as exc:
        raise MacUIError(
            code="INVALID_ARGUMENT",
            message=str(exc),
            details={"action": action},
        ) from exc

    if action == "see":
        return normalize_see_result(result)
    return result


def _run_preflight(action: str) -> None:
    if not gate_enabled():
        raise MacUIError(
            code="CAPABILITY_DISABLED",
            message="Set AWORLD_ENABLE_MAC_UI_AUTOMATION=1 to enable macOS UI automation",
        )
    if not is_macos_host():
        raise MacUIError(
            code="UNSUPPORTED_PLATFORM",
            message="macOS UI automation is only supported on Darwin hosts",
        )
    if not detect_backend_availability():
        raise MacUIError(
            code="BACKEND_NOT_AVAILABLE",
            message="Peekaboo CLI is not installed or not available on PATH",
        )
    if action == "permissions":
        return

    missing = _get_missing_required_permissions()
    if missing:
        raise MacUIError(
            code="PERMISSION_MISSING",
            message="Required macOS permissions are missing for UI automation",
            details={"missing_permissions": missing},
        )


async def _tool_response(action: str, params: dict[str, object]) -> TextContent:
    try:
        data = await asyncio.to_thread(run_action, action, params)
    except MacUIError as exc:
        raise ValueError(json.dumps(error_payload(exc), ensure_ascii=False)) from exc
    return TextContent(
        type="text",
        text=json.dumps({"ok": True, "action": action, "data": data}, ensure_ascii=False),
    )


def _require_fields(params: dict[str, object], *field_names: str) -> None:
    missing = [name for name in field_names if params.get(name) in (None, "")]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")


def _validate_timeout(params: dict[str, object]) -> None:
    timeout = params.get(OPTIONAL_TIMEOUT_FIELD)
    if timeout is None:
        return
    timeout_value = float(timeout)
    if timeout_value <= 0:
        raise ValueError("timeout_seconds must be greater than 0")
    if timeout_value > MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be less than or equal to {int(MAX_TIMEOUT_SECONDS)}")


def _validate_coordinate_pair(params: dict[str, object]) -> None:
    has_x = params.get("x") is not None
    has_y = params.get("y") is not None
    if has_x != has_y:
        raise ValueError("x and y must be provided together")


def _collect_missing_required_permissions(permissions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for permission in permissions:
        name = str(permission.get("name", "")).lower()
        status = str(permission.get("status", "")).lower()
        is_granted = permission.get("isGranted")
        if isinstance(is_granted, bool):
            granted = is_granted
        elif status:
            granted = status == "granted"
        else:
            granted = False

        if granted:
            continue
        if any(marker in name for marker in REQUIRED_PERMISSION_MARKERS):
            missing.append(permission)
    return missing


def _get_missing_required_permissions() -> list[dict[str, Any]]:
    now = time.monotonic()
    checked_at = float(_permission_preflight_cache.get("checked_at", 0.0) or 0.0)
    cached_missing = _permission_preflight_cache.get("missing")
    if checked_at and cached_missing is not None and now - checked_at < PERMISSION_CACHE_TTL_SECONDS:
        return list(cached_missing)

    permissions_result = execute_peekaboo_action("permissions", {})
    missing = _collect_missing_required_permissions(permissions_result.get("permissions", []))
    _permission_preflight_cache["checked_at"] = now
    _permission_preflight_cache["missing"] = list(missing)
    return missing


@mcp.tool(description="Inspect current macOS UI automation permission status.")
async def permissions(ctx: Context) -> TextContent:
    return await _tool_response("permissions", {})


@mcp.tool(description="List currently running GUI apps visible to the backend.")
async def list_apps(
    ctx: Context,
    running_only: bool = Field(default=True, description="Reserved for future use; running apps are always returned in phase 1."),
) -> TextContent:
    return await _tool_response("list_apps", {"running_only": running_only})


@mcp.tool(description="Launch a macOS application by app name.")
async def launch_app(
    ctx: Context,
    app: str = Field(description="Application name, bundle ID, or path supported by Peekaboo launch."),
) -> TextContent:
    return await _tool_response("launch_app", {"app": app})


@mcp.tool(description="List windows for a macOS application or the backend default scope.")
async def list_windows(
    ctx: Context,
    app: Optional[str] = Field(default=None, description="Optional app name to scope the window list."),
) -> TextContent:
    return await _tool_response("list_windows", {"app": app})


@mcp.tool(description="Focus a specific macOS window.")
async def focus_window(
    ctx: Context,
    app: Optional[str] = Field(default=None, description="Optional app name for window resolution."),
    window_id: Optional[str] = Field(default=None, description="Window identifier."),
    window_title: Optional[str] = Field(default=None, description="Window title."),
) -> TextContent:
    return await _tool_response(
        "focus_window",
        {"app": app, "window_id": window_id, "window_title": window_title},
    )


@mcp.tool(description="Observe the UI of a macOS app/window and return stable interaction targets.")
async def see(
    ctx: Context,
    app: Optional[str] = Field(default=None, description="Optional app name to scope observation."),
    window_id: Optional[str] = Field(default=None, description="Optional window identifier."),
    window_title: Optional[str] = Field(default=None, description="Optional window title."),
    include_artifact: bool = Field(default=False, description="Whether to request an annotated artifact."),
    timeout_seconds: Optional[float] = Field(default=None, description="Optional backend timeout override."),
) -> TextContent:
    return await _tool_response(
        "see",
        {
            "app": app,
            "window_id": window_id,
            "window_title": window_title,
            "include_artifact": include_artifact,
            "timeout_seconds": timeout_seconds,
        },
    )


@mcp.tool(description="Click a stable target ID or raw coordinates.")
async def click(
    ctx: Context,
    target_id: Optional[str] = Field(default=None, description="Stable target ID from see."),
    x: Optional[float] = Field(default=None, description="X coordinate fallback."),
    y: Optional[float] = Field(default=None, description="Y coordinate fallback."),
    app: Optional[str] = Field(default=None, description="Optional app name."),
    window_id: Optional[str] = Field(default=None, description="Optional window identifier."),
    window_title: Optional[str] = Field(default=None, description="Optional window title."),
    timeout_seconds: Optional[float] = Field(default=None, description="Optional wait timeout override."),
) -> TextContent:
    return await _tool_response(
        "click",
        {
            "target_id": target_id,
            "x": x,
            "y": y,
            "app": app,
            "window_id": window_id,
            "window_title": window_title,
            "timeout_seconds": timeout_seconds,
        },
    )


@mcp.tool(description="Type text into the currently focused element or scoped app/window.")
async def type(
    ctx: Context,
    text: str = Field(description="Text to type."),
    app: Optional[str] = Field(default=None, description="Optional app name."),
    window_id: Optional[str] = Field(default=None, description="Optional window identifier."),
    window_title: Optional[str] = Field(default=None, description="Optional window title."),
    timeout_seconds: Optional[float] = Field(default=None, description="Optional timeout override."),
) -> TextContent:
    return await _tool_response(
        "type",
        {
            "text": text,
            "app": app,
            "window_id": window_id,
            "window_title": window_title,
            "timeout_seconds": timeout_seconds,
        },
    )


@mcp.tool(description="Press one or more special keys.")
async def press(
    ctx: Context,
    keys: str = Field(description="Comma- or space-separated special keys."),
    count: int = Field(default=1, description="Repeat count for the key sequence."),
    app: Optional[str] = Field(default=None, description="Optional app name."),
    window_id: Optional[str] = Field(default=None, description="Optional window identifier."),
    window_title: Optional[str] = Field(default=None, description="Optional window title."),
    timeout_seconds: Optional[float] = Field(default=None, description="Optional timeout override."),
) -> TextContent:
    return await _tool_response(
        "press",
        {
            "keys": keys,
            "count": count,
            "app": app,
            "window_id": window_id,
            "window_title": window_title,
            "timeout_seconds": timeout_seconds,
        },
    )


@mcp.tool(description="Scroll in a direction by a fixed amount.")
async def scroll(
    ctx: Context,
    direction: str = Field(description="Scroll direction: up, down, left, or right."),
    amount: int = Field(description="Scroll amount in ticks."),
    target_id: Optional[str] = Field(default=None, description="Optional target anchor from see."),
    x: Optional[float] = Field(default=None, description="Optional X coordinate anchor."),
    y: Optional[float] = Field(default=None, description="Optional Y coordinate anchor."),
    app: Optional[str] = Field(default=None, description="Optional app name."),
    window_id: Optional[str] = Field(default=None, description="Optional window identifier."),
    window_title: Optional[str] = Field(default=None, description="Optional window title."),
    timeout_seconds: Optional[float] = Field(default=None, description="Optional timeout override."),
) -> TextContent:
    return await _tool_response(
        "scroll",
        {
            "direction": direction,
            "amount": amount,
            "target_id": target_id,
            "x": x,
            "y": y,
            "app": app,
            "window_id": window_id,
            "window_title": window_title,
            "timeout_seconds": timeout_seconds,
        },
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    use_stdio = "--stdio" in sys.argv or os.environ.get("MCP_TRANSPORT", "").strip().lower() == "stdio"
    try:
        if use_stdio:
            mcp.run(transport="stdio")
        else:
            mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        logging.info("macOS UI automation MCP server stopped")
