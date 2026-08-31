import pytest

from aworld.core.event.base import Message
from aworld.core.context.base import Context
from aworld.events.redis_backend import RedisEventbus


@pytest.mark.asyncio
async def test_redis_publish_propagates_transport_failure():
    class _Client:
        async def xadd(self, **kwargs):
            raise ConnectionError("redis unavailable")

    eventbus = RedisEventbus.__new__(RedisEventbus)
    eventbus.client = _Client()
    message = Message(headers={"context": Context(task_id="task-1")})

    with pytest.raises(ConnectionError, match="redis unavailable"):
        await eventbus.publish(message)
