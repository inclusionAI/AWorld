from __future__ import annotations

from typing import Any, TypeAlias, TypedDict

CronPushTarget: TypeAlias = dict[str, Any]
CronPushMeta: TypeAlias = dict[str, Any]
_SCALAR_BINDING_FIELDS = (
    "job_id",
    "channel",
    "account_id",
    "conversation_id",
    "sender_id",
)
_MAPPING_BINDING_FIELDS = ("target", "meta")


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


def normalize_cron_push_binding(
    binding: object,
    *,
    job_id: str | None = None,
) -> CronPushBinding | None:
    if not isinstance(binding, dict):
        return None

    normalized: CronPushBinding = {}
    for field in _SCALAR_BINDING_FIELDS:
        value = binding.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            return None
        normalized[field] = value

    for field in _MAPPING_BINDING_FIELDS:
        value = binding.get(field)
        if value is None:
            continue
        if not isinstance(value, dict):
            return None
        normalized[field] = dict(value)

    if job_id is not None:
        normalized["job_id"] = job_id

    return normalized
