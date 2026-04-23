from __future__ import annotations

from aworld_cli.core.skill_toggle_manager import SkillToggleManager


class EnableSkillCommand:
    @property
    def name(self) -> str:
        return "enable"

    @property
    def description(self) -> str:
        return "Enable a skill or installed skill package"

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    @property
    def usage(self) -> str:
        return "/skills enable <skill-name-or-package-name>"

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
                "[yellow]Usage: /skills enable <skill-name-or-package-name>[/yellow]"
            )
            return True

        result = SkillToggleManager().enable(identifier)
        if result.target_kind == "package":
            cli.console.print(
                f"[green]Enabled skill package:[/green] {result.identifier}"
            )
        else:
            cli.console.print(
                f"[green]Enabled skill:[/green] {result.identifier}"
            )
        return True
