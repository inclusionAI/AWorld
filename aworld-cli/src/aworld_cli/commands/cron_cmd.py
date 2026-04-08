# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Cron slash command - /cron for quick task management.
"""
from typing import List

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
        return "prompt"  # Generate prompt for Agent to execute

    @property
    def allowed_tools(self) -> List[str]:
        return ["cron"]  # Only allow cron tool

    async def get_prompt(self, context: CommandContext) -> str:
        """Generate prompt based on command arguments."""
        args = context.user_args  # FIXED: Use user_args

        if not args:
            # /cron without arguments -> list all tasks
            return """使用 cron tool 列出所有已配置的定时任务，以清晰的表格形式展示：
- Job ID
- 任务名称
- 调度配置（schedule）
- 下次运行时间（next_run）
- 状态（enabled/disabled）
- 最后执行状态（last_status）

如果有任务失败（last_status=error），请特别标注并显示错误信息。"""

        parts = args.split(maxsplit=1)
        action = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

        if action == "add":
            # /cron add <description>
            return f"""用户想要创建定时任务："{rest}"

请分析这个需求，确定：
1. 任务名称（简短清晰）
2. 调度时间：
   - 一次性任务：使用 'at' 类型，提供完整 ISO 时间戳
   - 重复间隔：使用 'every' 类型，如 '30m', '1h', '2d'
   - Cron 表达式：使用 'cron' 类型，如 '0 9 * * *'（每天9点）
3. 要执行的具体内容（message）
4. 是否需要在执行后删除（一次性提醒）

然后使用 cron tool 的 add 操作创建任务。"""

        elif action == "list":
            return "使用 cron tool 列出所有定时任务，以表格形式展示。"

        elif action in ["remove", "rm", "delete"]:
            # /cron remove <job_id>
            job_id = rest.strip()
            if not job_id:
                return "请提供要删除的任务 ID。先使用 /cron list 查看所有任务。"
            return f"使用 cron tool 删除任务 {job_id}。确认删除前，先显示该任务的详细信息。"

        elif action == "run":
            # /cron run <job_id>
            job_id = rest.strip()
            if not job_id:
                return "请提供要执行的任务 ID。先使用 /cron list 查看所有任务。"
            return f"使用 cron tool 立即执行任务 {job_id}。执行前显示任务详情，执行后报告结果。"

        elif action == "status":
            return """使用 cron tool 查看调度器状态，包括：
- 调度器是否运行中
- 总任务数
- 启用的任务数"""

        else:
            return f"""未知的 cron 子命令：{action}

支持的命令：
- /cron                    列出所有任务
- /cron add <描述>          创建新任务
- /cron list               列出所有任务
- /cron remove <job_id>    删除任务
- /cron run <job_id>       立即执行任务
- /cron status             查看调度器状态"""

    def get_help(self) -> str:
        """Return help information."""
        return """Cron 定时任务管理

用法：
  /cron                          列出所有定时任务
  /cron add <description>        创建新任务（自然语言描述）
  /cron list                     列出所有任务
  /cron remove <job_id>          删除任务
  /cron run <job_id>             立即执行任务
  /cron status                   查看调度器状态

示例：
  /cron add 每天早上9点提醒我运行测试
  /cron add 30分钟后提醒我提交代码
  /cron remove job-abc123
  /cron run job-abc123

注意：
- 任务只在 CLI 运行期间触发
- CLI 关闭后，任务不会执行
- 下次启动时，会重新计算未来的运行时间
"""
