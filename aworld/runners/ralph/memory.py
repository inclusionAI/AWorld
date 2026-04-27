import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from aworld.output import Artifact, ArtifactType

if TYPE_CHECKING:
    from aworld.runners.ralph.state import LoopContext


class LoopMemoryStore:
    """Thin adapter over existing workspace artifacts and sandbox file access."""

    def __init__(self, context: "LoopContext"):
        self.context = context

    def iteration_summary_artifact_id(self, task_id: str, iteration: int) -> str:
        return self._artifact_id(self.context.summary_dir(), task_id, iteration)

    def reflection_feedback_artifact_id(self, task_id: str, iteration: int) -> str:
        return self._artifact_id(self.context.reflect_dir(), task_id, iteration)

    def answer_path(self, task_id: str, iteration: int) -> Path:
        return self.context.answer_dir() / f"{task_id}_{iteration}"

    async def write_iteration_summary(self, task_id: str, iteration: int, text: str) -> None:
        await self._write_text_artifact(
            artifact_id=self.iteration_summary_artifact_id(task_id, iteration),
            text=text,
            metadata={
                "task_id": task_id,
                "iteration": iteration,
                "kind": "iteration_summary",
            },
        )

    async def read_iteration_summary(self, task_id: str, iteration: int) -> Optional[str]:
        return self._read_text_artifact(self.iteration_summary_artifact_id(task_id, iteration))

    async def write_reflection_feedback(self, task_id: str, iteration: int, text: str) -> None:
        await self._write_text_artifact(
            artifact_id=self.reflection_feedback_artifact_id(task_id, iteration),
            text=text,
            metadata={
                "task_id": task_id,
                "iteration": iteration,
                "kind": "reflection_feedback",
                "timestamp": time.time(),
            },
        )

    async def read_reflection_feedback(self, task_id: str, iteration: int) -> Optional[str]:
        return self._read_text_artifact(self.reflection_feedback_artifact_id(task_id, iteration))

    async def write_answer(self, task_id: str, iteration: int, content: Any) -> None:
        await self.write_answer_file(f"{task_id}_{iteration}", content)

    async def write_answer_file(self, filename: str, content: Any) -> None:
        payload = self._serialize_answer_payload(content)
        await self.context.sand_box.file.write_file(path=str(self.context.answer_dir() / filename), content=payload)

    async def read_answer(self, task_id: str, iteration: int) -> Optional[Any]:
        result = await self.context.sand_box.file.read_file(path=str(self.answer_path(task_id, iteration)))
        data = result.get("data")
        if data is None:
            return None

        if isinstance(data, str):
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                return data
        else:
            parsed = data

        if isinstance(parsed, dict) and "content" in parsed:
            return parsed["content"]
        return parsed

    def _artifact_id(self, base_dir: Path, task_id: str, iteration: int) -> str:
        return f"{base_dir}_{task_id}_{iteration}"

    async def _write_text_artifact(self, artifact_id: str, text: str, metadata: dict[str, Any]) -> None:
        artifact = Artifact(
            artifact_id=artifact_id,
            artifact_type=ArtifactType.TEXT,
            content=text,
            metadata=metadata,
        )
        await self.context.workspace.add_artifact(artifact, index=False)

    def _read_text_artifact(self, artifact_id: str) -> Optional[str]:
        artifact_data = self.context.workspace.get_artifact_data(artifact_id)
        if not artifact_data:
            return None
        return artifact_data.get("content")

    def _serialize_answer_payload(self, content: Any) -> str:
        if isinstance(content, str):
            return content

        return json.dumps({"content": content}, ensure_ascii=False)
