import os
from pathlib import Path
from typing import Iterable, Any

from aworld.logs.util import logger

from ..builtin_plugins.memory_cli.common import append_workspace_session_log


def log_queued_steering_event(
    *,
    workspace_path: str | os.PathLike[str] | None,
    session_id: str | None,
    task_id: str | None,
    steering_item: Any,
    pending_count: int | None = None,
) -> None:
    if not session_id or steering_item is None:
        return

    payload = {
        "event": "steering_queued",
        "source": "interactive_steering",
        "session_id": session_id,
        "task_id": task_id,
        "pending_count": int(pending_count or 0),
        "steering": _serialize_steering_item(steering_item),
    }
    _append_steering_event(workspace_path, session_id, payload)


def log_applied_steering_event(
    *,
    workspace_path: str | os.PathLike[str] | None,
    session_id: str | None,
    task_id: str | None,
    steering_items: Iterable[Any],
    checkpoint: str,
) -> None:
    items = [_serialize_steering_item(item) for item in steering_items if item is not None]
    if not session_id or not items:
        return

    payload = {
        "event": "steering_applied_at_checkpoint",
        "source": "interactive_steering",
        "session_id": session_id,
        "task_id": task_id,
        "checkpoint": checkpoint,
        "applied_count": len(items),
        "steering": items,
    }
    _append_steering_event(workspace_path, session_id, payload)


def _append_steering_event(
    workspace_path: str | os.PathLike[str] | None,
    session_id: str,
    payload: dict[str, Any],
) -> None:
    resolved_workspace = Path(workspace_path or os.getcwd()).expanduser().resolve()
    try:
        append_workspace_session_log(
            workspace_path=resolved_workspace,
            session_id=session_id,
            payload={key: value for key, value in payload.items() if value is not None},
        )
    except Exception as exc:
        logger.warning(f"Failed to append steering event to workspace session log: {exc}")


def _serialize_steering_item(item: Any) -> dict[str, Any]:
    return {
        "sequence": getattr(item, "sequence", None),
        "text": getattr(item, "text", None),
        "created_at": getattr(item, "created_at", None),
    }
