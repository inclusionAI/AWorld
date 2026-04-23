from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from aworld.logs.util import logger
from aworld.memory.main import _default_file_memory_store


class RuntimeBootstrapError(RuntimeError):
    """Raised when CLI runtime bootstrap cannot proceed."""


@dataclass(frozen=True)
class RuntimeBootstrapResult:
    config_dict: dict[str, Any]
    skill_registry: Any


def bootstrap_runtime(
    *,
    env_file: str = ".env",
    skill_paths: list[str] | None = None,
    show_banner: bool,
    init_middlewares_fn: Callable[..., None],
    show_banner_fn: Callable[[], None],
    console: Any | None = None,
) -> RuntimeBootstrapResult:
    from aworld_cli._globals import console as global_console
    from aworld_cli.core.config import has_model_config, load_config_with_env
    from aworld_cli.core.skill_registry import get_skill_registry

    resolved_console = console or global_console
    config_dict, _, _ = load_config_with_env(env_file)
    init_middlewares_fn(
        init_memory=True,
        init_retriever=False,
        custom_memory_store=_default_file_memory_store(),
    )

    if show_banner:
        show_banner_fn()

    if not has_model_config(config_dict):
        resolved_console.print(
            "[yellow]No model configuration (API key, etc.) detected. Please configure before starting.[/yellow]"
        )
        resolved_console.print("[dim]Run: aworld-cli --config[/dim]")
        resolved_console.print(
            "[dim]Or create .env in the current directory. See: [link=https://github.com/inclusionAI/AWorld/blob/main/README.md]README[/link][/dim]"
        )
        raise RuntimeBootstrapError("missing model configuration")

    if skill_paths:
        registry = get_skill_registry(skill_paths=skill_paths)
    else:
        registry = get_skill_registry()

    all_skills = registry.get_all_skills()
    if all_skills:
        skill_names = list(all_skills.keys())
        logger.info(
            "Loaded %d global skill(s): %s",
            len(skill_names),
            ", ".join(skill_names),
        )

    return RuntimeBootstrapResult(
        config_dict=config_dict,
        skill_registry=registry,
    )
