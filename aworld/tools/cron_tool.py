# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron tool - manage scheduled tasks through Agent.
"""
import re
import traceback
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Literal
from pydantic import Field
from pydantic.fields import FieldInfo

from croniter import croniter
from aworld.core.tool.func_to_tool import be_tool
from aworld.logs.util import logger
from aworld.core.scheduler.normalization import normalize_tool_names

DEFAULT_ADVANCE_REMINDER_MINUTES = 10


@be_tool(
    tool_name='cron',
    tool_desc="""Manage scheduled tasks (cron jobs).

Actions:
- add: Create a new scheduled task
- list: Show all scheduled tasks
- show: Show one scheduled task
- remove: Delete a task
- run: Manually trigger a task
- enable: Enable a disabled task
- disable: Disable a task (stops scheduling but keeps the job)
- status: Show scheduler status

Examples:
- add: Schedule "Run tests" at "every 1h"
- add: Schedule "Reminder" at "at 2026-04-09T09:00:00+08:00"
- add: Schedule "Daily check" with cron "0 9 * * *"
- list: Show all tasks
- show: Show task by ID
- remove: Delete task by ID
- run: Execute task immediately
- enable: Enable task by ID
- disable: Disable task by ID
"""
)
async def cron_tool(
    action: Literal["add", "list", "show", "remove", "run", "enable", "disable", "status"] = Field(
        description="Action to perform: add/list/show/remove/run/enable/disable/status"
    ),

    # add parameters
    name: Optional[str] = Field(
        default=None,
        description="Task name (required for add)"
    ),
    message: Optional[str] = Field(
        default=None,
        description="Message/instruction for the agent to execute (required for add)"
    ),
    request: Optional[str] = Field(
        default=None,
        description="Raw natural-language scheduling request for add (e.g., '一分钟后提醒我喝水', '每天早上9点提醒我运行测试')"
    ),
    schedule_type: Optional[Literal["at", "every", "cron"]] = Field(
        default=None,
        description="Schedule type: 'at' (once), 'every' (interval), 'cron' (expression)"
    ),
    schedule_value: Optional[str] = Field(
        default=None,
        description="""Schedule value based on type:
- at: ISO 8601 timestamp (e.g., '2026-04-09T09:00:00+08:00')
- every: interval like '30m', '1h', '2d'
- cron: cron expression (e.g., '0 9 * * *' for daily 9am)
"""
    ),
    agent_name: Optional[str] = Field(
        default="Aworld",
        description="Agent to use for execution"
    ),
    tools: Optional[List[str]] = Field(
        default=None,
        description="List of tool names to enable"
    ),
    delete_after_run: Optional[bool] = Field(
        default=None,
        description="Whether to delete the task after one execution (for one-time reminders)"
    ),
    max_runs: Optional[int] = Field(
        default=None,
        description="Optional maximum execution count for recurring tasks"
    ),

    # update/remove/run parameters
    job_id: Optional[str] = Field(
        default=None,
        description="Job ID for remove/run actions"
    ),

    # list parameters
    include_disabled: Optional[bool] = Field(
        default=True,
        description="Include disabled tasks in list"
    ),
) -> Dict[str, Any]:
    """Execute cron operations."""
    # Import helpers from the canonical module path so dynamic @be_tool wrappers
    # can still resolve them even when this function source is copied before the
    # helper definitions below are executed.
    from aworld.tools.cron_tool import (
        _build_advance_reminder_spec as build_advance_reminder_spec_helper,
        _parse_natural_language_add_request as parse_request_helper,
        _unwrap_fieldinfo as unwrap_fieldinfo_helper,
    )

    action = unwrap_fieldinfo_helper(action)
    name = unwrap_fieldinfo_helper(name)
    message = unwrap_fieldinfo_helper(message)
    request = unwrap_fieldinfo_helper(request)
    schedule_type = unwrap_fieldinfo_helper(schedule_type)
    schedule_value = unwrap_fieldinfo_helper(schedule_value)
    agent_name = unwrap_fieldinfo_helper(agent_name)
    tools = unwrap_fieldinfo_helper(tools)
    delete_after_run = unwrap_fieldinfo_helper(delete_after_run)
    max_runs = unwrap_fieldinfo_helper(max_runs)
    job_id = unwrap_fieldinfo_helper(job_id)
    include_disabled = unwrap_fieldinfo_helper(include_disabled)

    def normalize_agent_name_local(raw_agent_name: Optional[str]) -> str:
        candidate = (raw_agent_name or "Aworld").strip()
        if not candidate:
            return "Aworld"
        if candidate.lower() == "aworld":
            return "Aworld"
        return candidate

    def normalize_max_runs_local(raw_max_runs: Optional[Any]) -> Optional[int]:
        if raw_max_runs is None or raw_max_runs == "":
            return None
        try:
            parsed = int(raw_max_runs)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid max_runs value: {raw_max_runs}") from e
        if parsed <= 0:
            raise ValueError(f"Invalid max_runs value: {raw_max_runs}. Expected a positive integer.")
        return parsed

    try:
        max_runs = normalize_max_runs_local(max_runs)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    tools = normalize_tool_names(tools)

    def parse_duration_local(duration_str: str) -> int:
        match = re.fullmatch(r'(\d+)([smhd])', duration_str.strip())
        if not match:
            raise ValueError(
                f"Invalid duration format: {duration_str}. Use format like '30m', '1h', '2d'"
            )

        value, unit = int(match.group(1)), match.group(2)
        multipliers = {
            's': 1,
            'm': 60,
            'h': 3600,
            'd': 86400
        }
        return value * multipliers[unit]

    def parse_schedule_local(local_schedule_type: str, local_schedule_value: str):
        from aworld.core.scheduler.types import CronSchedule

        if local_schedule_type == "at":
            try:
                datetime.fromisoformat(local_schedule_value.replace('Z', '+00:00'))
            except ValueError as e:
                raise ValueError(
                    f"Invalid ISO 8601 timestamp '{local_schedule_value}'. "
                    f"Expected format: YYYY-MM-DDTHH:MM:SS or YYYY-MM-DDTHH:MM:SS+HH:MM. "
                    f"Error: {e}"
                )
            return CronSchedule(kind="at", at=local_schedule_value)

        if local_schedule_type == "every":
            return CronSchedule(kind="every", every_seconds=parse_duration_local(local_schedule_value))

        if local_schedule_type == "cron":
            try:
                if len(local_schedule_value.split()) != 5:
                    raise ValueError("cron expression must have exactly 5 fields")
                if not croniter.is_valid(local_schedule_value):
                    raise ValueError("croniter validation failed")
                croniter(local_schedule_value, datetime.now())
            except Exception as e:
                raise ValueError(
                    f"Invalid cron expression '{local_schedule_value}'. "
                    f"Expected format: 'MIN HOUR DAY MONTH WEEKDAY' (e.g., '0 9 * * *' for daily at 9:00). "
                    f"Error: {e}"
                )
            return CronSchedule(kind="cron", cron_expr=local_schedule_value)

        raise ValueError(f"Unknown schedule type: {local_schedule_type}")

    def format_schedule_local(schedule: 'CronSchedule') -> str:
        if schedule.kind == "at":
            return f"at {schedule.at}"
        if schedule.kind == "every":
            seconds = schedule.every_seconds
            if seconds < 60:
                return f"every {seconds}s"
            if seconds < 3600:
                return f"every {seconds // 60}m"
            if seconds < 86400:
                return f"every {seconds // 3600}h"
            return f"every {seconds // 86400}d"
        if schedule.kind == "cron":
            return f"cron {schedule.cron_expr}"
        return "unknown"

    def serialize_job_local(job: 'CronJob') -> Dict[str, Any]:
        return {
            "id": job.id,
            "name": job.name,
            "description": job.description,
            "schedule": format_schedule_local(job.schedule),
            "next_run": job.state.next_run_at,
            "last_run": job.state.last_run_at,
            "enabled": job.enabled,
            "running": job.state.running,
            "max_runs": job.payload.max_runs,
            "run_count": job.state.run_count,
            "last_status": job.state.last_status,
            "last_error": job.state.last_error if job.state.last_error else None,
            "last_result_summary": (
                job.state.last_result_summary if job.state.last_result_summary else None
            ),
        }

    def parse_iso_datetime_local(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        return datetime.fromisoformat(value.replace('Z', '+00:00'))

    def resolve_bound_agent_name_local(raw_agent_name: Optional[str], current_scheduler: Any) -> str:
        bound_agent_name = None
        executor = getattr(current_scheduler, "executor", None)
        if executor and hasattr(executor, "get_default_agent_name"):
            try:
                bound_agent_name = executor.get_default_agent_name()
            except Exception:
                bound_agent_name = None

        if bound_agent_name:
            return normalize_agent_name_local(bound_agent_name)
        return normalize_agent_name_local(raw_agent_name)

    def resolve_effective_tool_names_local(
        resolved_agent_name: str,
        requested_tools: List[str],
    ) -> List[str]:
        """
        Do not restrict Aworld/root-agent cron tasks unless explicitly designed otherwise.

        Cron-created automation tasks are often open-ended instructions. When the
        selected runtime root agent is Aworld, constraining tools based on an LLM-
        invented comma string degrades execution quality and can block required tools.
        """
        if resolved_agent_name == "Aworld":
            return []
        return requested_tools

    def is_reactivatable_local(job: 'CronJob', now: datetime) -> bool:
        if job.schedule.kind in ("every", "cron"):
            return True
        if job.schedule.kind != "at":
            return False

        next_run = parse_iso_datetime_local(job.state.next_run_at)
        if next_run:
            return next_run > now

        at_time = parse_iso_datetime_local(job.schedule.at)
        return bool(at_time and at_time > now)

    async def bulk_toggle_local(enable_target: bool) -> Dict[str, Any]:
        now = datetime.now().astimezone()
        jobs = await scheduler.list_jobs(enabled_only=enable_target is False)

        if enable_target:
            candidates = [job for job in jobs if not job.enabled and is_reactivatable_local(job, now)]
            empty_message = "No reactivatable jobs to enable"
            verb = "Enabled"
            summary_label = "reactivatable jobs"
        else:
            candidates = [job for job in jobs if job.enabled and is_reactivatable_local(job, now)]
            empty_message = "No active jobs to disable"
            verb = "Disabled"
            summary_label = "active jobs"

        if not candidates:
            return {
                "success": True,
                "message": empty_message,
                "updated_count": 0,
                "job_ids": [],
            }

        updated_ids = []
        for job in candidates:
            updated = await scheduler.update_job(job.id, enabled=enable_target)
            if updated:
                updated_ids.append(job.id)

        return {
            "success": True,
            "message": f"{verb} {len(updated_ids)} {summary_label}",
            "updated_count": len(updated_ids),
            "job_ids": updated_ids,
        }

    async def bulk_remove_local() -> Dict[str, Any]:
        jobs = await scheduler.list_jobs(enabled_only=False)

        if not jobs:
            return {
                "success": True,
                "message": "No visible jobs to remove",
                "removed_count": 0,
                "job_ids": [],
            }

        removed_ids = []
        for job in jobs:
            removed = await scheduler.remove_job(job.id)
            if removed:
                removed_ids.append(job.id)

        return {
            "success": True,
            "message": f"Removed {len(removed_ids)} visible jobs",
            "removed_count": len(removed_ids),
            "job_ids": removed_ids,
        }

    try:
        from aworld.core.scheduler import get_scheduler
        from aworld.core.scheduler.types import CronJob, CronSchedule, CronPayload

        scheduler = get_scheduler()
        agent_name = resolve_bound_agent_name_local(agent_name, scheduler)
        tools = resolve_effective_tool_names_local(agent_name, tools)

        if action == "add":
            request_schedule_derived = False
            parsed_request = None
            if request:
                try:
                    parsed_request = parse_request_helper(request)
                except ValueError as e:
                    if not all([name, message, schedule_type, schedule_value]):
                        return {"success": False, "error": str(e)}
                    parsed_request = None

                if parsed_request:
                    request_schedule_derived = True
                    if schedule_type and schedule_type != parsed_request["schedule_type"]:
                        logger.warning(
                            "cron_tool(add): overriding LLM-provided "
                            f"schedule_type {schedule_type!r} with request-derived value "
                            f"{parsed_request['schedule_type']!r} for request {request!r}"
                        )
                    if schedule_value and schedule_value != parsed_request["schedule_value"]:
                        logger.warning(
                            "cron_tool(add): overriding LLM-provided "
                            f"schedule_value {schedule_value!r} with request-derived value "
                            f"{parsed_request['schedule_value']!r} for request {request!r}"
                        )

                    name = name or parsed_request["name"]
                    message = message or parsed_request["message"]
                    schedule_type = parsed_request["schedule_type"]
                    schedule_value = parsed_request["schedule_value"]
                    max_runs = max_runs or parsed_request.get("max_runs")
                    if delete_after_run is None:
                        delete_after_run = parsed_request["delete_after_run"]

            # Validate required parameters
            if not all([name, message, schedule_type, schedule_value]):
                return {
                    "success": False,
                    "error": "Missing required parameters: name, message, schedule_type, schedule_value"
                }

            # Parse schedule
            try:
                schedule = parse_schedule_local(schedule_type, schedule_value)
            except ValueError as e:
                return {"success": False, "error": f"Invalid schedule: {str(e)}"}

            if schedule_type == "at" and schedule.at and not request_schedule_derived:
                target_time = datetime.fromisoformat(schedule.at.replace('Z', '+00:00'))
                current_time = _resolve_schedule_now(None)
                if target_time.tzinfo is None:
                    target_time = target_time.replace(tzinfo=current_time.tzinfo)
                if target_time <= current_time.astimezone(target_time.tzinfo):
                    return {
                        "success": False,
                        "error": (
                            f"One-time schedule is already in the past: {schedule.at}. "
                            "Provide a future time or pass a natural-language request such as "
                            "'一分钟后提醒我喝水'."
                        ),
                    }

            # Build job
            job = CronJob(
                name=name,
                schedule=schedule,
                payload=CronPayload(
                    message=message,
                    agent_name=agent_name or "Aworld",
                    tool_names=tools or [],
                    max_runs=max_runs,
                ),
                delete_after_run=delete_after_run or (schedule_type == "at"),
            )

            result = await scheduler.add_job(job)

            advance_reminder = None
            try:
                reminder_body_hint = None
                if parsed_request:
                    reminder_body_hint = parsed_request.get("message")

                advance_spec = build_advance_reminder_spec_helper(
                    name=name,
                    message=message,
                    schedule_type=schedule_type,
                    schedule_value=schedule_value,
                    request_text=request,
                    tools=tools,
                    reminder_body_hint=reminder_body_hint,
                )
                if advance_spec:
                    advance_job = CronJob(
                        name=advance_spec["name"],
                        schedule=parse_schedule_local(
                            advance_spec["schedule_type"],
                            advance_spec["schedule_value"],
                        ),
                        payload=CronPayload(
                            message=advance_spec["message"],
                            agent_name=agent_name or "Aworld",
                            tool_names=[],
                        ),
                        delete_after_run=advance_spec["delete_after_run"],
                    )
                    advance_result = await scheduler.add_job(advance_job)
                    advance_reminder = {
                        "job_id": advance_result.id,
                        "name": advance_result.name,
                        "next_run": advance_result.state.next_run_at,
                        "display": _format_confirmed_time_display(advance_result.state.next_run_at),
                        "lead_minutes": DEFAULT_ADVANCE_REMINDER_MINUTES,
                    }
            except Exception as advance_error:
                logger.warning(
                    "cron_tool(add): failed to create advance reminder for "
                    f"{name!r}: {advance_error}"
                )

            response = {
                "success": True,
                "job_id": result.id,
                "message": f"Created task '{name}' (ID: {result.id})",
                "next_run": result.state.next_run_at,
                "next_run_display": _format_confirmed_time_display(result.state.next_run_at),
            }
            if advance_reminder:
                response["advance_reminder"] = advance_reminder
            return response

        elif action == "list":
            jobs = await scheduler.list_jobs(enabled_only=not include_disabled)
            return {
                "success": True,
                "count": len(jobs),
                "jobs": [serialize_job_local(j) for j in jobs],
            }

        elif action == "show":
            if not job_id:
                return {"success": False, "error": "job_id required"}

            job = await scheduler.get_job(job_id)
            if not job:
                return {"success": False, "error": f"Job not found: {job_id}"}

            return {
                "success": True,
                "job": serialize_job_local(job),
            }

        elif action == "remove":
            if not job_id:
                return {"success": False, "error": "job_id required"}

            if job_id == "all":
                return await bulk_remove_local()

            removed = await scheduler.remove_job(job_id)
            if removed:
                return {"success": True, "message": f"Removed job {job_id}"}
            else:
                return {"success": False, "error": f"Job not found: {job_id}"}

        elif action == "run":
            if not job_id:
                return {"success": False, "error": "job_id required"}

            result = await scheduler.run_job(job_id, force=True)
            return {
                "success": result.success,
                "message": f"Job executed" if result.success else f"Job failed: {result.msg}",
                "result": result.answer if hasattr(result, 'answer') else None,
            }

        elif action == "enable":
            if not job_id:
                return {"success": False, "error": "job_id required"}

            if job_id == "all":
                return await bulk_toggle_local(enable_target=True)

            updated = await scheduler.update_job(job_id, enabled=True)
            if updated:
                return {"success": True, "message": f"Enabled job {job_id}"}
            else:
                return {"success": False, "error": f"Job not found: {job_id}"}

        elif action == "disable":
            if not job_id:
                return {"success": False, "error": "job_id required"}

            if job_id == "all":
                return await bulk_toggle_local(enable_target=False)

            updated = await scheduler.update_job(job_id, enabled=False)
            if updated:
                return {"success": True, "message": f"Disabled job {job_id}"}
            else:
                return {"success": False, "error": f"Job not found: {job_id}"}

        elif action == "status":
            status = await scheduler.get_status()
            return {
                "success": True,
                "scheduler_running": status["running"],
                "total_jobs": status["total_jobs"],
                "enabled_jobs": status["enabled_jobs"],
            }

        else:
            return {"success": False, "error": f"Unknown action: {action}"}

    except Exception as e:
        logger.error(f"Cron tool error: {e}\n{traceback.format_exc()}")
        return {
            "success": False,
            "error": f"Internal error: {str(e)}"
        }


def _parse_schedule(schedule_type: str, schedule_value: str) -> 'CronSchedule':
    """
    Parse and validate schedule input.

    Args:
        schedule_type: One of 'at', 'cron', 'every'
        schedule_value: Schedule specification string

    Returns:
        CronSchedule object

    Raises:
        ValueError: If schedule_value is invalid for the given type
            - 'at': Must be valid ISO 8601 timestamp
            - 'cron': Must be valid 5 or 6-field cron expression
            - 'every': Must match duration format (e.g., '30m', '2h', '1d')
    """
    from aworld.core.scheduler.types import CronSchedule

    if schedule_type == "at":
        # Validate ISO 8601 timestamp format
        try:
            datetime.fromisoformat(schedule_value.replace('Z', '+00:00'))
        except ValueError as e:
            raise ValueError(
                f"Invalid ISO 8601 timestamp '{schedule_value}'. "
                f"Expected format: YYYY-MM-DDTHH:MM:SS or YYYY-MM-DDTHH:MM:SS+HH:MM. "
                f"Error: {e}"
            )
        return CronSchedule(kind="at", at=schedule_value)

    elif schedule_type == "every":
        # Parse duration (e.g., "30m", "1h", "2d")
        seconds = _parse_duration(schedule_value)
        return CronSchedule(kind="every", every_seconds=seconds)

    elif schedule_type == "cron":
        # Validate cron expression (5 or 6 fields)
        try:
            if len(schedule_value.split()) != 5:
                raise ValueError("cron expression must have exactly 5 fields")
            if not croniter.is_valid(schedule_value):
                raise ValueError("croniter validation failed")
            croniter(schedule_value, datetime.now())
        except Exception as e:
            raise ValueError(
                f"Invalid cron expression '{schedule_value}'. "
                f"Expected format: 'MIN HOUR DAY MONTH WEEKDAY' (e.g., '0 9 * * *' for daily at 9:00). "
                f"Error: {e}"
            )
        return CronSchedule(kind="cron", cron_expr=schedule_value)

    else:
        raise ValueError(f"Unknown schedule type: {schedule_type}")


def _parse_natural_language_add_request(request: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Parse a common natural-language reminder request into cron add parameters.

    Supported examples:
    - 一分钟后提醒我喝水
    - 提醒我一分钟后喝水
    - 30分钟后提醒我提交代码
    - 每天早上9点提醒我运行测试
    - 明天下午3点提醒我开会
    """
    text = (request or "").strip()
    if not text:
        raise ValueError("Unsupported natural-language schedule request: empty request")

    current_time = _resolve_schedule_now(now)

    relative_match = re.fullmatch(
        r"(?P<num>\d+|[零一二两三四五六七八九十]+)\s*(?P<unit>秒钟?|秒|分钟|分|小时|个小时|小时|天|日)后提醒我(?P<body>.+)",
        text,
    )
    if relative_match:
        quantity = _parse_natural_language_number(relative_match.group("num"))
        unit = relative_match.group("unit")
        body = relative_match.group("body")
        delta = _natural_language_delta(quantity, unit)
        target_time = current_time + delta
        message = _normalize_reminder_message(body)
        return {
            "name": _reminder_name_from_message(message),
            "message": message,
            "schedule_type": "at",
            "schedule_value": target_time.isoformat(timespec="seconds"),
            "delete_after_run": True,
        }

    leading_relative_match = re.fullmatch(
        r"提醒我(?P<num>\d+|[零一二两三四五六七八九十]+)\s*"
        r"(?P<unit>秒钟?|秒|分钟|分|小时|个小时|小时|天|日)后(?P<body>.+)",
        text,
    )
    if leading_relative_match:
        quantity = _parse_natural_language_number(leading_relative_match.group("num"))
        unit = leading_relative_match.group("unit")
        body = leading_relative_match.group("body")
        delta = _natural_language_delta(quantity, unit)
        target_time = current_time + delta
        message = _normalize_reminder_message(body)
        return {
            "name": _reminder_name_from_message(message),
            "message": message,
            "schedule_type": "at",
            "schedule_value": target_time.isoformat(timespec="seconds"),
            "delete_after_run": True,
        }

    bounded_every_match = re.fullmatch(
        r"每\s*(?P<num>\d+|[零一二两三四五六七八九十百]+)\s*"
        r"(?P<unit>秒钟?|秒|分钟|分|小时|个小时|小时|天|日)\s*"
        r"(?:提醒我(?P<body1>.+?)|给我发(?:一次)?(?P<body2>.+?))"
        r"(?:提醒|通知)?\s*[，,]?\s*"
        r"(?:一共|总共|共)?(?:发送)?(?P<count>\d+|[零一二两三四五六七八九十百]+)次"
        r"(?:就可以|即可|就行|便可|就够了|就好了)?",
        text,
    )
    if bounded_every_match:
        quantity = _parse_natural_language_number(bounded_every_match.group("num"))
        count = _parse_natural_language_number(bounded_every_match.group("count"))
        unit = bounded_every_match.group("unit")
        body = bounded_every_match.group("body1") or bounded_every_match.group("body2") or ""
        clean_body = body.strip()
        for suffix in ("提醒", "通知"):
            if clean_body.endswith(suffix):
                clean_body = clean_body[:-len(suffix)].rstrip()
        message = _normalize_reminder_message(clean_body)
        return {
            "name": _reminder_name_from_message(message),
            "message": message,
            "schedule_type": "every",
            "schedule_value": _format_duration_for_every(quantity, unit),
            "delete_after_run": False,
            "max_runs": count,
        }

    daily_match = re.fullmatch(
        r"每天(?:(?P<period>早上|上午|中午|下午|晚上))?(?P<hour>\d{1,2})点(?P<half>半)?提醒我(?P<body>.+)",
        text,
    )
    if daily_match:
        hour = _normalize_hour(daily_match.group("period"), int(daily_match.group("hour")))
        minute = 30 if daily_match.group("half") else 0
        body = daily_match.group("body")
        message = _normalize_reminder_message(body)
        return {
            "name": _reminder_name_from_message(message),
            "message": message,
            "schedule_type": "cron",
            "schedule_value": f"{minute} {hour} * * *",
            "delete_after_run": False,
        }

    tomorrow_match = re.fullmatch(
        r"明天(?:(?P<period>早上|上午|中午|下午|晚上))?(?P<hour>\d{1,2})点(?P<half>半)?提醒我(?P<body>.+)",
        text,
    )
    if tomorrow_match:
        hour = _normalize_hour(tomorrow_match.group("period"), int(tomorrow_match.group("hour")))
        minute = 30 if tomorrow_match.group("half") else 0
        body = tomorrow_match.group("body")
        target_time = (current_time + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        message = _normalize_reminder_message(body)
        return {
            "name": _reminder_name_from_message(message),
            "message": message,
            "schedule_type": "at",
            "schedule_value": target_time.isoformat(timespec="seconds"),
            "delete_after_run": True,
        }

    weekday_match = _parse_weekday_timed_reminder(text, current_time)
    if weekday_match:
        return weekday_match

    raise ValueError(
        f"Unsupported natural-language schedule request: {request}. "
        "Use explicit schedule fields or a supported reminder phrase such as "
        "'一分钟后提醒我喝水', '每天早上9点提醒我运行测试', or '下周三中午12点提醒我开会'."
    )


def _parse_weekday_timed_reminder(text: str, current_time: datetime) -> Optional[Dict[str, Any]]:
    patterns = (
        r"(?P<week_ref>下周|下星期|本周|这周|本星期|这星期|周|星期)(?P<weekday>[一二三四五六日天])"
        r"\s*[，,]?\s*(?:(?P<period>早上|上午|中午|下午|晚上))?\s*(?P<hour>\d{1,2})点(?P<half>半)?"
        r"提醒我(?P<body>.+)",
        r"(?P<week_ref>下周|下星期|本周|这周|本星期|这星期|周|星期)(?P<weekday>[一二三四五六日天])"
        r"\s*[，,]?\s*(?:(?P<period>早上|上午|中午|下午|晚上))?\s*(?P<hour>\d{1,2})点(?P<half>半)?"
        r"(?P<body>.+?)(?:的)?提醒",
    )

    for pattern in patterns:
        match = re.fullmatch(pattern, text)
        if not match:
            continue

        hour = _normalize_hour(match.group("period"), int(match.group("hour")))
        minute = 30 if match.group("half") else 0
        body = match.group("body")
        target_time = _resolve_weekday_target_time(
            current_time=current_time,
            week_ref=match.group("week_ref"),
            weekday_token=match.group("weekday"),
            hour=hour,
            minute=minute,
        )
        message = _normalize_reminder_message(body)
        return {
            "name": _reminder_name_from_message(message),
            "message": message,
            "schedule_type": "at",
            "schedule_value": target_time.isoformat(timespec="seconds"),
            "delete_after_run": True,
        }

    return None


def _resolve_schedule_now(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now().astimezone()
    if now.tzinfo is None:
        return now.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return now


def _resolve_weekday_target_time(
    current_time: datetime,
    week_ref: str,
    weekday_token: str,
    hour: int,
    minute: int,
) -> datetime:
    weekday_index = _weekday_token_to_index(weekday_token)
    week_scope = _normalize_week_reference(week_ref)
    current_date = current_time.date()

    if week_scope in {"current", "next"}:
        start_of_week = current_date - timedelta(days=current_time.weekday())
        days_offset = weekday_index + (7 if week_scope == "next" else 0)
        target_date = start_of_week + timedelta(days=days_offset)
    else:
        days_ahead = weekday_index - current_time.weekday()
        target_date = current_date + timedelta(days=days_ahead)

    target_time = current_time.replace(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    if target_time <= current_time:
        target_time += timedelta(days=7)
    return target_time


def _normalize_week_reference(value: str) -> str:
    if value.startswith("下"):
        return "next"
    if value.startswith("本") or value.startswith("这"):
        return "current"
    return "upcoming"


def _weekday_token_to_index(value: str) -> int:
    weekday_map = {
        "一": 0,
        "二": 1,
        "三": 2,
        "四": 3,
        "五": 4,
        "六": 5,
        "日": 6,
        "天": 6,
    }
    if value not in weekday_map:
        raise ValueError(f"Unsupported weekday token in reminder request: {value}")
    return weekday_map[value]


def _natural_language_delta(quantity: int, unit: str) -> timedelta:
    if unit in {"秒", "秒钟"}:
        return timedelta(seconds=quantity)
    if unit in {"分", "分钟"}:
        return timedelta(minutes=quantity)
    if unit in {"小时", "个小时"}:
        return timedelta(hours=quantity)
    if unit in {"天", "日"}:
        return timedelta(days=quantity)
    raise ValueError(f"Unsupported relative reminder unit: {unit}")


def _format_duration_for_every(quantity: int, unit: str) -> str:
    if unit in {"秒", "秒钟"}:
        return f"{quantity}s"
    if unit in {"分", "分钟"}:
        return f"{quantity}m"
    if unit in {"小时", "个小时"}:
        return f"{quantity}h"
    if unit in {"天", "日"}:
        return f"{quantity}d"
    raise ValueError(f"Unsupported recurring reminder unit: {unit}")


def _parse_natural_language_number(value: str) -> int:
    if value.isdigit():
        return int(value)

    digit_map = {
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }

    if value == "十":
        return 10

    if "十" in value:
        parts = value.split("十", 1)
        tens = digit_map.get(parts[0], 1) if parts[0] else 1
        ones = digit_map.get(parts[1], 0) if parts[1] else 0
        return tens * 10 + ones

    if value in digit_map:
        return digit_map[value]

    raise ValueError(f"Unsupported natural-language number in reminder request: {value}")


def _normalize_reminder_message(body: str) -> str:
    clean_body = body.strip().rstrip("。.!！")
    if clean_body.startswith("提醒我"):
        return clean_body
    return f"提醒我{clean_body}"


def _reminder_name_from_message(message: str) -> str:
    body = message
    if body.startswith("提醒我"):
        body = body[len("提醒我"):]
    return f"提醒：{body.strip()}"


def _build_advance_reminder_spec(
    name: str,
    message: str,
    schedule_type: str,
    schedule_value: str,
    request_text: Optional[str] = None,
    tools: Optional[List[str]] = None,
    reminder_body_hint: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    if not _is_notification_reminder(name=name, message=message, tools=tools):
        return None
    if _is_relative_reminder_request(request_text):
        return None

    lead_time = timedelta(minutes=DEFAULT_ADVANCE_REMINDER_MINUTES)
    reminder_body = _extract_reminder_body(reminder_body_hint) or _extract_reminder_body(message) or _extract_reminder_body(name)
    if not reminder_body:
        return None

    if schedule_type != "at":
        shifted_cron = _shift_simple_daily_cron(schedule_value, lead_time) if schedule_type == "cron" else None
        if not shifted_cron:
            return None
        return {
            "name": f"{_reminder_name_from_message(f'提醒我{reminder_body}')}（提前{DEFAULT_ADVANCE_REMINDER_MINUTES}分钟）",
            "message": f"提醒我{reminder_body}，还有{DEFAULT_ADVANCE_REMINDER_MINUTES}分钟",
            "schedule_type": "cron",
            "schedule_value": shifted_cron,
            "delete_after_run": False,
        }

    target_time = _parse_iso_datetime(schedule_value)
    current_time = _resolve_schedule_now(now)
    if target_time.tzinfo is None:
        target_time = target_time.replace(tzinfo=current_time.tzinfo)
    current_time = current_time.astimezone(target_time.tzinfo)
    advance_time = target_time - lead_time
    if advance_time <= current_time:
        return None

    return {
        "name": f"{_reminder_name_from_message(f'提醒我{reminder_body}')}（提前{DEFAULT_ADVANCE_REMINDER_MINUTES}分钟）",
        "message": f"提醒我{reminder_body}，还有{DEFAULT_ADVANCE_REMINDER_MINUTES}分钟",
        "schedule_type": "at",
        "schedule_value": advance_time.isoformat(timespec="seconds"),
        "delete_after_run": True,
    }


def _is_notification_reminder(name: str, message: str, tools: Optional[List[str]] = None) -> bool:
    if tools:
        return False

    name_text = (name or "").strip().lower()
    message_text = (message or "").strip().lower()
    if not name_text and not message_text:
        return False

    keywords = ("提醒", "remind", "reminder")
    return any(keyword in name_text or keyword in message_text for keyword in keywords)


def _extract_reminder_body(text: Optional[str]) -> Optional[str]:
    value = (text or "").strip()
    if not value:
        return None

    value = re.sub(r"^提醒(?:我|用户|您)?[:：]?", "", value).strip()
    value = re.sub(r"[:：]?提醒$", "", value).strip()
    value = re.sub(r"^还有\d+\s*分钟[，,]?", "", value).strip()
    value = re.sub(r"[（(]提前\d+分钟[）)]$", "", value).strip()
    return value or None


def _parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_confirmed_time_display(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    try:
        dt = _parse_iso_datetime(value)
    except ValueError:
        return None

    weekday_map = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
    return (
        f"{dt.year}年{dt.month}月{dt.day}日"
        f"（{weekday_map[dt.weekday()]}）"
        f"{dt.hour:02d}:{dt.minute:02d}"
    )


def _shift_simple_daily_cron(schedule_value: str, lead_time: timedelta) -> Optional[str]:
    parts = schedule_value.split()
    if len(parts) != 5 or parts[2:] != ["*", "*", "*"]:
        return None
    if not parts[0].isdigit() or not parts[1].isdigit():
        return None

    base_time = datetime(2000, 1, 2, int(parts[1]), int(parts[0]), 0)
    shifted_time = base_time - lead_time
    return f"{shifted_time.minute} {shifted_time.hour} * * *"


def _is_relative_reminder_request(text: Optional[str]) -> bool:
    value = (text or "").strip()
    if not value:
        return False

    return bool(
        re.search(
            r"(?:\d+|[零一二两三四五六七八九十百]+)\s*(?:秒钟?|秒|分钟|分|小时|个小时|小时|天|日)后提醒我"
            r"|提醒我(?:\d+|[零一二两三四五六七八九十百]+)\s*(?:秒钟?|秒|分钟|分|小时|个小时|小时|天|日)后",
            value,
        )
    )


def _normalize_hour(period: Optional[str], hour: int) -> int:
    if hour < 0 or hour > 23:
        raise ValueError(f"Unsupported hour value in reminder request: {hour}")

    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    elif period == "中午" and hour < 11:
        hour = 12

    if hour > 23:
        raise ValueError(f"Unsupported hour value in reminder request: {hour}")

    return hour


def _unwrap_fieldinfo(value: Any) -> Any:
    if isinstance(value, FieldInfo):
        return value.default
    return value


def _parse_duration(duration_str: str) -> int:
    """
    Parse duration string to seconds.

    Supports: 30s, 5m, 2h, 1d

    Args:
        duration_str: Duration string

    Returns:
        Seconds

    Raises:
        ValueError: If format is invalid
    """
    match = re.fullmatch(r'(\d+)([smhd])', duration_str.strip())
    if not match:
        raise ValueError(f"Invalid duration format: {duration_str}. Use format like '30m', '1h', '2d'")

    value, unit = int(match.group(1)), match.group(2)
    multipliers = {
        's': 1,
        'm': 60,
        'h': 3600,
        'd': 86400
    }

    return value * multipliers[unit]


def _format_schedule(schedule: 'CronSchedule') -> str:
    """
    Format schedule for display.

    Args:
        schedule: CronSchedule instance

    Returns:
        Human-readable string
    """
    if schedule.kind == "at":
        return f"at {schedule.at}"
    elif schedule.kind == "every":
        seconds = schedule.every_seconds
        if seconds < 60:
            return f"every {seconds}s"
        elif seconds < 3600:
            return f"every {seconds // 60}m"
        elif seconds < 86400:
            return f"every {seconds // 3600}h"
        else:
            return f"every {seconds // 86400}d"
    elif schedule.kind == "cron":
        return f"cron {schedule.cron_expr}"
    else:
        return "unknown"
