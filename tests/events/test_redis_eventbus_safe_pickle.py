import json
import pickle

import pytest

from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.events.redis_backend import RedisEventbus
from aworld.events.serialization import (
    ALLOWED_MODULES_ENV,
    UNSAFE_PICKLE_ENV,
    dumps_message,
    loads_message,
)


class _DisallowedCallable:
    """Stand-in for a hostile global: it lives in a module that is not allowed."""

    calls = 0

    def __reduce__(self):
        return (_record_call, ())


def _record_call():
    _DisallowedCallable.calls += 1
    return None


@pytest.fixture(autouse=True)
def _reset_disallowed_call_count():
    _DisallowedCallable.calls = 0


class _BuiltinGadget:
    """Payload reaching for a builtin that can execute code."""

    def __reduce__(self):
        return (eval, ("1 + 1",))


class _JsonGadget:
    """Harmless payload whose global is only allowed once an operator asks for it."""

    def __reduce__(self):
        return (json.loads, ('{"allowed": true}',))


def test_message_round_trip_through_event_bus_serializer():
    message = Message(headers={"context": Context(task_id="task-1")}, sender="agent-a", receiver="agent-b")

    restored = loads_message(dumps_message(message))

    assert isinstance(restored, Message)
    assert restored.id == message.id
    assert restored.sender == "agent-a"
    assert restored.receiver == "agent-b"
    assert restored.headers["context"].task_id == "task-1"


def test_disallowed_module_global_is_refused_before_it_runs():
    payload = pickle.dumps(_DisallowedCallable(), protocol=pickle.HIGHEST_PROTOCOL)

    with pytest.raises(pickle.UnpicklingError):
        loads_message(payload)

    assert _DisallowedCallable.calls == 0


def test_builtin_execution_gadget_is_refused():
    payload = pickle.dumps(_BuiltinGadget(), protocol=pickle.HIGHEST_PROTOCOL)

    with pytest.raises(pickle.UnpicklingError):
        loads_message(payload)


def test_allowlist_can_be_extended_for_trusted_payloads(monkeypatch):
    payload = pickle.dumps(_JsonGadget(), protocol=pickle.HIGHEST_PROTOCOL)

    with pytest.raises(pickle.UnpicklingError):
        loads_message(payload)

    monkeypatch.setenv(ALLOWED_MODULES_ENV, "json")

    assert loads_message(payload) == {"allowed": True}


def test_unsafe_opt_out_is_explicit(monkeypatch):
    payload = pickle.dumps(_DisallowedCallable(), protocol=pickle.HIGHEST_PROTOCOL)

    monkeypatch.setenv(UNSAFE_PICKLE_ENV, "1")
    loads_message(payload)

    assert _DisallowedCallable.calls == 1


@pytest.mark.asyncio
async def test_consume_refuses_crafted_stream_entry():
    payload = pickle.dumps(_DisallowedCallable(), protocol=pickle.HIGHEST_PROTOCOL)

    class _Client:
        async def xread(self, **kwargs):
            return [(b"stream", [(b"1-1", {b"data": payload})])]

        async def xdel(self, *args, **kwargs):
            return 1

    eventbus = RedisEventbus.__new__(RedisEventbus)
    eventbus.client = _Client()
    message = Message(headers={"context": Context(task_id="task-1")})

    with pytest.raises(pickle.UnpicklingError):
        await eventbus.consume(message)

    assert _DisallowedCallable.calls == 0
