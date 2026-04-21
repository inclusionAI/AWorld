from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from aworld.plugins.discovery import discover_plugins
from aworld.plugins.models import PluginEntrypoint
from aworld.utils.skill_loader import collect_skill_docs


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

    def _load_candidates(
        self, request: SkillResolverRequest
    ) -> list[ResolvedSkillCandidate]:
        candidates: list[ResolvedSkillCandidate] = []
        seen: set[str] = set()

        for plugin in discover_plugins(request.plugin_roots):
            plugin_root = Path(plugin.manifest.plugin_root)
            for entrypoint in plugin.manifest.entrypoints.get("skills", ()):
                candidate = self._candidate_from_entrypoint(plugin_root, entrypoint)
                if candidate is None or candidate.skill_name in seen:
                    continue
                candidates.append(candidate)
                seen.add(candidate.skill_name)

        compatibility_patterns = tuple(request.compatibility_skill_patterns)
        for source in request.compatibility_sources:
            for candidate in self._load_compatibility_candidates(
                source, compatibility_patterns
            ):
                if candidate.skill_name in seen:
                    continue
                candidates.append(candidate)
                seen.add(candidate.skill_name)

        return candidates

    def _candidate_from_entrypoint(
        self, plugin_root: Path, entrypoint: PluginEntrypoint
    ) -> ResolvedSkillCandidate | None:
        skill_path = self._resolve_entrypoint_skill_path(plugin_root, entrypoint)
        if skill_path is None:
            return None

        skill_data = self._load_skill_data(skill_path)
        if skill_data is None:
            return None

        return ResolvedSkillCandidate(
            skill_name=entrypoint.entrypoint_id,
            skill_path=str(skill_path),
            scope=str(entrypoint.scope or "workspace").strip().lower() or "workspace",
            visibility=str(entrypoint.visibility or "public").strip().lower() or "public",
            metadata=dict(entrypoint.metadata or {}),
            skill_data=skill_data,
        )

    def _load_compatibility_candidates(
        self, source: str, patterns: tuple[str, ...]
    ) -> Iterable[ResolvedSkillCandidate]:
        for skill_name, skill_data in collect_skill_docs(source).items():
            if patterns and not self._matches_patterns(skill_name, patterns):
                continue
            skill_path = skill_data.get("skill_path")
            if not isinstance(skill_path, str) or not skill_path:
                continue
            yield ResolvedSkillCandidate(
                skill_name=skill_name,
                skill_path=skill_path,
                scope="global",
                visibility="public",
                metadata={},
                skill_data=dict(skill_data),
            )

    def _resolve_entrypoint_skill_path(
        self, plugin_root: Path, entrypoint: PluginEntrypoint
    ) -> Path | None:
        target = (entrypoint.target or "").strip()
        if target:
            candidate = (plugin_root / target).resolve()
            if candidate.is_file():
                return candidate

        fallback_paths = (
            plugin_root / "skills" / entrypoint.entrypoint_id / "SKILL.md",
            plugin_root / entrypoint.entrypoint_id / "SKILL.md",
        )
        for candidate in fallback_paths:
            if candidate.is_file():
                return candidate.resolve()

        return None

    def _load_skill_data(self, skill_path: Path) -> dict[str, Any] | None:
        loaded = collect_skill_docs(skill_path.parent)
        if not loaded:
            return None
        if skill_path.parent.name in loaded:
            return dict(loaded[skill_path.parent.name])
        first_name = next(iter(loaded))
        return dict(loaded[first_name])

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
