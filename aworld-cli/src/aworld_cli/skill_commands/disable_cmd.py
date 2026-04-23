from __future__ import annotations

from aworld_cli.core.installed_skill_manager import InstalledSkillManager


class DisableSkillCommand:
    @property
    def name(self) -> str:
        return "disable"

    @property
    def description(self) -> str:
        return "Disable an installed skill package by install id or skill name"

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    @property
    def usage(self) -> str:
        return "/skills disable <install-id-or-skill-name>"

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
                "[yellow]Usage: /skills disable <install-id-or-skill-name>[/yellow]"
            )
            return True

        record = InstalledSkillManager().disable_install(identifier)
        cli.console.print(
            f"[green]Disabled skill package:[/green] {record['install_id']}"
        )
        return True
