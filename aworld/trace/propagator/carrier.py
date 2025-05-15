from typing import TypeVar
from aworld.trace.base import Carrier
from aworld.logs.util import logger

T = TypeVar("T")


class ResponseCarrier(Carrier):

    def __init__(self, response_headers: list[tuple[str, T]]):
        self.response_headers = response_headers

    def get(self, key: str) -> T:
        for header, value in self.response_headers:
            if header.lower() == key.lower():
                return value
        return None

    def set(self, key: str, value: T) -> None:
        for i, (header, _) in enumerate(self.response_headers):
            if header.lower() == key.lower():
                self.response_headers[i] = (header, value)
                return
        self.response_headers.append((key, value))


class RequestCarrier(Carrier):
    def __init__(self, request_headers: dict[str, T]):
        self.request_headers = request_headers

    def get(self, key: str) -> T:
        return self.request_headers.get(key)

    def set(self, key: str, value: T) -> None:
        logger.info(f"set request header {key}={value}")
        self.request_headers[key] = value
