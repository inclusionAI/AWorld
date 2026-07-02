from __future__ import annotations

from aworld_gateway.cron_push.types import CronNotificationPayload


class CronNotificationFormatter:
    @staticmethod
    def format(notification: CronNotificationPayload) -> str:
        summary = str(notification.get("summary") or "").strip()
        detail = str(notification.get("detail") or "").strip()
        next_run_at = str(notification.get("next_run_at") or "").strip()

        lines: list[str] = []
        if summary:
            lines.append(summary)
        if detail and detail != summary:
            lines.append(detail)
        if next_run_at:
            lines.append(f"下次执行：{next_run_at}")

        return "\n".join(lines).strip()
