# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.events.inmemory import InMemoryEventbus
from aworld.events.redis_backend import RedisEventbus

# global
eventbus = InMemoryEventbus()