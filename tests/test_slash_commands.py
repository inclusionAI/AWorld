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
from aworld.plugins.discovery import discover_plugins
from aworld_cli.plugin_capabilities.commands import register_plugin_commands
from aworld_cli.commands import help_cmd, commit, review, diff, cron_cmd, plugins_cmd, evaluation_cmd, optimize_cmd
from aworld_cli.console import AWorldCLI


class TestCommandRegistration:
    """Test command registration system."""

    def test_commands_registered(self):
        """Verify all commands are registered."""
        expected_commands = ['help', 'commit', 'review', 'diff', 'cron', 'plugins', 'evaluation', 'optimize']
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

        for cmd_name in ['cron', 'plugins', 'evaluation', 'optimize']:
            tool_cmd = CommandRegistry.get(cmd_name)
            assert tool_cmd.command_type == 'tool'

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
        assert 'plugins' in command_names
        assert 'evaluation' in command_names
        assert 'optimize' in command_names


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
        assert '/plugins' in result
        assert '/evaluation' in result
        assert '/optimize' in result

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


class TestEvaluationCommand:
    """Test /evaluation command direct execution."""

    @pytest.mark.asyncio
    async def test_evaluation_without_args_shows_usage(self):
        cmd = CommandRegistry.get("evaluation")

        result = await cmd.execute(CommandContext(cwd=os.getcwd(), user_args=""))

        assert "Usage:" in result
        assert "/evaluation --input" in result
        assert "--kind trajectory" in result

    @pytest.mark.asyncio
    async def test_evaluation_delegates_to_source_runtime(self, monkeypatch, tmp_path):
        cmd = CommandRegistry.get("evaluation")
        input_path = tmp_path / "trajectory.log"
        agent_path = tmp_path / "agent.md"
        calls = {}

        def fake_run_evaluator_source_cli(**kwargs):
            calls.update(kwargs)
            return {
                "suite_id": "trajectory-source-evaluator",
                "gate": {"status": "pass"},
                "summary": {"trajectory-source-evaluator": {"score": {"mean": 88.0}}},
                "results": [],
                "approval": {"required": False, "resolved": False, "approved": None},
                "report_path": str(tmp_path / "report.json"),
            }

        monkeypatch.setattr(
            "aworld_cli.commands.evaluation_cmd.run_evaluator_source_cli",
            fake_run_evaluator_source_cli,
        )

        result = await cmd.execute(
            CommandContext(
                cwd=os.getcwd(),
                user_args=(
                    f"--input {input_path} --kind trajectory "
                    f"--task-id task-1 --judge-agent {agent_path} --out-dir {tmp_path}"
                ),
            )
        )

        assert calls["input"] == str(input_path)
        assert calls["kind"] == "trajectory"
        assert calls["task_id"] == "task-1"
        assert calls["judge_agent"] == str(agent_path)
        assert calls["judge_agent_name"] is None
        assert calls["judge_backend_ref"] is None
        assert calls["out_dir"] == str(tmp_path)
        assert "trajectory-source-evaluator" in result
        assert "Report:" in result

    @pytest.mark.asyncio
    async def test_evaluation_accepts_judge_agent_name(self, monkeypatch, tmp_path):
        cmd = CommandRegistry.get("evaluation")
        input_path = tmp_path / "answers.jsonl"
        calls = {}

        def fake_run_evaluator_source_cli(**kwargs):
            calls.update(kwargs)
            return {
                "suite_id": "answer-source-evaluator",
                "gate": {"status": "pass"},
                "summary": {"answer-source-evaluator": {"score": {"mean": 88.0}}},
                "results": [],
                "approval": {"required": False, "resolved": False, "approved": None},
                "report_path": str(tmp_path / "report.json"),
            }

        monkeypatch.setattr(
            "aworld_cli.commands.evaluation_cmd.run_evaluator_source_cli",
            fake_run_evaluator_source_cli,
        )

        result = await cmd.execute(
            CommandContext(
                cwd=os.getcwd(),
                user_args=f"--input {input_path} --kind answer --judge-agent-name JudgeTeam",
            )
        )

        assert calls["judge_agent"] is None
        assert calls["judge_agent_name"] == "JudgeTeam"
        assert calls["judge_backend_ref"] is None
        assert "answer-source-evaluator" in result

    @pytest.mark.asyncio
    async def test_evaluation_accepts_judge_backend_ref(self, monkeypatch, tmp_path):
        cmd = CommandRegistry.get("evaluation")
        input_path = tmp_path / "answers.jsonl"
        calls = {}

        def fake_run_evaluator_source_cli(**kwargs):
            calls.update(kwargs)
            return {
                "suite_id": "answer-source-evaluator",
                "gate": {"status": "pass"},
                "summary": {"answer-source-evaluator": {"score": {"mean": 88.0}}},
                "results": [],
                "approval": {"required": False, "resolved": False, "approved": None},
                "report_path": str(tmp_path / "report.json"),
            }

        monkeypatch.setattr(
            "aworld_cli.commands.evaluation_cmd.run_evaluator_source_cli",
            fake_run_evaluator_source_cli,
        )

        result = await cmd.execute(
            CommandContext(
                cwd=os.getcwd(),
                user_args=f"--input {input_path} --kind answer --judge-backend-ref custom_judge:build_backend",
            )
        )

        assert calls["judge_agent"] is None
        assert calls["judge_agent_name"] is None
        assert calls["judge_backend_ref"] == "custom_judge:build_backend"
        assert "answer-source-evaluator" in result

    @pytest.mark.asyncio
    async def test_evaluation_runs_source_runtime_without_nested_event_loop(self, monkeypatch, tmp_path):
        cmd = CommandRegistry.get("evaluation")
        input_path = tmp_path / "answers.jsonl"
        input_path.write_text('{"id":"case-1","input":"question","answer":"answer"}\n', encoding="utf-8")
        agent_path = tmp_path / "agent.md"
        agent_path.write_text("---\nname: judge\n---\nJudge.\n", encoding="utf-8")

        async def fake_run_evaluation_flow(flow):
            return {
                "report_version": 1,
                "report_format": {"id": "aworld.evaluator.report", "version": 1},
                "generated_at": "2026-06-10T00:00:00Z",
                "suite_id": "answer-source-evaluator",
                "target": flow.target,
                "judge_backend": {"backend_id": "source-agent-md"},
                "summary": {"answer-source-evaluator": {"score": {"mean": 88.0}}},
                "metrics": {"score": {"mean": 88.0}},
                "results": [],
                "result_counts": {"cases_total": 0, "cases_with_metrics": 0, "cases_with_judge": 0},
                "gate": {"status": "pass", "metric_name": "score", "value": 88.0},
                "approval": {"required": False, "resolved": False, "approved": None},
            }

        monkeypatch.setattr("aworld_cli.evaluator_runtime._load_evaluator_hooks", lambda: {})
        monkeypatch.setattr("aworld_cli.evaluator_runtime.run_evaluation_flow", fake_run_evaluation_flow)

        result = await cmd.execute(
            CommandContext(
                cwd=os.getcwd(),
                user_args=(
                    f"--input {input_path} --kind answer "
                    f"--judge-agent {agent_path} --output {tmp_path / 'report.json'}"
                ),
            )
        )

        assert "answer-source-evaluator" in result
        assert "Report:" in result


class TestOptimizeCommand:
    """Test /optimize command direct execution."""

    @pytest.mark.asyncio
    async def test_optimize_without_args_shows_usage(self):
        cmd = CommandRegistry.get("optimize")

        result = await cmd.execute(CommandContext(cwd=os.getcwd(), user_args=""))

        assert "Usage:" in result
        assert "/optimize --from-trajectory" in result
        assert "--apply auto_verified" in result

    @pytest.mark.asyncio
    async def test_optimize_delegates_to_top_level_runtime(self, monkeypatch, tmp_path):
        cmd = CommandRegistry.get("optimize")
        trajectory_path = tmp_path / "trajectory.log"
        judge_path = tmp_path / "agent.md"
        calls = {}

        def fake_run_optimize_cli(**kwargs):
            calls.update(kwargs)
            return {
                "status": "rejected",
                "report_path": str(tmp_path / "report.json"),
                "target_selection_path": str(tmp_path / "target_selection.json"),
                "replay_path": str(tmp_path / "replay" / "cand-1"),
                "selected_candidate_id": "cand-1",
                "best_candidate_id": None,
            }

        monkeypatch.setattr(
            "aworld_cli.commands.optimize_cmd.run_optimize_cli",
            fake_run_optimize_cli,
        )

        result = await cmd.execute(
            CommandContext(
                cwd=str(tmp_path),
                user_args=(
                    f"--from-trajectory {trajectory_path} --apply auto_verified "
                    "--new-skill-policy draft_only "
                    f"--judge-agent {judge_path} --replay-timeout 600 "
                    "--replay-max-runs 1 --judge-timeout 120"
                ),
            )
        )

        assert calls["workspace_root"] == str(tmp_path)
        assert calls["target"] is None
        assert calls["infer_target"] is True
        assert calls["from_trajectory"] == str(trajectory_path)
        assert calls["apply"] == "auto_verified"
        assert calls["new_skill_policy"] == "draft_only"
        assert calls["judge_agent"] == str(judge_path)
        assert calls["judge_agent_name"] is None
        assert calls["judge_backend_ref"] is None
        assert calls["replay_timeout_seconds"] == 600
        assert calls["replay_max_steps"] == 1
        assert calls["judge_timeout_seconds"] == 120
        assert "Status: rejected" in result
        assert "Selected candidate: cand-1" in result

    @pytest.mark.asyncio
    async def test_optimize_forwards_trajectory_set(self, monkeypatch, tmp_path):
        cmd = CommandRegistry.get("optimize")
        set_path = tmp_path / "trajectory-set.json"
        calls = {}

        def fake_run_optimize_cli(**kwargs):
            calls.update(kwargs)
            return {"status": "rejected", "report_path": str(tmp_path / "report.json")}

        monkeypatch.setattr(
            "aworld_cli.commands.optimize_cmd.run_optimize_cli",
            fake_run_optimize_cli,
        )

        result = await cmd.execute(
            CommandContext(
                cwd=str(tmp_path),
                user_args=(
                    f"--from-trajectory-set {set_path} "
                    "--include-prior-runs --apply proposal"
                ),
            )
        )

        assert calls["workspace_root"] == str(tmp_path)
        assert calls["from_trajectory_set"] == str(set_path)
        assert calls["include_prior_runs"] is True
        assert calls["from_trajectory"] is None
        assert calls["infer_target"] is True
        assert "Status: rejected" in result

    @pytest.mark.asyncio
    async def test_optimize_passes_runtime_skill_registry_refresher(self, monkeypatch, tmp_path):
        cmd = CommandRegistry.get("optimize")
        calls = {}

        class FakeRuntime:
            def refresh_skill_registry(self, candidate=None):
                return {"status": "refreshed"}

        def fake_run_optimize_cli(**kwargs):
            calls.update(kwargs)
            return {"status": "succeeded", "report_path": str(tmp_path / "report.json")}

        monkeypatch.setattr(
            "aworld_cli.commands.optimize_cmd.run_optimize_cli",
            fake_run_optimize_cli,
        )

        await cmd.execute(
            CommandContext(
                cwd=str(tmp_path),
                user_args="--from-trajectory trajectory.log --apply auto_verified",
                runtime=FakeRuntime(),
            )
        )

        assert calls["runtime_registry_refresher"] is not None
        assert calls["runtime_registry_refresher"](None) == {"status": "refreshed"}

    @pytest.mark.asyncio
    async def test_optimize_drains_pending_jobs(self, monkeypatch, tmp_path):
        cmd = CommandRegistry.get("optimize")
        calls = {}

        def fake_drain_pending_self_evolve_jobs(*, workspace_root):
            calls["workspace_root"] = workspace_root
            return 2

        monkeypatch.setattr(
            "aworld_cli.commands.optimize_cmd.drain_pending_self_evolve_jobs",
            fake_drain_pending_self_evolve_jobs,
        )

        result = await cmd.execute(
            CommandContext(cwd=str(tmp_path), user_args="--drain-pending")
        )

        assert calls["workspace_root"] == str(tmp_path)
        assert result == "Drained pending self-evolve jobs: 2"

    @pytest.mark.asyncio
    async def test_optimize_reports_framework_argument_errors(self, monkeypatch, tmp_path):
        cmd = CommandRegistry.get("optimize")

        def fake_run_optimize_cli(**kwargs):
            raise ValueError("use only one judge selector")

        monkeypatch.setattr(
            "aworld_cli.commands.optimize_cmd.run_optimize_cli",
            fake_run_optimize_cli,
        )

        result = await cmd.execute(
            CommandContext(
                cwd=str(tmp_path),
                user_args=(
                    "--from-trajectory trajectory.log --apply auto_verified "
                    "--judge-agent agent.md --judge-backend-ref judges:build"
                ),
            )
        )

        assert result == "Optimize error: use only one judge selector"


class TestSlashCommandCompletion:
    """Test slash command completion sources."""

    def test_console_completion_entries_include_evaluation_command(self):
        cli = AWorldCLI()

        words, meta = cli._build_completion_entries(agent_names=[])

        assert "/evaluation" in words
        assert "/evaluation --kind answer" in words
        assert "/evaluation --kind trajectory" in words
        assert meta["/evaluation"] == "Run evaluator flows"

    def test_console_completion_entries_include_optimize_command(self):
        cli = AWorldCLI()

        words, meta = cli._build_completion_entries(agent_names=[])

        assert "/optimize" in words
        assert "/optimize --from-trajectory" in words
        assert "/optimize --apply auto_verified" in words
        assert meta["/optimize"] == "Run self-evolve optimization"

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

    def test_console_completion_entries_include_plugins_command(self):
        cli = AWorldCLI()

        words, meta = cli._build_completion_entries(agent_names=[])

        assert "/plugins" in words
        assert "/plugins list" in words
        assert "/plugins enable" in words
        assert "/plugins disable" in words
        assert "/plugins reload" in words
        assert "/plugins validate" in words
        assert meta["/plugins"] == "Manage CLI plugins"

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

    def test_console_completion_includes_plugin_commands(self):
        """Registered plugin commands should appear in slash completion entries."""
        plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()
        plugin = discover_plugins([plugin_root])[0]
        snapshot = CommandRegistry.snapshot()
        try:
            register_plugin_commands([plugin])

            cli = AWorldCLI()
            words, meta = cli._build_completion_entries(agent_names=[])

            assert "/code-review" in words
            assert meta["/code-review"] == "Review the current pull request"
        finally:
            CommandRegistry.restore(snapshot)


class TestPluginsCommand:
    @pytest.mark.asyncio
    async def test_plugins_command_lists_builtin_plugins(self, monkeypatch):
        cmd = CommandRegistry.get("plugins")

        class FakePluginManager:
            def __init__(self):
                self.plugin_dir = Path("/tmp/plugins")

        monkeypatch.setattr("aworld_cli.commands.plugins_cmd.PluginManager", FakePluginManager)
        monkeypatch.setattr(
            "aworld_cli.commands.plugins_cmd.list_available_plugins",
            lambda _manager: [
                {
                    "name": "aworld-hud",
                    "plugin_id": "aworld-hud",
                    "enabled": True,
                    "lifecycle_phase": "activate",
                    "framework_source": "manifest",
                    "capabilities": ["hud"],
                    "source": "built-in",
                    "has_agents": False,
                    "has_skills": False,
                    "path": "/repo/aworld-cli/src/aworld_cli/builtin_plugins/aworld_hud",
                }
            ],
        )

        result = await cmd.execute(CommandContext(cwd=os.getcwd(), user_args="list"))

        assert "Available plugins (1)" in result

    @pytest.mark.asyncio
    async def test_plugins_command_enable_plugin(self, monkeypatch):
        cmd = CommandRegistry.get("plugins")

        class FakePluginManager:
            def __init__(self):
                self.plugin_dir = Path("/tmp/plugins")

            def enable(self, plugin_name):
                assert plugin_name == "aworld-hud"
                return {"path": "/tmp/plugins/aworld-hud"}

        monkeypatch.setattr("aworld_cli.commands.plugins_cmd.PluginManager", FakePluginManager)

        result = await cmd.execute(CommandContext(cwd=os.getcwd(), user_args="enable aworld-hud"))

        assert "enabled" in result
        assert "/tmp/plugins/aworld-hud" in result

    @pytest.mark.asyncio
    async def test_plugins_command_reload_plugin(self, monkeypatch):
        cmd = CommandRegistry.get("plugins")

        class FakePluginManager:
            def __init__(self):
                self.plugin_dir = Path("/tmp/plugins")

            def reload(self, plugin_name):
                assert plugin_name == "aworld-hud"
                return {"path": "/tmp/plugins/aworld-hud"}

        monkeypatch.setattr("aworld_cli.commands.plugins_cmd.PluginManager", FakePluginManager)

        result = await cmd.execute(CommandContext(cwd=os.getcwd(), user_args="reload aworld-hud"))

        assert "reloaded" in result
        assert "/tmp/plugins/aworld-hud" in result

    @pytest.mark.asyncio
    async def test_plugins_command_disables_builtin_plugin(self, monkeypatch):
        cmd = CommandRegistry.get("plugins")

        class FakePluginManager:
            def __init__(self):
                self.plugin_dir = Path("/tmp/plugins")

            def disable(self, plugin_name):
                assert plugin_name == "aworld-hud"
                return {"path": "/repo/aworld-cli/src/aworld_cli/builtin_plugins/aworld_hud"}

        monkeypatch.setattr("aworld_cli.commands.plugins_cmd.PluginManager", FakePluginManager)

        result = await cmd.execute(CommandContext(cwd=os.getcwd(), user_args="disable aworld-hud"))

        assert "disabled" in result
        assert "aworld-hud" in result

    @pytest.mark.asyncio
    async def test_plugins_command_refreshes_runtime_after_disable(self, monkeypatch):
        cmd = CommandRegistry.get("plugins")

        class FakePluginManager:
            def __init__(self):
                self.plugin_dir = Path("/tmp/plugins")

            def disable(self, plugin_name):
                assert plugin_name == "aworld-hud"
                return {"path": "/repo/aworld-cli/src/aworld_cli/builtin_plugins/aworld_hud"}

        class FakeRuntime:
            def __init__(self):
                self.refreshed = False

            def refresh_plugin_framework(self):
                self.refreshed = True

        monkeypatch.setattr("aworld_cli.commands.plugins_cmd.PluginManager", FakePluginManager)

        runtime = FakeRuntime()
        result = await cmd.execute(
            CommandContext(cwd=os.getcwd(), user_args="disable aworld-hud", runtime=runtime)
        )

        assert "disabled" in result
        assert runtime.refreshed is True

    @pytest.mark.asyncio
    async def test_plugins_command_validates_plugin(self, monkeypatch):
        cmd = CommandRegistry.get("plugins")

        class FakePluginManager:
            def __init__(self):
                self.plugin_dir = Path("/tmp/plugins")

            def validate(self, plugin_name):
                assert plugin_name == "aworld-hud"
                return {
                    "valid": True,
                    "plugin_id": "aworld-hud",
                    "framework_source": "manifest",
                    "capabilities": ["hud"],
                    "path": "/repo/aworld-cli/src/aworld_cli/builtin_plugins/aworld_hud",
                }

        monkeypatch.setattr("aworld_cli.commands.plugins_cmd.PluginManager", FakePluginManager)

        result = await cmd.execute(CommandContext(cwd=os.getcwd(), user_args="validate aworld-hud"))

        assert "valid" in result.lower()
        assert "aworld-hud" in result


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


class TestSessionRestoreCommands:
    def test_format_sessions_list_uses_session_store_records(self, tmp_path):
        from aworld_cli.console import AWorldCLI
        from aworld_cli.core.session_store import CliSessionRecord

        cli = AWorldCLI()
        output = cli._format_sessions_list(
            [
                CliSessionRecord(
                    session_id="session_1",
                    created_at="2026-01-01T00:00:00",
                    updated_at="2026-01-01T01:00:00",
                    cwd=str(tmp_path),
                    agent_name="Aworld",
                    mode="interactive",
                    last_prompt="continue the work",
                )
            ],
            current_cwd=str(tmp_path),
        )

        assert "session_1" in output
        assert "Aworld" in output
        assert "continue the work" in output
        assert "Session Info" not in output

    def test_restore_known_session_delegates_to_shared_core(self, monkeypatch, tmp_path):
        from aworld_cli.console import AWorldCLI
        from aworld_cli.core.session_store import CliSessionRecord, CliSessionStore

        store = CliSessionStore(root=tmp_path)
        record = store.upsert_session(
            CliSessionRecord(
                session_id="session_restore",
                created_at="2026-01-01T00:00:00",
                updated_at="2026-01-01T00:00:00",
                cwd=str(tmp_path.resolve()),
                agent_name="Aworld",
                mode="interactive",
            )
        )

        class FakeExecutor:
            def __init__(self):
                self.session_id = "old_session"

        calls = {}

        def fake_restore_session_to_executor(**kwargs):
            calls.update(kwargs)
            kwargs["executor_instance"].session_id = kwargs["record"].session_id
            return type(
                "Result",
                (),
                {
                    "record": record,
                    "message": "Restored to session: session_restore",
                    "warning": None,
                },
            )()

        monkeypatch.setattr(
            "aworld_cli.console.restore_session_to_executor",
            fake_restore_session_to_executor,
        )

        executor = FakeExecutor()
        cli = AWorldCLI()

        message = cli._restore_cli_session(
            "session_restore",
            executor_instance=executor,
            current_agent_name="Aworld",
            session_store=store,
        )

        assert executor.session_id == "session_restore"
        assert calls["record"].session_id == "session_restore"
        assert calls["executor_instance"] is executor
        assert calls["session_store"] is store
        assert "Restored to session" in message

    def test_format_session_show_renders_record_metadata(self, tmp_path):
        from aworld_cli.console import AWorldCLI
        from aworld_cli.core.session_store import CliSessionRecord

        cli = AWorldCLI()
        output = cli._format_session_show(
            CliSessionRecord(
                session_id="session_show",
                created_at="2026-01-01T00:00:00",
                updated_at="2026-01-01T01:00:00",
                cwd=str(tmp_path),
                agent_name="Aworld",
                mode="interactive",
                source_type="local",
                source_location="/agents",
                last_prompt="continue this",
                last_task_id="task-1",
                turn_count=2,
            )
        )

        assert "session_show" in output
        assert "Aworld" in output
        assert "/agents" in output
        assert "continue this" in output

    def test_format_resume_command_hint_uses_current_session(self):
        from aworld_cli.console import AWorldCLI

        cli = AWorldCLI()
        executor = type("Executor", (), {"session_id": "session_hint"})()

        output = cli._format_resume_command_hint(executor)

        assert "aworld-cli resume session_hint" in output


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
