# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron tool - manage scheduled tasks through Agent.
"""
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Literal
from pydantic import Field

from croniter import croniter
from aworld.core.tool.func_to_tool import be_tool
from aworld.logs.util import logger


@be_tool(
    tool_name='cron',
    tool_desc="""Manage scheduled tasks (cron jobs).

Actions:
- add: Create a new scheduled task
- list: Show all scheduled tasks
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
- remove: Delete task by ID
- run: Execute task immediately
- enable: Enable task by ID
- disable: Disable task by ID
"""
)
async def cron_tool(
    action: Literal["add", "list", "remove", "run", "enable", "disable", "status"] = Field(
        description="Action to perform: add/list/remove/run/enable/disable/status"
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

    try:
        from aworld.core.scheduler import get_scheduler
        from aworld.core.scheduler.types import CronJob, CronSchedule, CronPayload

        scheduler = get_scheduler()

        if action == "add":
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

            # Build job
            job = CronJob(
                name=name,
                schedule=schedule,
                payload=CronPayload(
                    message=message,
                    agent_name=agent_name or "Aworld",
                    tool_names=tools or [],
                ),
                delete_after_run=delete_after_run or (schedule_type == "at"),
            )

            result = await scheduler.add_job(job)

            return {
                "success": True,
                "job_id": result.id,
                "message": f"Created task '{name}' (ID: {result.id})",
                "next_run": result.state.next_run_at,
            }

        elif action == "list":
            jobs = await scheduler.list_jobs(enabled_only=not include_disabled)
            return {
                "success": True,
                "count": len(jobs),
                "jobs": [
                    {
                        "id": j.id,
                        "name": j.name,
                        "description": j.description,
                        "schedule": format_schedule_local(j.schedule),
                        "next_run": j.state.next_run_at,
                        "last_run": j.state.last_run_at,
                        "enabled": j.enabled,
                        "last_status": j.state.last_status,
                        "last_error": j.state.last_error if j.state.last_error else None,
                    }
                    for j in jobs
                ],
            }

        elif action == "remove":
            if not job_id:
                return {"success": False, "error": "job_id required"}

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

            updated = await scheduler.update_job(job_id, enabled=True)
            if updated:
                return {"success": True, "message": f"Enabled job {job_id}"}
            else:
                return {"success": False, "error": f"Job not found: {job_id}"}

        elif action == "disable":
            if not job_id:
                return {"success": False, "error": "job_id required"}

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
        logger.error(f"Cron tool error: {e}", exc_info=True)
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
