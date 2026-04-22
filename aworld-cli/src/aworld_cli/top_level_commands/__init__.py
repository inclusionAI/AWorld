from __future__ import annotations

from aworld_cli.top_level_commands.skill_cmd import SkillTopLevelCommand


def register_builtin_top_level_commands(registry) -> None:
    registry.register(SkillTopLevelCommand(), source="builtin")
