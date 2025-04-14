# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
from typing import List


class Message:
    id: str


class Messageable(object):
    __metaclass__ = abc.ABCMeta

    def __init__(self, conf, **kwargs):
        self.conf = conf

    def send(self, messages: List[Message], **kwargs):
        pass

    def receive(self, messages: List[Message], **kwargs):
        pass

    def transform(self, messages: List[Message], **kwargs):
        pass

    async def async_send(self, messages: List[Message], **kwargs):
        pass

    async def async_receive(self, messages: List[Message], **kwargs):
        pass

    async def async_transform(self, messages: List[Message], **kwargs):
        pass


class Recordable(Messageable):
    def send(self, messages: List[Message], **kwargs):
        return self.write(messages, **kwargs)

    def receive(self, messages: List[Message], **kwargs):
        return self.read(messages, **kwargs)

    async def async_send(self, messages: List[Message], **kwargs):
        return self.async_write(messages, **kwargs)

    async def async_receive(self, messages: List[Message], **kwargs):
        return self.async_read(messages, **kwargs)

    def read(self, messages: List[Message], **kwargs):
        pass

    def write(self, messages: List[Message], **kwargs):
        pass

    def async_read(self, messages: List[Message], **kwargs):
        pass

    def async_write(self, messages: List[Message], **kwargs):
        pass
