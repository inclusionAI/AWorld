import pytest

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.context.base import Context
from aworld.core.tool.surface import (
    RequiredToolSurfaceUnavailable,
    ToolCapabilitySpec,
    ToolSurfaceReceipt,
)


def _schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_agent_records_receipt_from_final_live_schemas() -> None:
    agent = Agent(
        name="surface-agent",
        conf=AgentConfig(),
        tool_names=[],
        tool_surface_specs=(
            ToolCapabilitySpec(
                capability_id="terminal",
                schema_ids=("run_code",),
                required=True,
            ),
        ),
    )
    agent.tools = [_schema("run_code")]
    context = Context()

    agent._reconcile_live_tool_surface(context)

    assert agent.tool_surface_receipt.ready is True
    stored = context.context_info.get("tool_surface_receipts")
    assert ToolSurfaceReceipt.from_dict(stored[agent.id()]).ready is True


def test_agent_fails_closed_when_required_schema_is_missing() -> None:
    agent = Agent(
        name="surface-agent",
        conf=AgentConfig(),
        tool_names=[],
        tool_surface_specs=(
            ToolCapabilitySpec(
                capability_id="terminal",
                schema_ids=("run_code",),
                required=True,
            ),
        ),
    )
    agent.tools = []

    with pytest.raises(RequiredToolSurfaceUnavailable) as raised:
        agent._reconcile_live_tool_surface(Context())

    assert raised.value.receipt.ready is False
    assert "terminal:schema_missing" in str(raised.value)
