from types import SimpleNamespace

import pytest

from aworld.core.event.base import Message


class _Agent:
    def __init__(self, name: str):
        self._name = name

    def name(self):
        return self._name


@pytest.mark.asyncio
async def test_pre_llm_cost_hook_resolves_current_agent_for_llm_sender(monkeypatch, tmp_path, caplog):
    from aworld_cli.executors import pre_llm_cost_hook
    from aworld_cli.executors.pre_llm_cost_hook import PreLlmCostHook

    history_path = tmp_path / "cli_history.jsonl"
    history_path.write_text("", encoding="utf-8")
    looked_up = []

    def fake_agent_instance(name):
        looked_up.append(name)
        if name == "real_agent":
            return _Agent("Real Agent")
        return None

    class FakeHistory:
        def __init__(self, path):
            self.path = path

        def format_cost_display(self, session_id=None):
            return "cost display"

    monkeypatch.setattr(pre_llm_cost_hook.AgentFactory, "agent_instance", fake_agent_instance)
    monkeypatch.setattr(
        "aworld_cli.core.context.get_default_history_path",
        lambda: history_path,
    )
    monkeypatch.setattr(
        "aworld_cli.core.context.check_session_token_limit",
        lambda **kwargs: (False, {}, 0),
    )
    monkeypatch.setattr("aworld_cli.history.JSONLHistory", FakeHistory)

    context = SimpleNamespace(
        session_id="sess-1",
        agent_info=SimpleNamespace(current_agent_id="real_agent"),
    )
    message = Message(
        category="agent_hook",
        payload={"event": "before_llm_call"},
        sender="llm_model",
        headers={},
    )

    result = await PreLlmCostHook().exec(message, context=context)

    assert result is message
    assert looked_up == ["real_agent"]
    assert "Agent llm_model not found" not in caplog.text


@pytest.mark.asyncio
async def test_pre_llm_cost_hook_skips_llm_sender_without_current_agent(monkeypatch, caplog):
    from aworld_cli.executors import pre_llm_cost_hook
    from aworld_cli.executors.pre_llm_cost_hook import PreLlmCostHook

    looked_up = []

    def fake_agent_instance(name):
        looked_up.append(name)
        return None

    monkeypatch.setattr(pre_llm_cost_hook.AgentFactory, "agent_instance", fake_agent_instance)

    message = Message(
        category="agent_hook",
        payload={"event": "before_llm_call"},
        sender="llm_model",
        headers={},
    )

    result = await PreLlmCostHook().exec(message, context=SimpleNamespace(agent_info=None))

    assert result is message
    assert looked_up == []
    assert "Agent llm_model not found" not in caplog.text
