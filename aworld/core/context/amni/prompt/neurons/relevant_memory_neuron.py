import os
from typing import List

from ... import ApplicationContext
from . import Neuron
from .neuron_factory import neuron_factory
from aworld.logs.util import logger
from aworld.memory.main import MemoryFactory

RELEVANT_MEMORY_NEURON_NAME = "relevant_memory"


@neuron_factory.register(
    name=RELEVANT_MEMORY_NEURON_NAME,
    desc="Relevant durable memory recall from workspace session logs",
    prio=55,
)
class RelevantMemoryNeuron(Neuron):
    def _workspace_path(self, context: ApplicationContext) -> str:
        return (
            getattr(context, "workspace_path", None)
            or getattr(context, "working_directory", None)
            or os.getcwd()
        )

    def _query_text(self, context: ApplicationContext) -> str:
        return str(getattr(context, "task_input", "") or getattr(context, "origin_user_input", "") or "")

    async def format_items(
        self,
        context: ApplicationContext,
        namespace: str = None,
        **kwargs,
    ) -> List[str]:
        try:
            memory = MemoryFactory.instance()
            if not hasattr(memory, "get_relevant_memory_context"):
                return []
            recall = memory.get_relevant_memory_context(
                self._workspace_path(context),
                query=self._query_text(context),
            )
            return list(getattr(recall, "texts", ()) or ())
        except Exception as exc:
            logger.warning(f"RelevantMemoryNeuron recall failed: {exc}")
            return []

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

        lines = "\n".join(f"- {item}" for item in items)
        return f"""
## Relevant Memory Recall

{lines}

---
"""

    async def desc(
        self,
        context: ApplicationContext,
        namespace: str = None,
        **kwargs,
    ) -> str:
        return "Relevant durable memories recalled from workspace session logs"
