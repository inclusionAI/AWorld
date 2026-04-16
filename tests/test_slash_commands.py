"""
Test suite for Phase 3 slash command system.

Tests command registration, routing, and execution.
"""
import asyncio
import os
import sys
import pytest
from io import StringIO
from pathlib import Path
from prompt_toolkit.document import Document
from rich.console import Console as RichConsole

# Add aworld-cli to path
sys.path.insert(0, str(Path(__file__).parent.parent / "aworld-cli" / "src"))

from aworld_cli.core.command_system import CommandRegistry, CommandContext
from aworld_cli.commands import help_cmd, commit, review, diff, cron_cmd
from aworld_cli.console import AWorldCLI


class TestCommandRegistration:
    """Test command registration system."""

    def test_commands_registered(self):
        """Verify all commands are registered."""
        expected_commands = ['help', 'commit', 'review', 'diff', 'cron']
        for cmd_name in expected_commands:
            cmd = CommandRegistry.get(cmd_name)
            assert cmd is not None, f"Command /{cmd_name} not registered"
            assert cmd.name == cmd_name

    def test_command_types(self):
        """Verify command types are correct."""
        # Tool command
        help_cmd = CommandRegistry.get('help')
        assert help_cmd.command_type == 'tool'

        # Prompt commands
        for cmd_name in ['commit', 'review', 'diff']:
            cmd = CommandRegistry.get(cmd_name)
            assert cmd.command_type == 'prompt', f"/{cmd_name} should be prompt command"

        cron_cmd = CommandRegistry.get('cron')
        assert cron_cmd.command_type == 'tool'

    def test_list_commands(self):
        """Test listing all registered commands."""
        commands = CommandRegistry.list_commands()
        assert len(commands) >= 4  # At least our 4 commands
        command_names = [cmd.name for cmd in commands]
        assert 'help' in command_names
        assert 'commit' in command_names
        assert 'review' in command_names
        assert 'diff' in command_names
        assert 'cron' in command_names


class TestHelpCommand:
    """Test /help command (tool command)."""

    @pytest.mark.asyncio
    async def test_help_command_execution(self):
        """Test /help command executes and returns help text."""
        cmd = CommandRegistry.get('help')
        context = CommandContext(cwd=os.getcwd(), user_args='')

        result = await cmd.execute(context)

        assert result is not None
        assert 'Available commands:' in result
        assert '/help' in result
        assert '/commit' in result
        assert '/review' in result
        assert '/diff' in result

    @pytest.mark.asyncio
    async def test_help_command_with_args(self):
        """Test /help command ignores arguments."""
        cmd = CommandRegistry.get('help')
        context = CommandContext(cwd=os.getcwd(), user_args='some args')

        result = await cmd.execute(context)

        # Should still return help text even with args
        assert 'Available commands:' in result


class TestCommitCommand:
    """Test /commit command (prompt command)."""

    @pytest.mark.asyncio
    async def test_commit_pre_execute_validation(self, tmp_path):
        """Test /commit validates git repository."""
        cmd = CommandRegistry.get('commit')

        # Non-git directory should fail validation
        context = CommandContext(cwd=str(tmp_path), user_args='')
        error = await cmd.pre_execute(context)

        assert error is not None
        assert 'git repository' in error.lower()

    @pytest.mark.asyncio
    async def test_commit_prompt_generation(self):
        """Test /commit generates appropriate prompt."""
        cmd = CommandRegistry.get('commit')
        context = CommandContext(cwd=os.getcwd(), user_args='')

        # Skip if not in git repo
        error = await cmd.pre_execute(context)
        if error:
            pytest.skip("Not in git repository")

        prompt = await cmd.get_prompt(context)

        assert prompt is not None
        assert 'Git Commit Task' in prompt
        assert 'CRITICAL RULES' in prompt
        assert 'HEREDOC' in prompt

    def test_commit_allowed_tools(self):
        """Test /commit specifies correct allowed tools."""
        cmd = CommandRegistry.get('commit')

        allowed_tools = cmd.allowed_tools
        assert 'terminal__mcp_execute_command' in allowed_tools
        assert 'git_status' in allowed_tools
        assert 'git_diff' in allowed_tools
        assert 'git_commit' in allowed_tools


class TestReviewCommand:
    """Test /review command (prompt command)."""

    @pytest.mark.asyncio
    async def test_review_pre_execute_validation(self, tmp_path):
        """Test /review validates git repository."""
        cmd = CommandRegistry.get('review')

        # Non-git directory should fail validation
        context = CommandContext(cwd=str(tmp_path), user_args='')
        error = await cmd.pre_execute(context)

        assert error is not None
        assert 'git repository' in error.lower()

    @pytest.mark.asyncio
    async def test_review_prompt_generation(self):
        """Test /review generates appropriate prompt."""
        cmd = CommandRegistry.get('review')
        context = CommandContext(cwd=os.getcwd(), user_args='')

        # Skip if not in git repo
        error = await cmd.pre_execute(context)
        if error:
            pytest.skip("Not in git repository")

        prompt = await cmd.get_prompt(context)

        assert prompt is not None
        assert 'Code Review Task' in prompt
        assert 'Review Checklist' in prompt
        assert 'Code Quality' in prompt

    def test_review_allowed_tools(self):
        """Test /review specifies correct allowed tools."""
        cmd = CommandRegistry.get('review')

        allowed_tools = cmd.allowed_tools
        assert 'git_diff' in allowed_tools
        assert 'CAST_ANALYSIS' in allowed_tools
        assert 'filesystem__read_file' in allowed_tools


class TestDiffCommand:
    """Test /diff command (prompt command)."""

    @pytest.mark.asyncio
    async def test_diff_pre_execute_validation(self, tmp_path):
        """Test /diff validates git repository."""
        cmd = CommandRegistry.get('diff')

        # Non-git directory should fail validation
        context = CommandContext(cwd=str(tmp_path), user_args='')
        error = await cmd.pre_execute(context)

        assert error is not None
        assert 'git repository' in error.lower()

    @pytest.mark.asyncio
    async def test_diff_prompt_with_default_ref(self):
        """Test /diff generates prompt with default HEAD ref."""
        cmd = CommandRegistry.get('diff')
        context = CommandContext(cwd=os.getcwd(), user_args='')

        # Skip if not in git repo
        error = await cmd.pre_execute(context)
        if error:
            pytest.skip("Not in git repository")

        prompt = await cmd.get_prompt(context)

        assert prompt is not None
        assert 'Diff Summary Task' in prompt
        assert 'HEAD' in prompt  # Default ref

    @pytest.mark.asyncio
    async def test_diff_prompt_with_custom_ref(self):
        """Test /diff generates prompt with custom ref."""
        cmd = CommandRegistry.get('diff')
        context = CommandContext(cwd=os.getcwd(), user_args='main')

        # Skip if not in git repo
        error = await cmd.pre_execute(context)
        if error:
            pytest.skip("Not in git repository")

        prompt = await cmd.get_prompt(context)

        assert prompt is not None
        assert 'main' in prompt  # Custom ref

    def test_diff_allowed_tools(self):
        """Test /diff specifies correct allowed tools."""
        cmd = CommandRegistry.get('diff')

        allowed_tools = cmd.allowed_tools
        assert 'git_diff' in allowed_tools
        assert 'git_status' in allowed_tools


class TestCronCommand:
    """Test /cron command direct execution."""

    @pytest.mark.asyncio
    async def test_cron_status_executes_tool_directly(self, monkeypatch):
        """Test /cron status bypasses the agent and calls cron_tool directly."""
        cmd = CommandRegistry.get('cron')
        calls = []

        async def fake_cron_tool(**kwargs):
            calls.append(kwargs)
            return {
                "success": True,
                "scheduler_running": False,
                "total_jobs": 0,
                "enabled_jobs": 0,
            }

        monkeypatch.setattr("aworld_cli.commands.cron_cmd.cron_tool", fake_cron_tool)

        context = CommandContext(cwd=os.getcwd(), user_args='status')
        result = await cmd.execute(context)

        assert calls == [{"action": "status"}]
        assert "scheduler_running" in result
        assert "False" in result

    @pytest.mark.asyncio
    async def test_cron_show_executes_tool_directly(self, monkeypatch):
        """Test /cron show <job_id> calls cron_tool(show)."""
        cmd = CommandRegistry.get('cron')
        calls = []

        async def fake_cron_tool(**kwargs):
            calls.append(kwargs)
            return {
                "success": True,
                "job": {
                    "id": "job-123",
                    "name": "运动通知",
                    "schedule": "every 3m",
                    "enabled": True,
                    "next_run": "2026-04-13T10:00:00+00:00",
                    "last_run": None,
                    "last_status": None,
                    "last_error": None,
                    "max_runs": 3,
                    "run_count": 1,
                    "last_result_summary": "BTC 当前价格 68000 USDT",
                },
            }

        monkeypatch.setattr("aworld_cli.commands.cron_cmd.cron_tool", fake_cron_tool)

        context = CommandContext(cwd=os.getcwd(), user_args='show job-123')
        result = await cmd.execute(context)

        assert calls == [{"action": "show", "job_id": "job-123"}]
        assert "job-123" in result
        assert "运动通知" in result
        assert "run_count" in result
        assert "last_result_summary" in result
        assert "BTC 当前价格 68000 USDT" in result

    @pytest.mark.asyncio
    async def test_cron_show_follows_live_logs_for_running_job(self, monkeypatch):
        """Running jobs should switch `/cron show` into live follow mode."""
        from aworld_cli.runtime.cron_notifications import CronNotificationCenter

        cmd = CommandRegistry.get('cron')
        call_count = 0

        async def fake_cron_tool(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return {
                    "success": True,
                    "job": {
                        "id": "job-live",
                        "name": "爬取 X",
                        "schedule": "at 2026-04-15T17:37:22+08:00",
                        "enabled": True,
                        "running": True,
                        "next_run": None,
                        "last_run": "2026-04-15T09:37:22.005051+00:00",
                        "last_status": None,
                        "last_error": None,
                        "max_runs": None,
                        "run_count": 0,
                        "last_result_summary": None,
                    },
                }
            return {
                "success": True,
                "job": {
                    "id": "job-live",
                    "name": "爬取 X",
                    "schedule": "at 2026-04-15T17:37:22+08:00",
                    "enabled": True,
                    "running": False,
                    "next_run": None,
                    "last_run": "2026-04-15T09:37:22.005051+00:00",
                    "last_status": "ok",
                    "last_error": None,
                    "max_runs": None,
                    "run_count": 1,
                    "last_result_summary": "已保存 10 条内容",
                },
            }

        monkeypatch.setattr("aworld_cli.commands.cron_cmd.cron_tool", fake_cron_tool)

        class FakeRuntime:
            def __init__(self):
                buffer = StringIO()
                self._buffer = buffer
                self.cli = type("FakeCli", (), {"console": RichConsole(file=buffer, force_terminal=False, color_system=None)})()
                self._notification_center = CronNotificationCenter()

            def _get_cron_progress_logs(self, job_id):
                return self._notification_center.get_progress_logs(job_id)

        runtime = FakeRuntime()
        await runtime._notification_center.publish_progress({
            "job_id": "job-live",
            "job_name": "爬取 X",
            "level": "info",
            "message": "开始第 1/4 次执行",
        })
        await runtime._notification_center.publish_progress({
            "job_id": "job-live",
            "job_name": "爬取 X",
            "level": "success",
            "message": "最终回答：\n已保存 10 条内容\n输出文件：twitter_latest_10_posts.md",
        })
        await runtime._notification_center.publish_progress({
            "job_id": "job-live",
            "job_name": "爬取 X",
            "level": "success",
            "message": "任务执行完成：已保存 10 条内容",
            "terminal": True,
        })

        context = CommandContext(cwd=os.getcwd(), user_args="show job-live", runtime=runtime)
        result = await cmd.execute(context)

        assert result == ""
        output = runtime._buffer.getvalue()
        assert "跟踪定时任务执行" in output
        assert "开始第 1/4 次执行" in output
        assert "最终回答：" in output
        assert "输出文件：twitter_latest_10_posts.md" in output
        assert "任务执行完成：已保存 10 条内容" in output
        assert "定时任务执行结束" in output

    @pytest.mark.asyncio
    async def test_cron_disable_all_executes_tool_directly(self, monkeypatch):
        """Test /cron disable all calls cron_tool(disable, job_id=all)."""
        cmd = CommandRegistry.get('cron')
        calls = []

        async def fake_cron_tool(**kwargs):
            calls.append(kwargs)
            return {"success": True, "message": "Disabled 2 active jobs"}

        monkeypatch.setattr("aworld_cli.commands.cron_cmd.cron_tool", fake_cron_tool)

        context = CommandContext(cwd=os.getcwd(), user_args='disable all')
        result = await cmd.execute(context)

        assert calls == [{"action": "disable", "job_id": "all"}]
        assert "Disabled 2 active jobs" in result

    @pytest.mark.asyncio
    async def test_cron_enable_all_executes_tool_directly(self, monkeypatch):
        """Test /cron enable all calls cron_tool(enable, job_id=all)."""
        cmd = CommandRegistry.get('cron')
        calls = []

        async def fake_cron_tool(**kwargs):
            calls.append(kwargs)
            return {"success": True, "message": "Enabled 2 reactivatable jobs"}

        monkeypatch.setattr("aworld_cli.commands.cron_cmd.cron_tool", fake_cron_tool)

        context = CommandContext(cwd=os.getcwd(), user_args='enable all')
        result = await cmd.execute(context)

        assert calls == [{"action": "enable", "job_id": "all"}]
        assert "Enabled 2 reactivatable jobs" in result

    @pytest.mark.asyncio
    async def test_cron_remove_all_executes_tool_directly(self, monkeypatch):
        """Test /cron remove all calls cron_tool(remove, job_id=all)."""
        cmd = CommandRegistry.get('cron')
        calls = []

        async def fake_cron_tool(**kwargs):
            calls.append(kwargs)
            return {"success": True, "message": "Removed 3 visible jobs"}

        monkeypatch.setattr("aworld_cli.commands.cron_cmd.cron_tool", fake_cron_tool)

        context = CommandContext(cwd=os.getcwd(), user_args='remove all')
        result = await cmd.execute(context)

        assert calls == [{"action": "remove", "job_id": "all"}]
        assert "Removed 3 visible jobs" in result

    @pytest.mark.asyncio
    async def test_cron_inbox_reads_notifications_from_runtime(self):
        """Test /cron inbox drains unread notifications from runtime."""
        from aworld_cli.runtime.cron_notifications import CronNotificationCenter

        cmd = CommandRegistry.get('cron')

        class FakeRuntime:
            def __init__(self):
                self._notification_center = CronNotificationCenter()

            async def _drain_notifications(self):
                return await self._notification_center.drain()

        runtime = FakeRuntime()
        await runtime._notification_center.publish({
            "job_id": "job-1",
            "job_name": "喝水提醒",
            "status": "ok",
            "summary": 'Cron task "喝水提醒" completed',
            "detail": "提醒我喝水",
        })

        context = CommandContext(cwd=os.getcwd(), user_args='inbox', runtime=runtime)
        result = await cmd.execute(context)

        assert "未读通知" in result
        assert "喝水提醒" in result
        assert "提醒我喝水" in result
        assert runtime._notification_center.get_unread_count() == 0

    @pytest.mark.asyncio
    async def test_cron_inbox_formats_multiline_result_detail(self):
        """Multiline result detail should remain readable in inbox output."""
        from aworld_cli.runtime.cron_notifications import CronNotificationCenter

        cmd = CommandRegistry.get('cron')

        class FakeRuntime:
            def __init__(self):
                self._notification_center = CronNotificationCenter()

            async def _drain_notifications(self):
                return await self._notification_center.drain()

        runtime = FakeRuntime()
        await runtime._notification_center.publish({
            "job_id": "job-1",
            "job_name": "twitter_scraper_200_posts",
            "status": "ok",
            "summary": 'Cron task "twitter_scraper_200_posts" completed',
            "detail": "最终回答：\n已保存 200 条内容\n输出文件：twitter_for_you_posts_200.md",
        })

        context = CommandContext(cwd=os.getcwd(), user_args='inbox', runtime=runtime)
        result = await cmd.execute(context)

        assert "content: 最终回答：" in result
        assert "已保存 200 条内容" in result
        assert "输出文件：twitter_for_you_posts_200.md" in result

    @pytest.mark.asyncio
    async def test_cron_inbox_reads_only_notifications_for_requested_job(self):
        """Test /cron inbox <job_id> drains only matching unread notifications."""
        from aworld_cli.runtime.cron_notifications import CronNotificationCenter

        cmd = CommandRegistry.get('cron')

        class FakeRuntime:
            def __init__(self):
                self._notification_center = CronNotificationCenter()

            async def _drain_notifications(self, job_id=None):
                return await self._notification_center.drain(job_id=job_id)

        runtime = FakeRuntime()
        await runtime._notification_center.publish({
            "job_id": "job-1",
            "job_name": "喝水提醒",
            "status": "ok",
            "summary": 'Cron task "喝水提醒" completed',
            "detail": "提醒我喝水",
        })
        await runtime._notification_center.publish({
            "job_id": "job-2",
            "job_name": "运动提醒",
            "status": "ok",
            "summary": 'Cron task "运动提醒" completed',
            "detail": "起来活动一下",
        })

        context = CommandContext(cwd=os.getcwd(), user_args='inbox job-1', runtime=runtime)
        result = await cmd.execute(context)

        assert "喝水提醒" in result
        assert "提醒我喝水" in result
        assert "运动提醒" not in result
        assert runtime._notification_center.get_unread_count() == 1

        remaining = await runtime._notification_center.drain()
        assert len(remaining) == 1
        assert remaining[0].job_id == "job-2"


class TestSlashCommandCompletion:
    """Test slash command completion sources."""

    def test_console_completion_entries_include_cron_subcommands(self):
        """Typing /cron should expose concrete cron subcommands in the completer source."""
        cli = AWorldCLI()

        words, meta = cli._build_completion_entries(agent_names=[])

        assert "/cron" in words
        assert "/cron add" in words
        assert "/cron list" in words
        assert "/cron show" in words
        assert "/cron inbox" in words
        assert meta["/cron show"] == "查看单个任务详情"
        assert meta["/cron inbox"] == "查看并清空未读提醒通知"

    def test_console_completer_suggests_job_ids_for_cron_show(self):
        """Typing /cron show should suggest live cron job IDs."""
        cli = AWorldCLI()

        class FakeJob:
            def __init__(self, job_id, name, enabled=True, last_status=None):
                self.id = job_id
                self.name = name
                self.enabled = enabled
                self.state = type(
                    "State",
                    (),
                    {"last_status": last_status},
                )()

        class FakeScheduler:
            async def list_jobs(self, enabled_only=False):
                return [
                    FakeJob("job-123", "喝水提醒", enabled=True, last_status="ok"),
                    FakeJob("job-456", "运动提醒", enabled=False, last_status="error"),
                    FakeJob("job-789", "拉伸提醒", enabled=True, last_status="error"),
                    FakeJob("job-999", "散步提醒", enabled=True, last_status=None),
                ]

        class FakeRuntime:
            def __init__(self):
                self._scheduler = FakeScheduler()

        completer = cli._build_session_completer(
            agent_names=[],
            runtime=FakeRuntime(),
            event_loop=None,
        )

        completions = list(completer.get_completions(Document("/cron show "), None))
        completion_texts = [item.text for item in completions]
        completion_meta = {item.text: item.display_meta_text for item in completions}

        assert completion_texts == ["job-789", "job-123", "job-999", "job-456"]
        assert "job-123" in completion_texts
        assert "job-456" in completion_texts
        assert "job-789" in completion_texts
        assert "job-999" in completion_texts
        assert completion_meta["job-789"] == "Name: 拉伸提醒 | State: Enabled | Last: Error"
        assert completion_meta["job-123"] == "Name: 喝水提醒 | State: Enabled | Last: OK"
        assert completion_meta["job-999"] == "Name: 散步提醒 | State: Enabled | Last: Never"
        assert completion_meta["job-456"] == "Name: 运动提醒 | State: Disabled | Last: Error"


class TestCommandContext:
    """Test CommandContext dataclass."""

    def test_context_creation(self):
        """Test creating CommandContext."""
        context = CommandContext(
            cwd='/tmp',
            user_args='test args',
            sandbox=None,
            agent_config=None
        )

        assert context.cwd == '/tmp'
        assert context.user_args == 'test args'
        assert context.sandbox is None
        assert context.agent_config is None

    def test_context_defaults(self):
        """Test CommandContext default values."""
        context = CommandContext(cwd='/tmp', user_args='')

        assert context.sandbox is None
        assert context.agent_config is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
