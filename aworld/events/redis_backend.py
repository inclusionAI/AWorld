# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import pickle
import traceback

from aworld.config import BaseConfig
from aworld.core.event.base import Message
from aworld.events import InMemoryEventbus
from aworld.logs.util import logger


class RedisConfig(BaseConfig):
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = ""
    user_name: str = ""


class RedisEventbus(InMemoryEventbus):
    def __init__(self, conf: RedisConfig = None, **kwargs):
        import aioredis

        super().__init__(conf, **kwargs)

        con_url = f"redis://{conf.user_name}:{conf.password}@{conf.host}:{conf.port}"
        self.client = aioredis.from_url(con_url, db=conf.db)

    async def wait_consume_size(self, id: str) -> int:
        # not really reserved data size
        return await self.client.dbsize()

    async def publish(self, message: Message, **kwargs):
        logger.info(f"publish message: {message} of task: {message.task_id}")

        name = message.task_id
        try:
            data = {"data": pickle.dumps(message)}
            msg_id = await self.client.xadd(name=name, id="*", fields=data)
            logger.info(f"redis add id {msg_id} to {name} channel.")
            return msg_id
        except Exception:
            logger.error(f"Error sending msg to redis eventbus, {message}\n{traceback.format_exc()}")

    async def consume(self, message: Message = None, **kwargs):
        name = message.task_id
        response = await self.client.xread(streams={name: '0-0'}, count=1, block=0)
        for stream, msgs in response:
            for msg in msgs:
                message_id = msg[0]
                message_content = msg[1]
                data = pickle.loads(message_content.get(b"data"))
                logger.info(f"Get message: {message_id} with content {data}")
                await self.client.xdel(name, message_id)
                return data
        return None

    async def consume_nowait(self, message: Message = None):
        response = await self.client.xread(streams={message.task_id: '0-0'}, count=1, block=1)
        for stream, msgs in response:
            for msg in msgs:
                message_id = msg[0]
                message_content = msg[1]
                data = pickle.loads(message_content.get(b"data"))
                logger.debug(f"Get message: {message_id} with content {data}")
                await self.client.xdel(message.task_id, message_id)
                return data
        return None

    async def done(self, id: str):
        while True:
            response = await self.client.xread(streams={id: '0-0'}, count=10, block=1)
            if response:
                for stream, msgs in response:
                    [await self.client.xdel(id, msg[0]) for msg in msgs]
            else:
                break
