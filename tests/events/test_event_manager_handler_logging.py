from aworld.core.context.base import Context
from aworld.core.event.base import Constants
from aworld.events.manager import EventManager
from aworld.events import manager as manager_module


def test_get_handlers_suppresses_expected_passthrough_event_noise(monkeypatch):
    entries = []

    def fake_info(message):
        entries.append(message)

    monkeypatch.setattr(manager_module.logger, "info", fake_info)

    event_manager = EventManager(Context(task_id="task-1"))

    assert not event_manager.get_handlers(Constants.CHUNK)
    assert not event_manager.get_handlers(Constants.OUTPUT)
    assert not event_manager.get_handlers(Constants.MEMORY)
    assert not event_manager.get_handlers(Constants.CONTEXT)
    assert not event_manager.get_handlers(Constants.CONTEXT_RESPONSE)
    assert not event_manager.get_handlers(Constants.TASK)
    assert not event_manager.get_handlers(Constants.TOOL_CALLBACK)

    assert entries == []


def test_get_handlers_keeps_missing_handler_signal_for_routable_events(monkeypatch):
    entries = []

    def fake_info(message):
        entries.append(message)

    monkeypatch.setattr(manager_module.logger, "info", fake_info)

    event_manager = EventManager(Context(task_id="task-2"))

    assert not event_manager.get_handlers(Constants.AGENT)

    assert entries == [
        "Task task-2 has no registered handlers with agent event_type."
    ]
