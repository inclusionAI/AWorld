# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron slash command - /cron for quick task management.
"""
import asyncio
from typing import List, Optional

from rich.markup import escape
from aworld.tools.cron_tool import cron_tool

from aworld_cli.core.command_system import Command, CommandContext, register_command


@register_command
class CronCommand(Command):
    """Cron task management command."""

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return "Manage scheduled tasks"

    @property
    def command_type(self) -> str:
        return "tool"

    @property
    def allowed_tools(self) -> Optional[List[str]]:
        return None

    @property
    def completion_items(self) -> dict[str, str]:
        return {
            "/cron add": "创建新任务",
            "/cron list": "列出所有任务",
            "/cron show": "查看单个任务详情",
            "/cron inbox": "查看并清空未读提醒通知",
            "/cron remove all": "批量删除当前列表中的全部任务",
            "/cron enable all": "批量启用可重新激活的任务",
            "/cron disable all": "批量禁用当前活跃任务",
            "/cron remove": "删除任务",
            "/cron run": "立即执行任务",
            "/cron enable": "启用任务",
            "/cron disable": "禁用任务",
            "/cron status": "查看调度器状态",
        }

    async def execute(self, context: CommandContext) -> str:
        """Execute cron subcommands directly through cron_tool."""
        args = context.user_args  # FIXED: Use user_args

        if not args:
            result = await cron_tool(action="list")
            return self._format_result("list", result)

        parts = args.split(maxsplit=1)
        action = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

        if action == "add":
            result = await cron_tool(action="add", request=rest)
            return self._format_result("add", result)

        elif action == "list":
            result = await cron_tool(action="list")
            return self._format_result("list", result)

        elif action == "show":
            job_id = rest.strip()
            if not job_id:
                return "请提供要查看的任务 ID。用法：/cron show <job_id>"
            result = await cron_tool(action="show", job_id=job_id)
            job = result.get("job") if result.get("success") else None
            runtime = getattr(context, "runtime", None)
            if (
                job
                and job.get("running")
                and runtime is not None
                and hasattr(runtime, "_get_cron_progress_logs")
            ):
                return await self._follow_running_job(context, job_id, job)
            return self._format_result("show", result)

        elif action == "inbox":
            return await self._render_inbox(context, rest.strip() or None)

        elif action in ["remove", "rm", "delete"]:
            job_id = rest.strip()
            if not job_id:
                return "请提供要删除的任务 ID。先使用 /cron list 查看所有任务。"
            result = await cron_tool(action="remove", job_id=job_id)
            return self._format_result("remove", result)

        elif action == "run":
            job_id = rest.strip()
            if not job_id:
                return "请提供要执行的任务 ID。先使用 /cron list 查看所有任务。"
            result = await cron_tool(action="run", job_id=job_id)
            return self._format_result("run", result)

        elif action == "enable":
            job_id = rest.strip()
            if not job_id:
                return "请提供要启用的任务 ID。先使用 /cron list 查看所有任务。"
            result = await cron_tool(action="enable", job_id=job_id)
            return self._format_result("enable", result)

        elif action == "disable":
            job_id = rest.strip()
            if not job_id:
                return "请提供要禁用的任务 ID。先使用 /cron list 查看所有任务。"
            result = await cron_tool(action="disable", job_id=job_id)
            return self._format_result("disable", result)

        elif action == "status":
            if rest.strip():
                return "如需查看单个任务详情，请使用 /cron show <job_id>。/cron status 仅显示调度器整体状态。"
            result = await cron_tool(action="status")
            return self._format_result("status", result)

        else:
            return f"""未知的 cron 子命令：{action}

支持的命令：
- /cron                    列出所有任务
- /cron add <描述>          创建新任务
- /cron list               列出所有任务
- /cron show <job_id>      查看单个任务详情
- /cron inbox [job_id]     查看并清空未读提醒通知
- /cron remove all         批量删除当前列表中的全部任务
- /cron remove <job_id>    删除任务
- /cron run <job_id>       立即执行任务
- /cron enable <job_id|all>    启用任务或批量启用可重新激活的任务
- /cron disable <job_id|all>   禁用任务或批量禁用当前活跃任务
- /cron status             查看调度器状态"""

    def _format_result(self, action: str, result: dict) -> str:
        if not result.get("success"):
            return f"操作失败: {result.get('error', 'unknown error')}"

        if action == "status":
            return (
                "Cron 调度器状态\n"
                f"- scheduler_running: {result.get('scheduler_running')}\n"
                f"- total_jobs: {result.get('total_jobs')}\n"
                f"- enabled_jobs: {result.get('enabled_jobs')}"
            )

        if action == "list":
            jobs = result.get("jobs", [])
            if not jobs:
                return "当前没有定时任务。"

            lines = [f"共 {len(jobs)} 个定时任务："]
            for job in jobs:
                lines.append(
                    f"- {job['id']} | {job['name']} | {job['schedule']} | "
                    f"enabled={job['enabled']} | next_run={job['next_run']} | "
                    f"last_status={job['last_status']}"
                )
            return "\n".join(lines)

        if action == "show":
            job = result.get("job")
            if not job:
                return "未找到任务详情。"
            return (
                "定时任务详情：\n"
                f"- id: {job.get('id')}\n"
                f"- name: {job.get('name')}\n"
                f"- schedule: {job.get('schedule')}\n"
                f"- enabled: {job.get('enabled')}\n"
                f"- running: {job.get('running')}\n"
                f"- next_run: {job.get('next_run')}\n"
                f"- last_run: {job.get('last_run')}\n"
                f"- max_runs: {job.get('max_runs')}\n"
                f"- run_count: {job.get('run_count')}\n"
                f"- last_status: {job.get('last_status')}\n"
                f"- last_error: {job.get('last_error')}\n"
                f"- last_result_summary: {job.get('last_result_summary')}"
            )

        if action == "add":
            lines = [
                result.get("message"),
                f"next_run: {result.get('next_run')}",
            ]
            advance_reminder = result.get("advance_reminder")
            if advance_reminder:
                lines.append(
                    "advance_reminder: "
                    f"{advance_reminder.get('next_run')} "
                    f"(lead={advance_reminder.get('lead_minutes')}m)"
                )
            return "\n".join(lines)

        return result.get("message", "操作成功")

    async def _render_inbox(self, context: CommandContext, job_id: Optional[str] = None) -> str:
        runtime = getattr(context, "runtime", None)
        if runtime is None or not hasattr(runtime, "_drain_notifications"):
            return "当前会话不支持提醒收件箱。"

        if job_id:
            notifications = await runtime._drain_notifications(job_id=job_id)
        else:
            notifications = await runtime._drain_notifications()
        if not notifications:
            if job_id:
                return f"当前没有来自任务 {job_id} 的未读通知。"
            return "当前没有未读通知。"

        title = (
            f"任务 {job_id} 的未读通知（共 {len(notifications)} 条）："
            if job_id else
            f"未读通知（共 {len(notifications)} 条）："
        )
        lines = [title]
        for item in notifications:
            next_run_at = getattr(item, "next_run_at", None)
            lines.append(
                f"- [{getattr(item, 'status', 'ok')}] {getattr(item, 'job_name', '')} | "
                f"id={getattr(item, 'job_id', '')}"
            )
            lines.append(f"  summary: {getattr(item, 'summary', '')}")
            detail = getattr(item, "detail", None)
            if detail:
                lines.append(f"  content: {detail}")
            if next_run_at:
                lines.append(f"  next_run: {next_run_at}")
            created_at = getattr(item, "created_at", None)
            if created_at:
                lines.append(f"  created_at: {created_at}")
        return "\n".join(lines)

    async def _follow_running_job(self, context: CommandContext, job_id: str, initial_job: dict) -> str:
        """Follow a running cron job and print live execution logs."""
        from rich.console import Console as RichConsole

        runtime = getattr(context, "runtime", None)
        runtime_console = getattr(getattr(runtime, "cli", None), "console", None)
        console = runtime_console or RichConsole()

        console.print(f"\n[bold cyan]🔄 跟踪定时任务执行：{job_id}[/bold cyan]")
        console.print(f"[dim]任务名称：{initial_job.get('name')}[/dim]")
        console.print("[yellow]Press Ctrl+C to stop following[/yellow] [dim](任务仍会继续执行)[/dim]\n")
        console.print(self._format_result("show", {"success": True, "job": initial_job}))
        console.print("[dim]--- Live Execution Logs ---[/dim]")

        seen_log_ids = set()
        showed_waiting_hint = False

        try:
            while True:
                progress_logs = []
                if runtime and hasattr(runtime, "_get_cron_progress_logs"):
                    progress_logs = runtime._get_cron_progress_logs(job_id) or []

                new_logs = [log for log in progress_logs if getattr(log, "id", None) not in seen_log_ids]
                if new_logs:
                    for log in new_logs:
                        seen_log_ids.add(getattr(log, "id", ""))
                        self._print_progress_log(console, log)
                    showed_waiting_hint = False
                elif not showed_waiting_hint:
                    console.print("[dim]等待执行日志...[/dim]")
                    showed_waiting_hint = True

                result = await cron_tool(action="show", job_id=job_id)
                if result.get("success"):
                    job = result.get("job") or {}
                    if not job.get("running"):
                        console.print(
                            f"\n[bold green]✅ 定时任务执行结束[/bold green] "
                            f"(status={job.get('last_status')})"
                        )
                        if job.get("last_result_summary"):
                            console.print(f"[bold]结果：[/bold]{job.get('last_result_summary')}")
                        if job.get("last_error"):
                            console.print(f"[bold red]错误：[/bold red]{job.get('last_error')}")
                        return ""
                else:
                    latest_log = progress_logs[-1] if progress_logs else None
                    if latest_log and getattr(latest_log, "terminal", False):
                        console.print("\n[bold green]✅ 定时任务执行结束[/bold green]")
                        return ""

                await asyncio.sleep(0.2)

        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print(f"\n[yellow]⏸ 已停止跟踪 {job_id}[/yellow]")
            console.print("[dim]任务仍在后台继续执行[/dim]")
            console.print(f"[cyan]→ /cron show {job_id}[/cyan]  [dim]重新查看状态[/dim]")
            return ""

    def _print_progress_log(self, console, log) -> None:
        """Render a progress log entry with multiline-safe formatting."""
        time_text = str(getattr(log, "created_at", ""))[11:19] or "--:--:--"
        level = getattr(log, "level", "info")
        color = {
            "info": "cyan",
            "warning": "yellow",
            "error": "red",
            "success": "green",
        }.get(level, "white")
        message = str(getattr(log, "message", "") or "")
        lines = message.splitlines() or [""]

        prefix = f"[{time_text}] "
        indent = " " * len(prefix)
        console.print(f"[dim]{escape(prefix)}[/dim][{color}]{escape(lines[0])}[/{color}]")
        for line in lines[1:]:
            console.print(f"[dim]{escape(indent)}[/dim][{color}]{escape(line)}[/{color}]")

    def get_help(self) -> str:
        """Return help information."""
        return """Cron 定时任务管理

用法：
  /cron                          列出所有定时任务
  /cron add <description>        创建新任务（自然语言描述）
  /cron list                     列出所有任务
  /cron show <job_id>            查看单个任务详情
  /cron inbox [job_id]           查看并清空未读提醒通知
  /cron remove all               批量删除当前列表中的全部任务
  /cron remove <job_id>          删除任务
  /cron run <job_id>             立即执行任务
  /cron enable <job_id|all>      启用任务或批量启用可重新激活的任务
  /cron disable <job_id|all>     禁用任务或批量禁用当前活跃任务
  /cron status                   查看调度器状态

示例：
  /cron add 每天早上9点提醒我运行测试
  /cron add 30分钟后提醒我提交代码
  /cron show job-abc123
  /cron inbox
  /cron inbox job-abc123
  /cron remove job-abc123
  /cron run job-abc123
  /cron disable job-abc123
  /cron enable job-abc123
  /cron disable all
  /cron enable all

注意：
- 任务只在 CLI 运行期间触发
- CLI 关闭后，任务不会执行
- 下次启动时，会重新计算未来的运行时间
- 禁用的任务不会被调度，但保留配置
"""
