"""
Core modules for aworld-cli.
"""

from importlib import import_module


_LAZY_EXPORTS = {
    "InstalledSkillManager": (".installed_skill_manager", "InstalledSkillManager"),
    "LocalAgent": (".agent_registry", "LocalAgent"),
    "LocalAgentRegistry": (".agent_registry", "LocalAgentRegistry"),
    "agent": (".agent_registry", "agent"),
    "init_agents": (".loader", "init_agents"),
    "get_skill_registry": (".skill_registry", "get_skill_registry"),
    "register_skill_source": (".skill_registry", "register_skill_source"),
    "reset_skill_registry": (".skill_registry", "reset_skill_registry"),
}


def __getattr__(name: str):
    """Load heavy Agent/runtime modules only when their public symbol is used."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})


__all__ = [
    "InstalledSkillManager",
    "LocalAgent",
    "LocalAgentRegistry",
    "agent",
    "init_agents",
    "get_skill_registry",
    "register_skill_source",
    "reset_skill_registry",
]
