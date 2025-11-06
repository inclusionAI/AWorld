# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.events.inmemory import InMemoryEventbus
from aworld.events.redis_backend import RedisEventbus

# global - use named instances to avoid singleton collision
# 'main' eventbus for normal event handling
eventbus = InMemoryEventbus.get_instance(name='main')
# 'streaming' eventbus will be initialized on-demand for streaming mode
streaming_eventbus = None