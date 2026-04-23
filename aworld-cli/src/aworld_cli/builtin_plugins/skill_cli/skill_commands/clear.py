from __future__ import annotations


class ClearSkillCommand:
    @property
    def name(self) -> str:
        return "clear"

    @property
    def description(self) -> str:
        return "Clear the pending forced skill"

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    @property
    def usage(self) -> str:
        return "/skills clear"

    async def run(
        self,
        cli,
        args_text: str,
        *,
        agent_name: str | None = None,
        executor_instance=None,
    ) -> bool:
        cli._pending_skill_overrides = []
        cli.console.print("[dim]Cleared pending explicit skill selection.[/dim]")
        return True


def build_command():
    return ClearSkillCommand()
