from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

from aworld.runners.ralph.policy import RalphLoopPolicy

if TYPE_CHECKING:
    from aworld.runners.ralph.memory import LoopMemoryStore


DEFAULT_REFLECTION_FEEDBACK = (
    "Your previous answer was incorrect. Please read the original question carefully, "
    "check and analyze it, and try to answer it again."
)


@dataclass
class IterationInput:
    task_input: str
    reuse_context: bool


class IterationInputBuilder:
    def __init__(self, policy: RalphLoopPolicy, memory_store: "LoopMemoryStore"):
        self.policy = policy
        self.memory_store = memory_store

    async def build(
        self,
        task_id: str,
        original_task: str,
        iteration: int,
        previous_answer: Optional[str] = None,
        reflection_feedback: Optional[str] = None,
        attempt_receipt: Optional[dict[str, Any]] = None,
    ) -> IterationInput:
        if iteration <= 1:
            return IterationInput(
                task_input=original_task,
                reuse_context=self.policy.execution_mode == "reuse_context",
            )

        if previous_answer is None:
            previous_answer = await self.memory_store.read_answer(task_id, iteration - 1)
        if reflection_feedback is None:
            reflection_feedback = await self.memory_store.read_reflection_feedback(task_id, iteration - 1)
        if attempt_receipt is None:
            metadata = getattr(
                getattr(self.memory_store.context, "loop_state", None),
                "metadata",
                {},
            )
            candidate = metadata.get("last_attempt_receipt") if isinstance(metadata, dict) else None
            attempt_receipt = candidate if isinstance(candidate, dict) else None

        return IterationInput(
            task_input=self._compose_task_input(
                original_task=original_task,
                iteration=iteration,
                previous_answer=previous_answer,
                reflection_feedback=reflection_feedback,
                attempt_receipt=attempt_receipt,
            ),
            reuse_context=self.policy.execution_mode == "reuse_context",
        )

    def _compose_task_input(
        self,
        original_task: str,
        iteration: int,
        previous_answer: Optional[str],
        reflection_feedback: Optional[str],
        attempt_receipt: Optional[dict[str, Any]],
    ) -> str:
        sections: list[str] = [f"Iteration: {iteration}", ""]

        if self.policy.execution_mode == "fresh_context":
            sections[0:0] = ["Original task:", original_task, ""]

        sections.extend(
            [
                "Previous answer summary:",
                previous_answer or "No previous answer available.",
                "",
                "Previous attempt receipt:",
                self._format_attempt_receipt(attempt_receipt),
                "",
                "Reflection feedback:",
                reflection_feedback or DEFAULT_REFLECTION_FEEDBACK,
                "",
                "Execution rule for the next step:",
                self._execution_rule(),
            ]
        )
        return "\n".join(sections).strip()

    @staticmethod
    def _format_attempt_receipt(receipt: Optional[dict[str, Any]]) -> str:
        if not receipt:
            return "No structured receipt available."
        values = []
        for key in (
            "semantic_succeeded",
            "completion_claimed",
            "verification_required",
            "verification_passed",
            "acceptance_satisfied",
            "disposition",
            "decision_reason",
        ):
            value = receipt.get(key)
            if isinstance(value, (str, bool)) or value is None:
                values.append(f"- {key}: {value}")
        return "\n".join(values) or "No structured receipt available."

    def _execution_rule(self) -> str:
        if self.policy.execution_mode == "reuse_context":
            return "Continue from the existing task context and incorporate the persisted feedback."
        return "Start from a fresh task context and rely only on the persisted loop memory above."
