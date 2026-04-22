from __future__ import annotations

from aworld.core.event.base import Constants, Message
from aworld.runners import HandlerFactory
from aworld.runners.handler.base import DefaultHandler
from aworld.runners.handler.human import DefaultHumanHandler


class AcpRequiresHumanError(RuntimeError):
    """Raised when ACP mode hits a human-in-loop boundary."""


@HandlerFactory.register(name=f"__{Constants.HUMAN}__", prio=1000)
class AcpHumanInterceptHandler(DefaultHumanHandler):
    def __init__(self, runner):
        DefaultHandler.__init__(self, runner)
        self.swarm = getattr(runner, "swarm", None)
        self.endless_threshold = getattr(runner, "endless_threshold", None)
        self.task_id = getattr(getattr(runner, "task", None), "id", None)
        self.agent_calls: list[str] = []

    async def handle_user_input(self, message: Message):
        raise AcpRequiresHumanError(
            "Human approval/input flow is not bridged in ACP mode."
        )
