import pytest

from examples.gaia.mcp_collections.base import ActionArguments
from examples.gaia.mcp_collections.tools.terminal import (
    TerminalActionCollection,
    _check_delayed_reminder_simulation,
)


def test_check_delayed_reminder_simulation_blocks_sleep_reminder():
    blocked, reason = _check_delayed_reminder_simulation('sleep 60 && echo "提醒我喝水"')
    assert blocked is True
    assert "cron" in reason.lower()


@pytest.mark.parametrize(
    "command",
    [
        "sleep 1",
        "sleep 1 && echo done",
        "python -c \"import time; time.sleep(1); print('done')\"",
    ],
)
def test_check_delayed_reminder_simulation_allows_normal_sleep_usage(command):
    blocked, reason = _check_delayed_reminder_simulation(command)
    assert blocked is False
    assert reason is None


@pytest.mark.asyncio
async def test_execute_command_rejects_sleep_based_reminder():
    terminal = TerminalActionCollection(ActionArguments(name="terminal", workspace=".", unittest=True))

    result = await terminal.mcp_execute_command(
        command='sleep 60 && echo "⏰ 提醒：该喝水了！💧"',
        timeout=10,
        output_format="text",
    )

    assert result.success is False
    assert result.metadata["error_type"] == "reminder_delay_blocked"
    assert "cron" in result.message.lower()


@pytest.mark.parametrize(
    "command",
    [
        'sleep 300; echo "reminder"',
        'sleep 60 && echo "提醒我提交代码" > reminder.txt',
        'sleep 60 && osascript -e \'display notification "该喝水了！" with title "喝水提醒"\'',
        'nohup bash -c \'sleep 60 && osascript -e "display notification \\"该喝水了！\\" with title \\"喝水提醒\\""\'',
        'echo "osascript -e \'display notification \\"该喝水了！\\" with title \\"提醒\\"\'" | at now + 1 minute',
    ],
)
def test_check_delayed_reminder_simulation_blocks_common_variants(command):
    blocked, reason = _check_delayed_reminder_simulation(command)
    assert blocked is True
    assert "cron" in reason.lower()
