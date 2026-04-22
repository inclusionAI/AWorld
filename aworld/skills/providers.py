from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable

from aworld.skills.models import SkillContent, SkillDescriptor


class SkillProvider(ABC):
    @abstractmethod
    def provider_id(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def list_descriptors(self) -> Iterable[SkillDescriptor]:
        raise NotImplementedError

    @abstractmethod
    def load_content(self, skill_id: str) -> SkillContent:
        raise NotImplementedError

    @abstractmethod
    def resolve_asset_path(self, skill_id: str, relative_path: str) -> Path:
        raise NotImplementedError
