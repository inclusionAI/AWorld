from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aworld.plugins.discovery import discover_plugins
from aworld.skills.compat_provider import build_compat_provider
from aworld.skills.plugin_provider import PluginSkillProvider
from aworld.skills.registry import SkillRegistry as FrameworkSkillRegistry

from aworld_cli.core.installed_skill_manager import InstalledSkillManager
from aworld_cli.core.plugin_manager import PluginManager
from aworld_cli.core.skill_registry import get_user_skills_paths


@dataclass(frozen=True)
class RuntimeSkillRegistryView:
    registry: FrameworkSkillRegistry
    source_paths: tuple[str, ...]

    def get_all_skills(self) -> dict[str, dict[str, object]]:
        skills: dict[str, dict[str, object]] = {}
        for descriptor in self.registry.list_descriptors():
            if descriptor.skill_name in skills:
                continue
            skills[descriptor.skill_name] = self.registry.build_skill_config(
                descriptor.skill_id
            )
        return skills

    def list_descriptors(self):
        return self.registry.list_descriptors()


def build_runtime_skill_registry_view(
    *,
    skill_paths: list[str] | None = None,
    cwd: Path | None = None,
) -> RuntimeSkillRegistryView:
    providers = []
    source_paths: list[str] = []

    plugin_manager = PluginManager()
    for plugin in discover_plugins(plugin_manager.get_runtime_plugin_roots()):
        provider = PluginSkillProvider(plugin)
        descriptors = provider.list_descriptors()
        if descriptors:
            providers.append(provider)
            source_paths.append(str(Path(plugin.manifest.plugin_root).resolve()))

    for source in _iter_runtime_compat_sources(skill_paths=skill_paths, cwd=cwd):
        providers.append(build_compat_provider(source))
        source_paths.append(source)

    return RuntimeSkillRegistryView(
        registry=FrameworkSkillRegistry(providers),
        source_paths=tuple(dict.fromkeys(source_paths)),
    )


def _iter_runtime_compat_sources(
    *,
    skill_paths: list[str] | None = None,
    cwd: Path | None = None,
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(source: str | Path | None) -> None:
        if source is None:
            return
        normalized = str(source).strip()
        if not normalized or normalized in seen:
            return
        if "github.com" not in normalized and not normalized.startswith("git@"):
            path = Path(normalized).expanduser().resolve()
            if not path.exists() or not path.is_dir():
                return
            normalized = str(path)
        ordered.append(normalized)
        seen.add(normalized)

    for source in skill_paths or ():
        _add(source)

    for source_path in get_user_skills_paths():
        _add(source_path)

    default_skills_dir = (cwd or Path.cwd()) / "skills"
    _add(default_skills_dir)

    return ordered
