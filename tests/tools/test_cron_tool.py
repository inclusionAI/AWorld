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

    def test_parse_natural_language_relative_reminder_with_leading_prefix_in_chinese(self):
        """Test '提醒我X分钟后Y' phrasing is parsed to a one-shot schedule."""
        now = datetime(2026, 4, 11, 23, 21, 0, tzinfo=timezone(timedelta(hours=8)))

        parsed = self._parse_natural_language_add_request("提醒我两分钟后喝水", now=now)

        assert parsed["name"] == "提醒：喝水"
        assert parsed["message"] == "提醒我喝水"
        assert parsed["schedule_type"] == "at"
        assert parsed["schedule_value"] == "2026-04-11T23:23:00+08:00"
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

    def test_parse_natural_language_next_weekday_reminder_in_chinese(self):
        """Test next-week weekday reminders resolve to the correct calendar date."""
        now = datetime(2026, 4, 15, 16, 33, 0, tzinfo=timezone(timedelta(hours=8)))

        parsed = self._parse_natural_language_add_request("下周三中午12点提醒我开会", now=now)

        assert parsed["name"] == "提醒：开会"
        assert parsed["message"] == "提醒我开会"
        assert parsed["schedule_type"] == "at"
        assert parsed["schedule_value"] == "2026-04-22T12:00:00+08:00"
        assert parsed["delete_after_run"] is True

    def test_parse_natural_language_next_weekday_suffix_reminder_in_chinese(self):
        """Test trailing '...的提醒' phrasing also resolves correctly."""
        now = datetime(2026, 4, 15, 16, 33, 0, tzinfo=timezone(timedelta(hours=8)))

        parsed = self._parse_natural_language_add_request("下周三，中午12点开会的提醒", now=now)

        assert parsed["name"] == "提醒：开会"
        assert parsed["message"] == "提醒我开会"
        assert parsed["schedule_type"] == "at"
        assert parsed["schedule_value"] == "2026-04-22T12:00:00+08:00"
        assert parsed["delete_after_run"] is True

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
async def test_cron_tool_add_prefers_request_derived_schedule_over_llm_supplied_absolute_time(monkeypatch):
    """Raw request should be authoritative when LLM also supplies a stale absolute schedule."""
    from aworld.core.scheduler.types import CronJobState
    import aworld.tools.cron_tool as cron_tool_module

    class FakeScheduler:
        def __init__(self):
            self.last_job = None

        async def add_job(self, job):
            self.last_job = job
            job.state = CronJobState(next_run_at=job.schedule.at)
            return job

    fake_scheduler = FakeScheduler()

    monkeypatch.setattr(
        cron_tool_module,
        "_parse_natural_language_add_request",
        lambda request, now=None: {
            "name": "提醒：上厕所",
            "message": "提醒我上厕所",
            "schedule_type": "at",
            "schedule_value": "2026-04-14T16:25:02+08:00",
            "delete_after_run": True,
        },
    )
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: fake_scheduler)

    result = await cron_tool_module.cron_tool(
        action="add",
        request="两分钟后提醒我上厕所",
        name="上厕所提醒",
        message="提醒：该去上厕所了！",
        schedule_type="at",
        schedule_value="2026-04-14T16:07:45+08:00",
    )

    assert result["success"] is True
    assert fake_scheduler.last_job is not None
    assert fake_scheduler.last_job.name == "上厕所提醒"
    assert fake_scheduler.last_job.payload.message == "提醒：该去上厕所了！"
    assert fake_scheduler.last_job.schedule.kind == "at"
    assert fake_scheduler.last_job.schedule.at == "2026-04-14T16:25:02+08:00"
    assert result["next_run"] == "2026-04-14T16:25:02+08:00"


@pytest.mark.asyncio
async def test_cron_tool_add_creates_default_advance_reminder_for_future_calendar_reminder(monkeypatch):
    """Future point-in-time reminders should get an extra default pre-reminder."""
    from aworld.core.scheduler.types import CronJobState
    import aworld.tools.cron_tool as cron_tool_module

    class FakeScheduler:
        def __init__(self):
            self.jobs = []

        async def add_job(self, job):
            self.jobs.append(job)
            job.state = CronJobState(next_run_at=job.schedule.at)
            return job

    fake_scheduler = FakeScheduler()

    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: fake_scheduler)
    monkeypatch.setattr(
        cron_tool_module,
        "_resolve_schedule_now",
        lambda now=None: now or datetime(2026, 4, 15, 10, 0, 0, tzinfo=timezone(timedelta(hours=8))),
    )
    monkeypatch.setattr(
        cron_tool_module,
        "_parse_natural_language_add_request",
        lambda request, now=None: {
            "name": "提醒：开会",
            "message": "提醒我开会",
            "schedule_type": "at",
            "schedule_value": "2026-04-22T12:00:00+08:00",
            "delete_after_run": True,
        },
    )

    result = await cron_tool_module.cron_tool(
        action="add",
        request="下周三中午12点提醒我开会",
    )

    assert result["success"] is True
    assert len(fake_scheduler.jobs) == 2
    assert fake_scheduler.jobs[0].schedule.at == "2026-04-22T12:00:00+08:00"
    assert fake_scheduler.jobs[1].name == "提醒：开会（提前10分钟）"
    assert fake_scheduler.jobs[1].payload.message == "提醒我开会，还有10分钟"
    assert fake_scheduler.jobs[1].schedule.at == "2026-04-22T11:50:00+08:00"
    assert result["next_run_display"] == "2026年4月22日（星期三）12:00"
    assert result["advance_reminder"]["display"] == "2026年4月22日（星期三）11:50"
    assert result["advance_reminder"]["next_run"] == "2026-04-22T11:50:00+08:00"
    assert result["advance_reminder"]["lead_minutes"] == 10


@pytest.mark.asyncio
async def test_cron_tool_add_creates_default_advance_reminder_for_daily_reminder(monkeypatch):
    """Daily fixed-time reminders should also get a shifted pre-reminder cron."""
    from aworld.core.scheduler.types import CronJobState
    import aworld.tools.cron_tool as cron_tool_module

    class FakeScheduler:
        def __init__(self):
            self.jobs = []

        async def add_job(self, job):
            self.jobs.append(job)
            next_run = job.schedule.at
            if job.schedule.kind == "cron":
                next_run = job.schedule.cron_expr
            job.state = CronJobState(next_run_at=next_run)
            return job

    fake_scheduler = FakeScheduler()

    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: fake_scheduler)

    result = await cron_tool_module.cron_tool(
        action="add",
        request="每天早上9点提醒我运行测试",
    )

    assert result["success"] is True
    assert len(fake_scheduler.jobs) == 2
    assert fake_scheduler.jobs[0].schedule.cron_expr == "0 9 * * *"
    assert fake_scheduler.jobs[1].name == "提醒：运行测试（提前10分钟）"
    assert fake_scheduler.jobs[1].payload.message == "提醒我运行测试，还有10分钟"
    assert fake_scheduler.jobs[1].schedule.cron_expr == "50 8 * * *"
    assert result["advance_reminder"]["next_run"] == "50 8 * * *"
    assert result["advance_reminder"]["lead_minutes"] == 10


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


@pytest.mark.asyncio
async def test_cron_tool_add_binds_job_to_runtime_default_agent(monkeypatch):
    """Cron add should bind the persisted job to the CLI-selected root agent."""
    from aworld.core.scheduler.types import CronJobState
    import aworld.tools.cron_tool as cron_tool_module

    class FakeExecutor:
        def get_default_agent_name(self):
            return "Aworld"

    class FakeScheduler:
        def __init__(self):
            self.last_job = None
            self.executor = FakeExecutor()

        async def add_job(self, job):
            self.last_job = job
            job.state = CronJobState(next_run_at="2026-04-16T10:32:00+00:00")
            return job

    fake_scheduler = FakeScheduler()
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: fake_scheduler)

    result = await cron_tool_module.cron_tool(
        action="add",
        name="爬取X最新10条内容",
        message="参考当前目录下twitter_scraper_skill.md skill爬取x上面的内容，存到当前目录。注意爬取最新的10条就行",
        schedule_type="at",
        schedule_value="2026-04-16T18:32:00+08:00",
        agent_name="default",
        tools=["bash", "CAST_SEARCH"],
        delete_after_run=True,
    )

    assert result["success"] is True
    assert fake_scheduler.last_job is not None
    assert fake_scheduler.last_job.payload.agent_name == "Aworld"
    assert fake_scheduler.last_job.payload.tool_names == []


@pytest.mark.asyncio
async def test_cron_tool_add_splits_comma_delimited_tools_for_non_aworld_agent(monkeypatch):
    """Non-Aworld cron jobs should still normalize comma-delimited tool strings."""
    from aworld.core.scheduler.types import CronJobState
    import aworld.tools.cron_tool as cron_tool_module

    class FakeScheduler:
        def __init__(self):
            self.last_job = None

        async def add_job(self, job):
            self.last_job = job
            job.state = CronJobState(next_run_at="2026-04-16T10:32:00+00:00")
            return job

    fake_scheduler = FakeScheduler()
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: fake_scheduler)

    result = await cron_tool_module.cron_tool(
        action="add",
        name="special-agent-task",
        message="run with specific tools",
        schedule_type="at",
        schedule_value="2026-04-16T18:32:00+08:00",
        agent_name="SpecialAgent",
        tools="CAST_SEARCH,bash,SKILL",
        delete_after_run=True,
    )

    assert result["success"] is True
    assert fake_scheduler.last_job is not None
    assert fake_scheduler.last_job.payload.tool_names == ["CAST_SEARCH", "bash", "SKILL"]


@pytest.mark.asyncio
async def test_cron_tool_add_prefers_request_derived_schedule_for_leading_relative_phrase(monkeypatch):
    """Leading '提醒我X分钟后Y' requests should override stale LLM absolute timestamps."""
    from aworld.core.scheduler.types import CronJobState
    import aworld.tools.cron_tool as cron_tool_module

    class FakeScheduler:
        def __init__(self):
            self.last_job = None

        async def add_job(self, job):
            self.last_job = job
            job.state = CronJobState(next_run_at=job.schedule.at)
            return job

    fake_scheduler = FakeScheduler()
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: fake_scheduler)
    monkeypatch.setattr(
        cron_tool_module,
        "_parse_natural_language_add_request",
        lambda request, now=None: {
            "name": "喝水提醒",
            "message": "提醒用户喝水",
            "schedule_type": "at",
            "schedule_value": "2026-04-14T17:17:00+08:00",
            "delete_after_run": True,
        },
    )

    result = await cron_tool_module.cron_tool(
        action="add",
        request="提醒我两分钟后喝水",
        name="喝水提醒",
        message="提醒用户喝水",
        schedule_type="at",
        schedule_value="2026-04-14T16:50:01+08:00",
    )

    assert result["success"] is True
    assert fake_scheduler.last_job is not None
    assert fake_scheduler.last_job.schedule.at == "2026-04-14T17:17:00+08:00"
    assert result["next_run"] == "2026-04-14T17:17:00+08:00"


@pytest.mark.asyncio
async def test_cron_tool_add_rejects_past_one_time_schedule(monkeypatch):
    """Stale absolute one-time timestamps should fail closed instead of creating a dead job."""
    import aworld.tools.cron_tool as cron_tool_module

    class FakeScheduler:
        def __init__(self):
            self.add_called = False

        async def add_job(self, job):
            self.add_called = True
            return job

    fake_scheduler = FakeScheduler()
    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: fake_scheduler)

    result = await cron_tool_module.cron_tool(
        action="add",
        name="喝水提醒",
        message="提醒用户喝水",
        schedule_type="at",
        schedule_value="2026-04-14T16:50:01+08:00",
    )

    assert result["success"] is False
    assert "already in the past" in result["error"]
    assert fake_scheduler.add_called is False


@pytest.mark.asyncio
async def test_cron_tool_disable_all_only_targets_enabled_jobs(monkeypatch):
    """disable all should only disable currently enabled jobs."""
    import aworld.tools.cron_tool as cron_tool_module
    from aworld.core.scheduler.types import CronJob, CronJobState, CronPayload, CronSchedule

    updated_ids = []

    class FakeScheduler:
        async def list_jobs(self, enabled_only=False):
            return [
                CronJob(
                    id="job-enabled-1",
                    name="活跃提醒1",
                    enabled=True,
                    schedule=CronSchedule(kind="every", every_seconds=60),
                    payload=CronPayload(message="test"),
                    state=CronJobState(next_run_at="2026-04-13T10:00:00+00:00"),
                ),
                CronJob(
                    id="job-enabled-2",
                    name="活跃提醒2",
                    enabled=True,
                    schedule=CronSchedule(kind="cron", cron_expr="0 9 * * *"),
                    payload=CronPayload(message="test"),
                    state=CronJobState(next_run_at="2026-04-14T01:00:00+00:00"),
                ),
                CronJob(
                    id="job-disabled",
                    name="已禁用提醒",
                    enabled=False,
                    schedule=CronSchedule(kind="every", every_seconds=60),
                    payload=CronPayload(message="test"),
                    state=CronJobState(next_run_at=None),
                ),
            ]

        async def update_job(self, job_id, **updates):
            updated_ids.append((job_id, updates))
            return {"id": job_id}

    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: FakeScheduler())

    result = await cron_tool_module.cron_tool(action="disable", job_id="all")

    assert result["success"] is True
    assert result["updated_count"] == 2
    assert [item[0] for item in updated_ids] == ["job-enabled-1", "job-enabled-2"]
    assert all(item[1]["enabled"] is False for item in updated_ids)


@pytest.mark.asyncio
async def test_cron_tool_enable_all_skips_expired_one_time_history(monkeypatch):
    """enable all should not resurrect expired one-time historical jobs."""
    import aworld.tools.cron_tool as cron_tool_module
    from aworld.core.scheduler.types import CronJob, CronJobState, CronPayload, CronSchedule

    updated_ids = []
    future_at = "2026-04-20T10:00:00+00:00"
    past_at = "2026-04-10T10:00:00+00:00"

    class FakeScheduler:
        async def list_jobs(self, enabled_only=False):
            return [
                CronJob(
                    id="job-recurring",
                    name="循环提醒",
                    enabled=False,
                    schedule=CronSchedule(kind="every", every_seconds=60),
                    payload=CronPayload(message="test"),
                    state=CronJobState(next_run_at=None),
                ),
                CronJob(
                    id="job-future-once",
                    name="未来一次性提醒",
                    enabled=False,
                    schedule=CronSchedule(kind="at", at=future_at),
                    payload=CronPayload(message="test"),
                    state=CronJobState(next_run_at=future_at),
                ),
                CronJob(
                    id="job-expired-once",
                    name="历史一次性提醒",
                    enabled=False,
                    schedule=CronSchedule(kind="at", at=past_at),
                    payload=CronPayload(message="test"),
                    state=CronJobState(next_run_at=None, last_run_at=past_at),
                ),
            ]

        async def update_job(self, job_id, **updates):
            updated_ids.append((job_id, updates))
            return {"id": job_id}

    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: FakeScheduler())

    result = await cron_tool_module.cron_tool(action="enable", job_id="all")

    assert result["success"] is True
    assert result["updated_count"] == 2
    assert [item[0] for item in updated_ids] == ["job-recurring", "job-future-once"]
    assert "job-expired-once" not in [item[0] for item in updated_ids]


@pytest.mark.asyncio
async def test_cron_tool_remove_all_targets_default_list_scope(monkeypatch):
    """remove all should delete every job visible in the default list view."""
    import aworld.tools.cron_tool as cron_tool_module
    from aworld.core.scheduler.types import CronJob, CronJobState, CronPayload, CronSchedule

    removed_ids = []

    class FakeScheduler:
        async def list_jobs(self, enabled_only=False):
            assert enabled_only is False
            return [
                CronJob(
                    id="job-enabled",
                    name="活跃提醒",
                    enabled=True,
                    schedule=CronSchedule(kind="every", every_seconds=60),
                    payload=CronPayload(message="test"),
                    state=CronJobState(next_run_at="2026-04-13T10:00:00+00:00"),
                ),
                CronJob(
                    id="job-disabled",
                    name="已禁用提醒",
                    enabled=False,
                    schedule=CronSchedule(kind="at", at="2026-04-14T10:00:00+00:00"),
                    payload=CronPayload(message="test"),
                    state=CronJobState(next_run_at=None),
                ),
            ]

        async def remove_job(self, job_id):
            removed_ids.append(job_id)
            return True

    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: FakeScheduler())

    result = await cron_tool_module.cron_tool(action="remove", job_id="all")

    assert result["success"] is True
    assert result["removed_count"] == 2
    assert result["job_ids"] == ["job-enabled", "job-disabled"]
    assert removed_ids == ["job-enabled", "job-disabled"]


@pytest.mark.asyncio
async def test_cron_tool_show_includes_last_result_summary(monkeypatch):
    """show should expose the persisted last execution summary."""
    import aworld.tools.cron_tool as cron_tool_module
    from aworld.core.scheduler.types import CronJob, CronJobState, CronPayload, CronSchedule

    class FakeScheduler:
        async def get_job(self, job_id):
            assert job_id == "job-123"
            return CronJob(
                id="job-123",
                name="BTC价格监控",
                enabled=True,
                schedule=CronSchedule(kind="every", every_seconds=60),
                payload=CronPayload(message="检查 BTC 当前价格"),
                state=CronJobState(
                    next_run_at="2026-04-13T06:23:26.376502+00:00",
                    last_run_at="2026-04-13T06:22:26.376502+00:00",
                    last_status="ok",
                    last_result_summary="BTC 当前价格 68000 USDT，较上一分钟上涨 0.2%",
                    run_count=3,
                ),
            )

    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: FakeScheduler())

    result = await cron_tool_module.cron_tool(action="show", job_id="job-123")

    assert result["success"] is True
    assert result["job"]["id"] == "job-123"
    assert result["job"]["last_result_summary"] == "BTC 当前价格 68000 USDT，较上一分钟上涨 0.2%"


@pytest.mark.asyncio
async def test_cron_tool_logs_traceback_for_unexpected_internal_errors(monkeypatch):
    """Unexpected internal errors should still log a traceback for diagnosis."""
    import aworld.tools.cron_tool as cron_tool_module

    logged_messages = []

    def fake_error(message, *args, **kwargs):
        logged_messages.append(str(message))

    monkeypatch.setattr("aworld.core.scheduler.get_scheduler", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(cron_tool_module.logger, "error", fake_error)

    result = await cron_tool_module.cron_tool(action="status")

    assert result["success"] is False
    assert result["error"] == "Internal error: boom"
    assert logged_messages
    assert "Cron tool error: boom" in logged_messages[0]
    assert "Traceback" in logged_messages[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
