from unittest.mock import AsyncMock, patch

import pytest

from aworld.config.conf import AgentConfig
from aworld.core.agent.base import BaseAgent
from aworld.core.context.base import Context
from aworld.core.event.base import Constants, Message
from aworld.output.base import StepOutput


class FailingStepAgent(BaseAgent):
    async def async_policy(self, observation, message: Message = None, **kwargs):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_async_run_closes_step_and_emits_failed_output_on_exception() -> None:
    agent = FailingStepAgent(
        name="planner",
        conf=AgentConfig(llm_provider="mock", llm_model_name="mock-model"),
    )
    context = Context(task_id="task-1")
    message = Message(
        category=Constants.AGENT,
        payload="test",
        sender="user",
        session_id="session-1",
        headers={"context": context},
    )
    sent_messages: list[Message] = []

    async def _capture(msg: Message) -> None:
        sent_messages.append(msg)

    with patch("aworld.core.agent.base.send_message", AsyncMock(side_effect=_capture)):
        with pytest.raises(RuntimeError, match="boom"):
            await agent.async_run(message)

    assert len(sent_messages) == 2
    assert isinstance(sent_messages[0].payload, StepOutput)
    assert isinstance(sent_messages[1].payload, StepOutput)
    assert sent_messages[0].payload.status == "START"
    assert sent_messages[1].payload.status == "FAILED"
    assert sent_messages[1].payload.step_id == sent_messages[0].payload.step_id
    assert context.current_step_id(namespace=agent.id()) is None
