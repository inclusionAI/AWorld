# coding: utf-8
# Copyright (c) 2025 inclusionAI.

class Message:
    id: str


class Messageable(object):
    def __init__(self, conf, **kwargs):
        self.conf = conf

    def send(self, ):
        pass

    def receive(self, ):
        pass

    def transform(self, messages):
        pass

    async def async_send(self, ):
        pass

    async def async_receive(self, ):
        pass

    async def async_transform(self, messages, **kwargs):
        pass


class Recordable(Messageable):
    def send(self, ):
        return self.write()

    def receive(self, ):
        return self.read()

    async def async_send(self, ):
        return self.async_write()

    async def async_receive(self, ):
        pass

    def read(self):
        pass

    def write(self):
        pass

    def async_read(self):
        pass

    def async_write(self):
        pass
