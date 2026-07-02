from __future__ import annotations

from pathlib import Path
from typing import Any

from .coordinator import SteeringCoordinator


STEERING_CAPTURED_ACK = "Steering captured. Applying at next checkpoint."


class SessionSteeringRuntime:
    """Minimal runtime surface for session-scoped steering outside interactive CLI."""

    def __init__(
        self,
        *,
        workspace_path: str | None = None,
        base_runtime: Any | None = None,
        steering: SteeringCoordinator | None = None,
    ) -> None:
        self.workspace_path = str(
            Path(workspace_path or Path.cwd()).expanduser().resolve()
        )
        self._base_runtime = base_runtime
        inherited = getattr(base_runtime, "_steering", None) if base_runtime is not None else None
        self._steering = steering or inherited or SteeringCoordinator()

    def steering_snapshot(self, session_id: str | None) -> dict[str, Any]:
        return self._steering.snapshot(session_id) if session_id else {}

    def request_session_interrupt(self, session_id: str | None) -> bool:
        return bool(session_id) and self._steering.request_interrupt(session_id)

    def update_hud_snapshot(self, **sections: dict[str, Any]) -> dict[str, dict[str, Any]]:
        delegate = getattr(self._base_runtime, "update_hud_snapshot", None)
        if callable(delegate):
            return delegate(**sections)
        return dict(sections)

    def settle_hud_snapshot(self, task_status: str = "idle") -> dict[str, dict[str, Any]]:
        delegate = getattr(self._base_runtime, "settle_hud_snapshot", None)
        if callable(delegate):
            return delegate(task_status=task_status)
        return {"task": {"status": task_status}}

    def get_hud_snapshot(self) -> dict[str, dict[str, Any]]:
        delegate = getattr(self._base_runtime, "get_hud_snapshot", None)
        if callable(delegate):
            return delegate()
        return {}

    def active_plugin_capabilities(self) -> tuple[str, ...]:
        delegate = getattr(self._base_runtime, "active_plugin_capabilities", None)
        if callable(delegate):
            return tuple(delegate())
        return tuple()

    async def run_plugin_hooks(
        self,
        hook_point: str,
        event: dict[str, Any],
        executor_instance: Any = None,
    ) -> list[tuple[Any, Any]]:
        delegate = getattr(self._base_runtime, "run_plugin_hooks", None)
        if callable(delegate):
            return await delegate(
                hook_point,
                event=event,
                executor_instance=executor_instance,
            )
        return []

    def __getattr__(self, name: str) -> Any:
        if self._base_runtime is None:
            raise AttributeError(name)
        return getattr(self._base_runtime, name)
