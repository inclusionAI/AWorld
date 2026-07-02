from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def default_skill_home() -> Path:
    return Path.home() / ".aworld" / "skills"


def default_skill_state_path() -> Path:
    return default_skill_home() / ".skill-state.json"


class SkillStateManager:
    def __init__(self, state_path: Path | None = None) -> None:
        self.state_path = (state_path or default_skill_state_path()).expanduser()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def disabled_skill_names(self) -> tuple[str, ...]:
        payload = self._load()
        raw_names = payload.get("disabled_skills", [])
        if not isinstance(raw_names, list):
            return tuple()

        ordered: list[str] = []
        seen: set[str] = set()
        for item in raw_names:
            normalized = self._normalize_skill_name(item)
            if not normalized or normalized in seen:
                continue
            ordered.append(normalized)
            seen.add(normalized)
        return tuple(ordered)

    def is_enabled(self, skill_name: str) -> bool:
        return self._normalize_skill_name(skill_name) not in set(
            self.disabled_skill_names()
        )

    def enable_skill(self, skill_name: str) -> None:
        target = self._normalize_skill_name(skill_name)
        disabled = [
            item for item in self.disabled_skill_names() if item != target
        ]
        self._save({"disabled_skills": disabled})

    def disable_skill(self, skill_name: str) -> None:
        target = self._normalize_skill_name(skill_name)
        disabled = list(self.disabled_skill_names())
        if target and target not in disabled:
            disabled.append(target)
        self._save({"disabled_skills": disabled})

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if isinstance(payload, dict):
            return payload
        return {}

    def _save(self, payload: dict[str, Any]) -> None:
        self.state_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _normalize_skill_name(self, value: object) -> str:
        normalized = str(value or "").strip().lower()
        return normalized
