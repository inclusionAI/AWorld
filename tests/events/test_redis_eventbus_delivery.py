import pytest

import aworld.events.redis_backend as redis_backend
from aworld.core.event.base import Constants, Message
from aworld.core.context.base import Context
from aworld.events.redis_backend import RedisEventbus


@pytest.mark.asyncio
async def test_redis_publish_propagates_transport_failure(monkeypatch):
    class _Client:
        async def xadd(self, **kwargs):
            raise ConnectionError("redis unavailable")

    eventbus = RedisEventbus.__new__(RedisEventbus)
    eventbus.client = _Client()
    message = Message(headers={"context": Context(task_id="task-1")})
    monkeypatch.setattr(redis_backend.pickle, "dumps", lambda _message: b"payload")

    with pytest.raises(ConnectionError, match="redis unavailable"):
        await eventbus.publish(message)


@pytest.mark.asyncio
async def test_redis_chunk_publish_has_no_info_log(monkeypatch):
    class _Client:
        async def xadd(self, **kwargs):
            return "redis-message-1"

    info_logs = []
    debug_logs = []
    monkeypatch.setattr(redis_backend.pickle, "dumps", lambda _message: b"payload")
    monkeypatch.setattr(redis_backend.logger, "info", info_logs.append)
    monkeypatch.setattr(redis_backend.logger, "debug", debug_logs.append)
    eventbus = RedisEventbus.__new__(RedisEventbus)
    eventbus.client = _Client()
    message = Message(
        category=Constants.CHUNK,
        headers={"context": Context(task_id="task-1")},
    )

    assert await eventbus.publish(message) == "redis-message-1"
    assert info_logs == []
    assert len(debug_logs) == 2
    assert all("stream chunk" in entry for entry in debug_logs)
