# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron slash command - /cron for quick task management.
"""
from typing import List, Optional

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
            result = await cron_tool(action="status")
            return self._format_result("status", result)

        else:
            return f"""未知的 cron 子命令：{action}

支持的命令：
- /cron                    列出所有任务
- /cron add <描述>          创建新任务
- /cron list               列出所有任务
- /cron remove <job_id>    删除任务
- /cron run <job_id>       立即执行任务
- /cron enable <job_id>    启用任务
- /cron disable <job_id>   禁用任务
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

        if action == "add":
            return (
                f"{result.get('message')}\n"
                f"next_run: {result.get('next_run')}"
            )

        return result.get("message", "操作成功")

    def get_help(self) -> str:
        """Return help information."""
        return """Cron 定时任务管理

用法：
  /cron                          列出所有定时任务
  /cron add <description>        创建新任务（自然语言描述）
  /cron list                     列出所有任务
  /cron remove <job_id>          删除任务
  /cron run <job_id>             立即执行任务
  /cron enable <job_id>          启用任务
  /cron disable <job_id>         禁用任务
  /cron status                   查看调度器状态

示例：
  /cron add 每天早上9点提醒我运行测试
  /cron add 30分钟后提醒我提交代码
  /cron remove job-abc123
  /cron run job-abc123
  /cron disable job-abc123
  /cron enable job-abc123

注意：
- 任务只在 CLI 运行期间触发
- CLI 关闭后，任务不会执行
- 下次启动时，会重新计算未来的运行时间
- 禁用的任务不会被调度，但保留配置
"""
