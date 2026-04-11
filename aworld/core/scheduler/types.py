# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron scheduler data models.

Simplified models for MVP - isolated mode only.
"""
from dataclasses import dataclass, field
from typing import Literal, Optional, List
import uuid
from datetime import UTC, datetime


@dataclass
class CronSchedule:
    """Scheduling configuration."""
    kind: Literal["at", "every", "cron"]

    # at: One-time task (ISO 8601 timestamp)
    at: Optional[str] = None

    # every: Interval repetition (seconds)
    every_seconds: Optional[int] = None

    # cron: Cron expression
    cron_expr: Optional[str] = None
    timezone: str = "UTC"


@dataclass
class CronPayload:
    """Task execution content (serializable)."""
    message: str                          # Task input
    agent_name: str = "Aworld"           # Agent to use
    tool_names: List[str] = field(default_factory=list)
    timeout_seconds: Optional[int] = None


@dataclass
class CronJobState:
    """Runtime state."""
    next_run_at: Optional[str] = None     # ISO 8601
    last_run_at: Optional[str] = None
    last_status: Optional[Literal["ok", "error", "timeout"]] = None
    last_error: Optional[str] = None
    running: bool = False
    consecutive_errors: int = 0


@dataclass
class CronJob:
    """Cron task definition (serializable to file)."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: Optional[str] = None
    enabled: bool = True
    delete_after_run: bool = False        # One-time task

    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="cron"))
    payload: CronPayload = field(default_factory=lambda: CronPayload(message=""))
    state: CronJobState = field(default_factory=CronJobState)

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
