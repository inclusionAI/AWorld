from __future__ import annotations

from dataclasses import dataclass

from aworld_cli.core.installed_skill_manager import InstalledSkillManager
from aworld_cli.core.runtime_skill_registry import build_runtime_skill_registry_view
from aworld_cli.core.skill_state_manager import SkillStateManager


@dataclass(frozen=True)
class SkillToggleResult:
    target_kind: str
    identifier: str
    enabled: bool
    install_id: str | None = None


class SkillToggleManager:
    def __init__(
        self,
        *,
        installed_manager: InstalledSkillManager | None = None,
        state_manager: SkillStateManager | None = None,
    ) -> None:
        self.installed_manager = installed_manager or InstalledSkillManager()
        self.state_manager = state_manager or SkillStateManager()

    def enable(self, identifier: str) -> SkillToggleResult:
        return self._set_enabled(identifier, enabled=True)

    def disable(self, identifier: str) -> SkillToggleResult:
        return self._set_enabled(identifier, enabled=False)

    def _set_enabled(self, identifier: str, *, enabled: bool) -> SkillToggleResult:
        exact_install = self._find_exact_install(identifier)
        if exact_install is not None:
            install_id = str(exact_install["install_id"])
            if enabled:
                self.installed_manager.enable_install(install_id)
            else:
                self.installed_manager.disable_install(install_id)
            return SkillToggleResult(
                target_kind="package",
                identifier=install_id,
                enabled=enabled,
                install_id=install_id,
            )

        runtime_skill_name = self._resolve_runtime_skill_name(identifier)
        if runtime_skill_name is not None:
            if enabled:
                self.state_manager.enable_skill(runtime_skill_name)
            else:
                self.state_manager.disable_skill(runtime_skill_name)
            return SkillToggleResult(
                target_kind="skill",
                identifier=runtime_skill_name,
                enabled=enabled,
            )

        fallback = (
            self.installed_manager.enable_install(identifier)
            if enabled
            else self.installed_manager.disable_install(identifier)
        )
        install_id = str(fallback["install_id"])
        return SkillToggleResult(
            target_kind="package",
            identifier=install_id,
            enabled=enabled,
            install_id=install_id,
        )

    def _find_exact_install(self, identifier: str) -> dict[str, str | int | bool] | None:
        normalized = str(identifier or "").strip().lower()
        if not normalized:
            return None

        for install in self.installed_manager.list_installs(include_disabled=True):
            install_id = str(install.get("install_id", "")).strip()
            name = str(install.get("name", "")).strip()
            if normalized in {install_id.lower(), name.lower()}:
                return install
        return None

    def _resolve_runtime_skill_name(self, identifier: str) -> str | None:
        normalized = str(identifier or "").strip().lower()
        if not normalized:
            return None

        runtime_skills = build_runtime_skill_registry_view().get_all_skills()
        matches = [
            skill_name
            for skill_name in runtime_skills
            if skill_name.strip().lower() == normalized
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"Multiple runtime skills matched: {identifier}")
        return None
