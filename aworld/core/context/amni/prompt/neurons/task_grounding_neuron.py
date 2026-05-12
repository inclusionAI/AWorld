from typing import List

from ... import ApplicationContext
from ...utils.context_tree_utils import format_task_content
from . import Neuron
from .neuron_factory import neuron_factory

TASK_GROUNDING_NEURON_NAME = "task_grounding"


@neuron_factory.register(
    name=TASK_GROUNDING_NEURON_NAME,
    desc="Task grounding rules derived from the authoritative user request",
    prio=45,
)
class TaskGroundingNeuron(Neuron):
    def __init__(self):
        self.name = TASK_GROUNDING_NEURON_NAME

    def _authoritative_request(self, context: ApplicationContext) -> str:
        return format_task_content(
            getattr(context, "origin_user_input", None) or getattr(context, "task_input", None)
        ).strip()

    def _current_task_view(self, context: ApplicationContext) -> str:
        return format_task_content(getattr(context, "task_input", None)).strip()

    async def format_items(
        self,
        context: ApplicationContext,
        namespace: str = None,
        **kwargs,
    ) -> List[str]:
        authoritative_request = self._authoritative_request(context)
        return [authoritative_request] if authoritative_request else []

    async def format(
        self,
        context: ApplicationContext,
        items: List[str] = None,
        namespace: str = None,
        **kwargs,
    ) -> str:
        if not items:
            items = await self.format_items(context, namespace, **kwargs)

        if not items:
            return ""

        authoritative_request = items[0]
        current_task_view = self._current_task_view(context)
        current_task_block = ""
        if current_task_view and current_task_view != authoritative_request:
            current_task_block = f"\nCurrent task view:\n- {current_task_view}\n"

        return f"""
## Task Grounding

Authoritative user request:
- {authoritative_request}
{current_task_block}
Grounding rules:
- Treat the authoritative user request as the fixed source of truth for the goal, target, scope, output, and success bar.
- Do not silently change named entities, handles, URLs, file paths, dates, time windows, topic filters, or requested deliverables.
- If current evidence points to a different target or scope, do not reinterpret the task to fit that evidence.
- Before claiming success, verify the final result matches the authoritative request using evidence from this run.
- If evidence is missing or conflicts with the authoritative request, keep working or report the mismatch instead of declaring success.

---
"""

    async def desc(
        self,
        context: ApplicationContext,
        namespace: str = None,
        **kwargs,
    ) -> str:
        return "Task grounding rules derived from the authoritative user request"
