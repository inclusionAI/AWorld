from __future__ import annotations

from abc import ABC, abstractmethod
from typing import FrozenSet, Optional

from aworld.core.context.base import Context
from aworld.core.event.base import Message


class ExecutionScopedHook(ABC):
    """Opt-in hook mixin that requires an explicit allowed execution scope."""

    allowed_execution_scopes: FrozenSet[str] = frozenset()

    def applies_to(self, context: Optional[Context]) -> bool:
        if context is None:
            return False
        execution_scope = getattr(context, "execution_scope", None)
        if not execution_scope:
            context_info = getattr(context, "context_info", None)
            if context_info is not None and hasattr(context_info, "get"):
                execution_scope = context_info.get("execution_scope")
        return (
            isinstance(execution_scope, str)
            and execution_scope in self.allowed_execution_scopes
        )

    async def exec(
        self,
        message: Message,
        context: Optional[Context] = None,
    ) -> Message:
        if not self.applies_to(context):
            return message
        return await self._exec_scoped(message, context=context)

    @abstractmethod
    async def _exec_scoped(
        self,
        message: Message,
        context: Optional[Context] = None,
    ) -> Message:
        """Execute hook behavior after the scope predicate succeeds."""
