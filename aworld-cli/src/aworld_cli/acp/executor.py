from __future__ import annotations

from pathlib import Path
from typing import Any

from aworld_cli.executors.local import LocalAgentExecutor, WorkSpace
from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel


class AcpLocalExecutor(LocalAgentExecutor):
    """ACP-local executor that keeps workspace semantics explicit per session."""

    def __init__(self, *, working_directory: str, **kwargs: Any) -> None:
        self._working_directory = str(Path(working_directory).expanduser().resolve())
        super().__init__(**kwargs)

    def _workspace_base_dir(self) -> Path:
        return Path(self._working_directory) / ".aworld" / "workspaces"

    def _get_session_history_file(self) -> Path:
        workspace_base = self._workspace_base_dir()
        workspace_base.mkdir(parents=True, exist_ok=True)
        return workspace_base / ".session_history.json"

    def _ensure_context_working_dir_config(self) -> None:
        if not self.context_config:
            self.context_config = AmniConfigFactory.create(
                AmniConfigLevel.NAVIGATOR,
                debug_mode=True,
            )
            self.context_config.agent_config.history_scope = "session"

        env_config = getattr(self.context_config, "env_config", None)
        if env_config is not None:
            env_config.working_dir_base_path = str(self._workspace_base_dir())

    async def _build_task(self, task_content: str, session_id: str = None, task_id: str = None, image_urls=None):
        self._ensure_context_working_dir_config()
        return await super()._build_task(
            task_content,
            session_id=session_id,
            task_id=task_id,
            image_urls=image_urls,
        )

    async def _create_workspace(self, session_id: str):
        if WorkSpace is None:
            return None

        workspace_base = self._workspace_base_dir()
        workspace_base.mkdir(parents=True, exist_ok=True)

        workspace_path = workspace_base / session_id
        return WorkSpace.from_local_storages(
            session_id=session_id,
            storage_path=str(workspace_path),
        )

    async def _execute_hooks(self, hook_point: str, **kwargs):
        context = kwargs.get("context")
        if context is not None:
            context.workspace_path = self._working_directory
        return await super()._execute_hooks(hook_point, **kwargs)
