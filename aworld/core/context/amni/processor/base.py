from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from pydantic import BaseModel

from aworld.output import Artifact


class BaseArtifactProcessor(BaseModel, ABC):
    @abstractmethod
    def process_artifacts(
            self,
            artifacts: List[Artifact],
            query: str,
    ) -> List[Artifact]:
        pass

    async def aprocess_artifacts(
            self,
            artifacts: List[Artifact],
            query: str,
    ) -> List[Artifact]:
        return self.process_artifacts(artifacts, query)
