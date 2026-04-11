# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Tests for cron_tool schedule parsing and validation.
"""
import pytest
from datetime import datetime


class TestParseScheduleValidation:
    """Test schedule input validation in _parse_schedule()."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Import _parse_schedule lazily to avoid tool initialization."""
        from aworld.tools.cron_tool import _parse_schedule
        self._parse_schedule = _parse_schedule

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
