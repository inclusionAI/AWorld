from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aworld.plugins.discovery import discover_plugins
from aworld.skills.compat_provider import build_compat_provider
from aworld.skills.plugin_provider import PluginSkillProvider
from aworld.skills.registry import SkillRegistry as FrameworkSkillRegistry


_SCOPE_ORDER = {
    "session": 0,
    "workspace": 1,
    "global": 2,
}


@dataclass(frozen=True)
class SkillResolverRequest:
    plugin_roots: tuple[Path, ...]
    runtime_scope: str
    agent_name: str | None = None
    task_text: str | None = None
    requested_skill_names: tuple[str, ...] = ()
    compatibility_sources: tuple[str, ...] = ()
    compatibility_skill_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedSkillSet:
    skill_configs: dict[str, dict[str, Any]]
    active_skill_names: tuple[str, ...]
    available_skill_names: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedSkillCandidate:
    skill_name: str
    skill_path: str
    scope: str
    visibility: str
    metadata: dict[str, object]
    skill_data: dict[str, Any]


class SkillActivationResolver:
    def resolve(self, request: SkillResolverRequest) -> ResolvedSkillSet:
        candidates = self._load_candidates(request)
        filtered = self._filter_candidates(candidates, request)
        selected = self._select_active_skills(filtered, request)
        return self._build_skill_configs(filtered, selected)

    def _build_registry(
        self, request: SkillResolverRequest
    ) -> tuple[FrameworkSkillRegistry, set[str]]:
        providers = []
        compatibility_provider_ids: set[str] = set()

        for plugin in discover_plugins(request.plugin_roots):
            providers.append(PluginSkillProvider(plugin))

        for source in request.compatibility_sources:
            provider = build_compat_provider(source)
            providers.append(provider)
            compatibility_provider_ids.add(provider.provider_id())

        return FrameworkSkillRegistry(providers), compatibility_provider_ids

    def _load_candidates(
        self, request: SkillResolverRequest
    ) -> list[ResolvedSkillCandidate]:
        candidates: list[ResolvedSkillCandidate] = []
        seen: set[str] = set()
        registry, compatibility_provider_ids = self._build_registry(request)
        compatibility_patterns = tuple(request.compatibility_skill_patterns)

        for descriptor in registry.list_descriptors():
            if (
                descriptor.provider_id in compatibility_provider_ids
                and compatibility_patterns
                and not self._matches_patterns(descriptor.skill_name, compatibility_patterns)
            ):
                continue

            if descriptor.skill_name in seen:
                continue

            content = registry.load_content(descriptor.skill_id)
            skill_data = {
                "name": descriptor.skill_name,
                "description": descriptor.description,
                "tool_list": dict(content.tool_list),
                "usage": content.usage,
                "skill_path": descriptor.skill_file,
                "asset_root": descriptor.asset_root,
            }
            if descriptor.requirements:
                skill_data["aworld_metadata"] = dict(descriptor.requirements)
            if "type" in descriptor.metadata:
                skill_data["type"] = descriptor.metadata["type"]
            if "active" in descriptor.metadata:
                skill_data["active"] = bool(descriptor.metadata["active"])

            candidates.append(
                ResolvedSkillCandidate(
                    skill_name=descriptor.skill_name,
                    skill_path=descriptor.skill_file,
                    scope=str(descriptor.scope or "workspace").strip().lower() or "workspace",
                    visibility=str(descriptor.visibility or "public").strip().lower() or "public",
                    metadata=dict(descriptor.metadata or {}),
                    skill_data=skill_data,
                )
            )
            seen.add(descriptor.skill_name)

        return candidates

    def _filter_candidates(
        self,
        candidates: list[ResolvedSkillCandidate],
        request: SkillResolverRequest,
    ) -> list[ResolvedSkillCandidate]:
        filtered: list[ResolvedSkillCandidate] = []
        for candidate in candidates:
            if candidate.visibility != "public":
                continue
            if not self._scope_allows(
                candidate.scope,
                str(request.runtime_scope or "workspace").strip().lower() or "workspace",
            ):
                continue
            if not self._agent_matches(candidate, request.agent_name):
                continue
            filtered.append(candidate)
        return filtered

    def _agent_matches(
        self, candidate: ResolvedSkillCandidate, agent_name: str | None
    ) -> bool:
        selectors = candidate.metadata.get("agent_selectors")
        if not selectors:
            return True
        if not isinstance(selectors, (list, tuple, set)):
            return False

        normalized_agent = (agent_name or "").strip().lower()
        if not normalized_agent:
            return False

        return normalized_agent in {
            str(selector).strip().lower()
            for selector in selectors
            if str(selector).strip()
        }

    def _scope_allows(self, candidate_scope: str, runtime_scope: str) -> bool:
        candidate_rank = _SCOPE_ORDER.get(candidate_scope, _SCOPE_ORDER["workspace"])
        runtime_rank = _SCOPE_ORDER.get(runtime_scope, _SCOPE_ORDER["workspace"])
        return candidate_rank >= runtime_rank

    def _select_active_skills(
        self,
        candidates: list[ResolvedSkillCandidate],
        request: SkillResolverRequest,
    ) -> tuple[str, ...]:
        if request.requested_skill_names:
            requested: list[str] = []
            available = {candidate.skill_name for candidate in candidates}
            for skill_name in request.requested_skill_names:
                if skill_name not in available:
                    raise ValueError(f"Requested skill is not available: {skill_name}")
                if skill_name not in requested:
                    requested.append(skill_name)
            return tuple(requested)

        scored = sorted(
            (
                (
                    self._score_candidate(candidate, request.task_text or ""),
                    candidate.skill_name,
                )
                for candidate in candidates
            ),
            key=lambda item: (-item[0], item[1]),
        )
        if not scored or scored[0][0] <= 0:
            return tuple()
        return (scored[0][1],)

    def _score_candidate(
        self, candidate: ResolvedSkillCandidate, task_text: str
    ) -> int:
        normalized = task_text.lower()
        score = 0

        keywords = candidate.metadata.get("match_keywords") or ()
        if isinstance(keywords, (list, tuple, set)):
            for keyword in keywords:
                keyword_text = str(keyword).strip().lower()
                if not keyword_text:
                    continue
                score += normalized.count(keyword_text)

        if score == 0:
            score += normalized.count(candidate.skill_name.lower())

        return score

    def _build_skill_configs(
        self,
        candidates: list[ResolvedSkillCandidate],
        active_skill_names: tuple[str, ...],
    ) -> ResolvedSkillSet:
        active_names = set(active_skill_names)
        ordered_candidates = sorted(candidates, key=lambda item: item.skill_name)
        skill_configs = {
            candidate.skill_name: self._candidate_to_skill_config(candidate, active_names)
            for candidate in ordered_candidates
        }
        return ResolvedSkillSet(
            skill_configs=skill_configs,
            active_skill_names=active_skill_names,
            available_skill_names=tuple(skill_configs),
        )

    def _candidate_to_skill_config(
        self,
        candidate: ResolvedSkillCandidate,
        active_names: set[str],
    ) -> dict[str, Any]:
        skill_data = dict(candidate.skill_data)
        skill_data["active"] = candidate.skill_name in active_names
        skill_data.setdefault("name", candidate.skill_name)
        skill_data.setdefault("desc", skill_data.get("description", ""))
        skill_data.setdefault("skill_path", candidate.skill_path)
        return skill_data

    def _matches_patterns(self, skill_name: str, patterns: tuple[str, ...]) -> bool:
        import re

        for pattern in patterns:
            normalized = (pattern or "").strip()
            if not normalized:
                continue
            if normalized.startswith("regex:"):
                if re.search(normalized[6:], skill_name):
                    return True
                continue
            if normalized == skill_name:
                return True
        return False
