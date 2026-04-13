# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron scheduler module.

Usage:
    from aworld.core.scheduler import get_scheduler

    scheduler = get_scheduler()
    await scheduler.start()
"""
from .types import CronJob, CronSchedule, CronPayload, CronJobState
from .store import FileBasedCronStore
from .executor import CronExecutor
from .scheduler import CronScheduler

__all__ = [
    'CronJob',
    'CronSchedule',
    'CronPayload',
    'CronJobState',
    'FileBasedCronStore',
    'CronExecutor',
    'CronScheduler',
    'get_scheduler',
    'reset_scheduler',
]

# Global scheduler instance
_scheduler_instance = None


def get_scheduler() -> CronScheduler:
    """
    Get global scheduler singleton.

    Returns:
        CronScheduler instance
    """
    global _scheduler_instance

    if _scheduler_instance is None:
        store = FileBasedCronStore(".aworld/cron.json")
        executor = CronExecutor()
        _scheduler_instance = CronScheduler(store, executor)

    return _scheduler_instance


def reset_scheduler():
    """Reset scheduler singleton (for testing)."""
    global _scheduler_instance
    _scheduler_instance = None
