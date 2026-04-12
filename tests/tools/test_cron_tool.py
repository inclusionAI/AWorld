# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Tests for cron_tool schedule parsing and validation.
"""
import pytest
from datetime import datetime, timedelta, timezone


class TestParseScheduleValidation:
    """Test schedule input validation in _parse_schedule()."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Import _parse_schedule lazily to avoid tool initialization."""
        from aworld.tools.cron_tool import _parse_schedule, _parse_natural_language_add_request
        self._parse_schedule = _parse_schedule
        self._parse_natural_language_add_request = _parse_natural_language_add_request

    def test_parse_schedule_valid_at(self):
        """Test valid ISO 8601 timestamps are accepted."""
        valid_timestamps = [
            "2026-04-09T09:00:00+08:00",
            "2026-04-09T01:00:00Z",
            "2026-12-31T23:59:59+00:00",
            "2026-01-01T00:00:00",
        ]

        for timestamp in valid_timestamps:
            schedule = self._parse_schedule("at", timestamp)
            assert schedule.kind == "at"
            assert schedule.at == timestamp

    def test_parse_schedule_invalid_at(self):
        """Test invalid ISO 8601 timestamps raise ValueError."""
        invalid_timestamps = [
            "not-a-date",
            "2026-13-01T00:00:00",  # Invalid month
            "2026-04-32T00:00:00",  # Invalid day
            "invalid format",
            "2026/04/09 09:00:00",  # Wrong separator
        ]

        for timestamp in invalid_timestamps:
            with pytest.raises(ValueError) as exc_info:
                self._parse_schedule("at", timestamp)
            assert "Invalid ISO 8601 timestamp" in str(exc_info.value)
            assert timestamp in str(exc_info.value)

    def test_parse_schedule_valid_cron(self):
        """Test valid cron expressions are accepted."""
        valid_cron_exprs = [
            "0 9 * * *",      # Daily at 9:00
            "*/5 * * * *",    # Every 5 minutes
            "0 0 1 * *",      # First day of month
            "30 2 * * 1-5",   # Weekdays at 2:30 AM
        ]

        for cron_expr in valid_cron_exprs:
            schedule = self._parse_schedule("cron", cron_expr)
            assert schedule.kind == "cron"
            assert schedule.cron_expr == cron_expr

    def test_parse_schedule_invalid_cron(self):
        """Test invalid cron expressions raise ValueError."""
        invalid_cron_exprs = [
            "invalid",
            "99 99 * * *",    # Out of range
            "* * * *",        # Too few fields
            "* * * * * * *",  # Too many fields
            "a b c d e",      # Non-numeric
        ]

        for cron_expr in invalid_cron_exprs:
            with pytest.raises(ValueError) as exc_info:
                self._parse_schedule("cron", cron_expr)
            assert "Invalid cron expression" in str(exc_info.value)
            assert cron_expr in str(exc_info.value)

    def test_parse_schedule_valid_every(self):
        """Test valid duration formats are accepted."""
        valid_durations = [
            ("30s", 30),
            ("5m", 300),
            ("2h", 7200),
            ("1d", 86400),
        ]

        for duration_str, expected_seconds in valid_durations:
            schedule = self._parse_schedule("every", duration_str)
            assert schedule.kind == "every"
            assert schedule.every_seconds == expected_seconds

    def test_parse_schedule_invalid_every(self):
        """Test invalid duration formats raise ValueError."""
        invalid_durations = [
            "5minutes",       # Full word not allowed
            "2hours",         # Full word not allowed
            "30",             # No unit
            "m5",             # Wrong order
        ]

        for duration in invalid_durations:
            with pytest.raises(ValueError) as exc_info:
                self._parse_schedule("every", duration)
            assert "Invalid duration format" in str(exc_info.value)

    def test_parse_schedule_unknown_type(self):
        """Test unknown schedule type raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            self._parse_schedule("unknown", "value")
        assert "Unknown schedule type" in str(exc_info.value)

    def test_parse_natural_language_relative_reminder_in_chinese(self):
        """Test common Chinese relative reminder requests are parsed to one-shot schedules."""
        now = datetime(2026, 4, 11, 23, 21, 0, tzinfo=timezone(timedelta(hours=8)))

        parsed = self._parse_natural_language_add_request("一分钟后提醒我喝水", now=now)

        assert parsed["name"] == "提醒：喝水"
        assert parsed["message"] == "提醒我喝水"
        assert parsed["schedule_type"] == "at"
        assert parsed["schedule_value"] == "2026-04-11T23:22:00+08:00"
        assert parsed["delete_after_run"] is True

    def test_parse_natural_language_daily_recurring_reminder_in_chinese(self):
        """Test common daily reminder requests are parsed to cron schedules."""
        now = datetime(2026, 4, 11, 23, 21, 0, tzinfo=timezone(timedelta(hours=8)))

        parsed = self._parse_natural_language_add_request("每天早上9点提醒我运行测试", now=now)

        assert parsed["name"] == "提醒：运行测试"
        assert parsed["message"] == "提醒我运行测试"
        assert parsed["schedule_type"] == "cron"
        assert parsed["schedule_value"] == "0 9 * * *"
        assert parsed["delete_after_run"] is False

    def test_parse_natural_language_bounded_every_reminder_in_chinese(self):
        """Test bounded recurring reminders are parsed with interval and max run count."""
        now = datetime(2026, 4, 11, 23, 21, 0, tzinfo=timezone(timedelta(hours=8)))

        parsed = self._parse_natural_language_add_request(
            "每3分钟提醒我运动，一共发送三次就可以",
            now=now,
        )

        assert parsed["name"] == "提醒：运动"
        assert parsed["message"] == "提醒我运动"
        assert parsed["schedule_type"] == "every"
        assert parsed["schedule_value"] == "3m"
        assert parsed["delete_after_run"] is False
        assert parsed["max_runs"] == 3

    def test_parse_natural_language_rejects_unsupported_request(self):
        """Test unsupported natural-language requests fail clearly."""
        now = datetime(2026, 4, 11, 23, 21, 0, tzinfo=timezone(timedelta(hours=8)))

        with pytest.raises(ValueError) as exc_info:
            self._parse_natural_language_add_request("有空的时候提醒我喝水", now=now)

        assert "Unsupported natural-language schedule request" in str(exc_info.value)


@pytest.mark.asyncio
async def test_cron_tool_add_accepts_raw_natural_language_request(monkeypatch):
    """Test cron_tool(add) can consume a raw reminder request without explicit schedule fields."""
    from aworld.core.scheduler.types import CronJobState
    import aworld.tools.cron_tool as cron_tool_module

    class FakeScheduler:
        def __init__(self):
            self.last_job = None

        async def add_job(self, job):
            self.last_job = job
            job.state = CronJobState(next_run_at="2026-04-11T23:22:00+08:00")
            return job

    fake_scheduler = FakeScheduler()

    monkeypatch.setattr(
        cron_tool_module,
        "_parse_natural_language_add_request",
        lambda request, now=None: {
            "name": "提醒：喝水",
            "message": "提醒我喝水",
            "schedule_type": "at",
            "schedule_value": "2026-04-11T23:22:00+08:00",
            "delete_after_run": True,
        },
    )
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: fake_scheduler)

    result = await cron_tool_module.cron_tool(action="add", request="一分钟后提醒我喝水")

    assert result["success"] is True
    assert fake_scheduler.last_job is not None
    assert fake_scheduler.last_job.name == "提醒：喝水"
    assert fake_scheduler.last_job.payload.message == "提醒我喝水"
    assert fake_scheduler.last_job.schedule.kind == "at"


@pytest.mark.asyncio
async def test_cron_tool_normalizes_string_max_runs_and_aworld_agent_name(monkeypatch):
    """String max_runs and lowercase aworld agent name should be normalized before persistence."""
    from aworld.core.scheduler.types import CronJobState
    import aworld.tools.cron_tool as cron_tool_module

    class FakeScheduler:
        def __init__(self):
            self.last_job = None

        async def add_job(self, job):
            self.last_job = job
            job.state = CronJobState(next_run_at="2026-04-12T10:32:00+00:00")
            return job

    fake_scheduler = FakeScheduler()
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: fake_scheduler)

    result = await cron_tool_module.cron_tool(
        action="add",
        name="运动通知",
        message="提醒用户进行运动",
        schedule_type="every",
        schedule_value="3m",
        agent_name="aworld",
        max_runs="3",
        delete_after_run=False,
    )

    assert result["success"] is True
    assert result["next_run"] == "2026-04-12T10:32:00+00:00"
    assert fake_scheduler.last_job is not None
    assert fake_scheduler.last_job.payload.max_runs == 3
    assert fake_scheduler.last_job.payload.agent_name == "Aworld"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
