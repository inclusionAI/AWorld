from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aworld.core.event.base import Constants, Message, TopicType


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.human_intercept import AcpHumanInterceptHandler, AcpRequiresHumanError


@pytest.mark.asyncio
async def test_acp_human_handler_fails_instead_of_waiting_for_terminal_input() -> None:
    handler = AcpHumanInterceptHandler(runner=object())
    message = Message(
        category=Constants.HUMAN,
        topic=TopicType.HUMAN_CONFIRM,
        payload="1|approve?",
    )

    with pytest.raises(AcpRequiresHumanError):
        await handler.handle_user_input(message)
