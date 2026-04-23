from __future__ import annotations

from aworld_cli.core.installed_skill_manager import InstalledSkillManager


class EnableSkillCommand:
    @property
    def name(self) -> str:
        return "enable"

    @property
    def description(self) -> str:
        return "Enable an installed skill package by install id or skill name"

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    @property
    def usage(self) -> str:
        return "/skills enable <install-id-or-skill-name>"

    async def run(
        self,
        cli,
        args_text: str,
        *,
        agent_name: str | None = None,
        executor_instance=None,
    ) -> bool:
        identifier = str(args_text or "").strip()
        if not identifier:
            cli.console.print(
                "[yellow]Usage: /skills enable <install-id-or-skill-name>[/yellow]"
            )
            return True

        record = InstalledSkillManager().enable_install(identifier)
        cli.console.print(
            f"[green]Enabled skill package:[/green] {record['install_id']}"
        )
        return True
