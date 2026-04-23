from __future__ import annotations


class UseSkillCommand:
    @property
    def name(self) -> str:
        return "use"

    @property
    def description(self) -> str:
        return "Force a skill for the next task"

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    @property
    def usage(self) -> str:
        return "/skills use <name>"

    async def run(
        self,
        cli,
        args_text: str,
        *,
        agent_name: str | None = None,
        executor_instance=None,
    ) -> bool:
        skill_name = str(args_text or "").strip()
        if not skill_name:
            cli.console.print("[yellow]Usage: /skills use <name>[/yellow]")
            return True

        if executor_instance is not None:
            try:
                cli._resolve_visible_skills(
                    agent_name=agent_name,
                    executor_instance=executor_instance,
                    requested_skill_names=[skill_name],
                )
            except ValueError as exc:
                cli.console.print(f"[red]{exc}[/red]")
                return True
            except Exception:
                pass

        cli._pending_skill_overrides = [skill_name]
        cli.console.print(
            f"[green]Will force skill on next task:[/green] {skill_name}"
        )
        return True


def build_command():
    return UseSkillCommand()
