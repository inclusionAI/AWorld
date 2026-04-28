from __future__ import annotations

from typing import Any, TypeAlias, TypedDict

CronPushTarget: TypeAlias = dict[str, Any]
CronPushMeta: TypeAlias = dict[str, Any]


class CronPushBinding(TypedDict, total=False):
    job_id: str
    channel: str
    account_id: str
    conversation_id: str
    sender_id: str
    target: CronPushTarget
    meta: CronPushMeta


class CronNotificationPayload(TypedDict, total=False):
    job_id: str
    summary: str
    detail: str
    next_run_at: str | None
    user_visible: bool


def copy_cron_push_binding(binding: CronPushBinding) -> CronPushBinding:
    return dict(binding)


def with_cron_push_job_id(job_id: str, binding: CronPushBinding) -> CronPushBinding:
    payload = copy_cron_push_binding(binding)
    payload["job_id"] = job_id
    return payload
