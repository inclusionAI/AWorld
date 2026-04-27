from __future__ import annotations

from typing import Any, Awaitable, Callable

from aworld.logs.util import logger


class AcpCronBridge:
    """ACP-local bridge from cron terminal notifications to session updates."""

    def __init__(
        self,
        *,
        write_session_update: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._write_session_update = write_session_update
        self._session_ids: set[str] = set()
        self._job_bindings: dict[str, str] = {}

    def register_session(self, session_id: str) -> None:
        if session_id:
            self._session_ids.add(session_id)

    def unregister_session(self, session_id: str) -> None:
        self._session_ids.discard(session_id)
        bound_job_ids = [job_id for job_id, bound_session_id in self._job_bindings.items() if bound_session_id == session_id]
        for job_id in bound_job_ids:
            self._job_bindings.pop(job_id, None)

    def bind_from_tool_result(
        self,
        *,
        session_id: str,
        tool_name: str | None,
        payload: Any,
    ) -> None:
        if not session_id or session_id not in self._session_ids:
            return
        if not self._is_cron_tool(tool_name):
            return
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return

        for job_id in self._extract_job_ids(payload):
            self._job_bindings[job_id] = session_id

    async def publish_notification(self, notification_data: dict[str, Any]) -> None:
        job_id = str(notification_data.get("job_id") or "").strip()
        if not job_id:
            return

        session_id = self._job_bindings.get(job_id)
        if not session_id or session_id not in self._session_ids:
            if notification_data.get("next_run_at") is None:
                self._job_bindings.pop(job_id, None)
            return

        if notification_data.get("user_visible") is False:
            if notification_data.get("next_run_at") is None:
                self._job_bindings.pop(job_id, None)
            return

        text = self._render_notification_text(notification_data)
        if not text:
            if notification_data.get("next_run_at") is None:
                self._job_bindings.pop(job_id, None)
            return

        update = {
            "sessionUpdate": "agent_message_chunk",
            "content": {
                "text": text,
                "cron": {
                    "jobId": job_id,
                    "jobName": notification_data.get("job_name"),
                    "status": notification_data.get("status"),
                    "createdAt": notification_data.get("created_at"),
                    "nextRunAt": notification_data.get("next_run_at"),
                    "source": "cron_notification",
                },
            },
        }

        try:
            await self._write_session_update(session_id, update)
        except Exception as exc:
            logger.warning(f"Failed to push ACP cron notification for job {job_id}: {exc}")
        finally:
            if notification_data.get("next_run_at") is None:
                self._job_bindings.pop(job_id, None)

    @staticmethod
    def _is_cron_tool(tool_name: str | None) -> bool:
        if not isinstance(tool_name, str):
            return False
        normalized = tool_name.strip().lower()
        return normalized == "cron" or normalized.startswith("cron__")

    @staticmethod
    def _extract_job_ids(payload: dict[str, Any]) -> list[str]:
        job_ids: list[str] = []

        primary = payload.get("job_id")
        if isinstance(primary, str) and primary.strip():
            job_ids.append(primary.strip())

        advance = payload.get("advance_reminder")
        if isinstance(advance, dict):
            advance_job_id = advance.get("job_id")
            if isinstance(advance_job_id, str) and advance_job_id.strip():
                job_ids.append(advance_job_id.strip())

        return job_ids

    @staticmethod
    def _render_notification_text(notification_data: dict[str, Any]) -> str:
        summary = str(notification_data.get("summary") or "").strip()
        detail = notification_data.get("detail")
        detail_text = str(detail).strip() if detail is not None else ""

        if summary and detail_text:
            return f"{summary}\n{detail_text}"
        if summary:
            return summary
        return detail_text
