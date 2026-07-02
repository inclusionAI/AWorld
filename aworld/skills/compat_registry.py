from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from aworld.logs.util import logger


class CompatSkillRegistry:
    """Compatibility registry backed by framework-powered `collect_skill_docs()`."""

    def __init__(self, cache_dir: Path | None = None, conflict_strategy: str = "keep_first") -> None:
        if cache_dir is None:
            from aworld.utils.skill_loader import DEFAULT_CACHE_DIR

            cache_dir = DEFAULT_CACHE_DIR

        if conflict_strategy not in {"keep_first", "keep_last", "raise"}:
            raise ValueError(
                f"Invalid conflict_strategy: {conflict_strategy}. "
                "Must be one of: keep_first, keep_last, raise"
            )

        self._sources: dict[str, str | Path] = {}
        self._skills: dict[str, dict[str, Any]] = {}
        self._source_to_skills: dict[str, list[str]] = {}
        self._cache_dir = cache_dir
        self._conflict_strategy = conflict_strategy

    def _load_skills_from_source(self, source: str | Path) -> dict[str, dict[str, Any]]:
        from aworld.utils.skill_loader import collect_skill_docs

        return collect_skill_docs(source, cache_dir=self._cache_dir)

    def register_source(
        self,
        source: str | Path,
        source_name: str | None = None,
        force_reload: bool = False,
    ) -> int:
        source_str = str(source)
        source_key = source_name or source_str

        if source_key in self._sources and not force_reload:
            logger.debug(f"ℹ️ Source already registered: {source_key}")
            return len(self._source_to_skills.get(source_key, []))

        loaded_skills = self._load_skills_from_source(source)
        previous_skills = list(self._source_to_skills.get(source_key, []))

        if force_reload and previous_skills:
            for skill_name in previous_skills:
                if self._get_skill_source(skill_name) == source_key:
                    self._skills.pop(skill_name, None)

        source_skill_names: list[str] = []
        for skill_name, skill_data in loaded_skills.items():
            if skill_name in self._skills:
                if self._conflict_strategy == "raise":
                    raise ValueError(
                        f"Skill name conflict: '{skill_name}' already exists. "
                        f"Existing source: {self._get_skill_source(skill_name)}, "
                        f"New source: {source_key}"
                    )
                if self._conflict_strategy == "keep_first":
                    logger.warning(
                        f"⚠️ Skill '{skill_name}' already exists, keeping first version. "
                        f"New source: {source_key}"
                    )
                    continue
                if self._conflict_strategy == "keep_last":
                    logger.warning(
                        f"⚠️ Skill '{skill_name}' already exists, replacing with new version. "
                        f"Old source: {self._get_skill_source(skill_name)}, "
                        f"New source: {source_key}"
                    )
                    old_source = self._get_skill_source(skill_name)
                    if old_source and old_source in self._source_to_skills:
                        self._source_to_skills[old_source] = [
                            name
                            for name in self._source_to_skills[old_source]
                            if name != skill_name
                        ]

            self._skills[skill_name] = dict(skill_data)
            source_skill_names.append(skill_name)

        self._sources[source_key] = source
        self._source_to_skills[source_key] = source_skill_names
        return len(source_skill_names)

    def unregister_source(self, source_name: str) -> int:
        if source_name not in self._sources:
            logger.warning(f"⚠️ Source not registered: {source_name}")
            return 0

        skill_names = self._source_to_skills.get(source_name, [])
        removed_count = 0
        for skill_name in list(skill_names):
            if self._get_skill_source(skill_name) == source_name:
                del self._skills[skill_name]
                removed_count += 1

        del self._sources[source_name]
        self._source_to_skills.pop(source_name, None)
        return removed_count

    def reload_source(self, source_name: str) -> int:
        if source_name not in self._sources:
            raise ValueError(f"Source not registered: {source_name}")
        return self.register_source(
            self._sources[source_name],
            source_name=source_name,
            force_reload=True,
        )

    def get_all_skills(self) -> dict[str, dict[str, Any]]:
        return {name: dict(data) for name, data in self._skills.items()}

    def get_skill(self, skill_name: str) -> dict[str, Any] | None:
        skill = self._skills.get(skill_name)
        return dict(skill) if skill is not None else None

    def get_skills_by_source(self, source_name: str) -> dict[str, dict[str, Any]]:
        return {
            name: dict(self._skills[name])
            for name in self._source_to_skills.get(source_name, [])
            if name in self._skills
        }

    def list_sources(self) -> list[str]:
        return list(self._sources.keys())

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())

    def search_skills(
        self,
        keyword: str,
        search_fields: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        if search_fields is None:
            search_fields = ["name", "description", "usage"]

        keyword_lower = keyword.lower()
        results: dict[str, dict[str, Any]] = {}
        for skill_name, skill_data in self._skills.items():
            for field in search_fields:
                field_value = skill_name if field == "name" else skill_data.get(field, "")
                if isinstance(field_value, str) and keyword_lower in field_value.lower():
                    results[skill_name] = dict(skill_data)
                    break
        return results

    def get_skills_by_regex(
        self,
        pattern: str,
        match_field: str = "name",
        flags: int = 0,
    ) -> dict[str, dict[str, Any]]:
        valid_fields = ["name", "description", "usage", "type"]
        if match_field not in valid_fields:
            raise ValueError(
                f"Invalid match_field: {match_field}. Must be one of: {valid_fields}"
            )

        compiled_pattern = re.compile(pattern, flags)
        results: dict[str, dict[str, Any]] = {}
        for skill_name, skill_data in self._skills.items():
            field_value = skill_name if match_field == "name" else skill_data.get(match_field, "")
            if isinstance(field_value, str) and compiled_pattern.search(field_value):
                results[skill_name] = dict(skill_data)
        return results

    def get_skill_configs(self) -> dict[str, dict[str, Any]]:
        configs: dict[str, dict[str, Any]] = {}
        for skill_name, skill_data in self._skills.items():
            configs[skill_name] = {
                "name": skill_data.get("name", skill_name),
                "desc": skill_data.get("description", skill_data.get("desc", "")),
                "usage": skill_data.get("usage", ""),
                "tool_list": dict(skill_data.get("tool_list", {}) or {}),
                "type": skill_data.get("type", ""),
                "active": skill_data.get("active", False),
            }
        return configs

    def update_cache(self, source_name: str | None = None) -> None:
        from aworld.utils.skill_loader import (
            clone_or_update_github_repo,
            parse_github_url,
        )

        sources_to_update = [source_name] if source_name else list(self._sources.keys())
        for source_key in sources_to_update:
            if source_key not in self._sources:
                logger.warning(f"⚠️ Source not found: {source_key}")
                continue

            source = str(self._sources[source_key])
            if "github.com" in source or source.startswith("git@github.com"):
                repo_info = parse_github_url(source)
                if repo_info:
                    clone_or_update_github_repo(
                        repo_info,
                        cache_dir=self._cache_dir,
                        force_update=False,
                    )
                    self.reload_source(source_key)

    def clear(self) -> None:
        self._sources.clear()
        self._skills.clear()
        self._source_to_skills.clear()

    def _get_skill_source(self, skill_name: str) -> str | None:
        for source_name, skill_names in self._source_to_skills.items():
            if skill_name in skill_names:
                return source_name
        return None
