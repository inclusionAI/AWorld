from __future__ import annotations

from inspect import isawaitable
import json
from typing import Any, Awaitable, Callable

from aworld_gateway.cron_push.formatter import CronNotificationFormatter
from aworld_gateway.logging import get_gateway_logger
from aworld_gateway.cron_push.store import CronPushBindingStore
from aworld_gateway.cron_push.types import (
    CronNotificationPayload,
    CronPushBinding,
    normalize_cron_push_binding,
)

CronPushSender = Callable[
    [CronPushBinding, str, CronNotificationPayload],
    Awaitable[None],
]
logger = get_gateway_logger("cron_push.bridge")


class CronPushBridge:
    def __init__(self, *, binding_store: CronPushBindingStore) -> None:
        self._binding_store = binding_store
        self._senders: dict[str, CronPushSender] = {}
        self._installed_scheduler_sinks: dict[int, Any] = {}
        self._previous_scheduler_sinks: dict[int, Any] = {}

    def register_sender(self, channel: str, sender: CronPushSender) -> None:
        normalized_channel = str(channel or "").strip().lower()
        if not normalized_channel:
            return
        self._senders[normalized_channel] = sender
        logger.info(f"Cron push sender registered channel={normalized_channel}")

    def bind_output(
        self,
        output: Any,
        binding_context: CronPushBinding,
    ) -> list[str]:
        binding = normalize_cron_push_binding(binding_context)
        if binding is None:
            return []

        job_ids = self.extract_job_ids(output)
        for job_id in job_ids:
            self._binding_store.upsert(job_id, binding)
            logger.info(
                "Cron push binding stored "
                f"channel={binding.get('channel')} job_id={job_id}"
            )
        return job_ids

    async def publish_notification(self, notification: CronNotificationPayload) -> None:
        job_id = str(notification.get("job_id") or "").strip()
        if not job_id:
            logger.info("Cron push notification skipped reason=missing_job_id")
            return

        binding = self._binding_store.get(job_id)
        if binding is None:
            logger.info(
                f"Cron push notification skipped job_id={job_id} reason=binding_missing"
            )
            return

        sender = self._senders.get(str(binding.get("channel") or "").strip().lower())
        user_visible = notification.get("user_visible") is not False
        text = CronNotificationFormatter.format(notification)
        is_terminal = not notification.get("next_run_at")
        if sender is None:
            logger.info(
                "Cron push notification skipped "
                f"channel={binding.get('channel')} job_id={job_id} reason=sender_missing"
            )
        elif not user_visible:
            logger.info(
                "Cron push notification skipped "
                f"channel={binding.get('channel')} job_id={job_id} reason=user_hidden"
            )
        elif not text:
            logger.info(
                "Cron push notification skipped "
                f"channel={binding.get('channel')} job_id={job_id} reason=empty_text"
            )
        else:
            logger.info(
                "Cron push notification publishing "
                f"channel={binding.get('channel')} job_id={job_id}"
            )
        try:
            if sender is not None and user_visible and text:
                await sender(binding, text, notification)
        except Exception as exc:
            logger.exception(
                "Cron push sender failed "
                f"channel={binding.get('channel')} job_id={job_id} error={exc}"
            )
        finally:
            if is_terminal:
                self._binding_store.remove(job_id)
                logger.info(
                    f"Cron push binding removed job_id={job_id} reason=terminal_notification"
                )

    def install_scheduler_sink(self, scheduler: Any) -> None:
        scheduler_key = id(scheduler)
        existing_sink = self._installed_scheduler_sinks.get(scheduler_key)
        if (
            scheduler_key in self._installed_scheduler_sinks
            and existing_sink is getattr(scheduler, "notification_sink", None)
        ):
            return

        previous_sink = getattr(scheduler, "notification_sink", None)

        async def _fanout(notification: CronNotificationPayload) -> None:
            if previous_sink is not None:
                previous_result = previous_sink(notification)
                if isawaitable(previous_result):
                    await previous_result
            await self.publish_notification(notification)

        scheduler.notification_sink = _fanout
        self._installed_scheduler_sinks[scheduler_key] = _fanout
        self._previous_scheduler_sinks[scheduler_key] = previous_sink
        logger.info(f"Cron push scheduler sink installed scheduler={scheduler_key}")

    def uninstall_scheduler_sink(self, scheduler: Any) -> None:
        scheduler_key = id(scheduler)
        installed_sink = self._installed_scheduler_sinks.pop(scheduler_key, None)
        previous_sink = self._previous_scheduler_sinks.pop(scheduler_key, None)
        if installed_sink is None:
            return
        if getattr(scheduler, "notification_sink", None) is installed_sink:
            scheduler.notification_sink = previous_sink
        logger.info(f"Cron push scheduler sink uninstalled scheduler={scheduler_key}")

    @staticmethod
    def extract_job_ids(output: Any) -> list[str]:
        output_type = getattr(output, "output_type", lambda: None)()
        if output_type != "tool_call_result":
            return []

        tool_name = str(getattr(output, "tool_name", "") or "").strip().lower()
        if tool_name != "cron":
            return []

        payload = CronPushBridge._coerce_payload(getattr(output, "data", None))
        if not isinstance(payload, dict):
            return []
        if payload.get("success") is not True:
            return []

        job_ids: list[str] = []
        primary_job_id = str(payload.get("job_id") or "").strip()
        if primary_job_id:
            job_ids.append(primary_job_id)

        advance_reminder = payload.get("advance_reminder")
        if isinstance(advance_reminder, dict):
            advance_job_id = str(advance_reminder.get("job_id") or "").strip()
            if advance_job_id:
                job_ids.append(advance_job_id)

        deduped: list[str] = []
        seen: set[str] = set()
        for job_id in job_ids:
            if job_id in seen:
                continue
            seen.add(job_id)
            deduped.append(job_id)
        return deduped

    @staticmethod
    def _coerce_payload(value: Any) -> Any:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value
