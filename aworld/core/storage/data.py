# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from aworld.utils.serialized_util import to_serializable


@dataclass
class DataBlock:
    """The base definition structure of AWorld data block."""
    id: str = field(default=None)
    create_at: float = field(default=time.time())
    meta_info: dict = field(default_factory=dict)


class Data(BaseModel):
    """The base definition structure of AWorld data storage."""
    block_id: str = field(default=None)
    id: str = field(default=uuid.uuid4().hex)
    value: Any = field(default=None)
    create_at: float = field(default=time.time())
    update_at: float = field(default=time.time())
    expires_at: float = field(default=0)
    meta_info: dict = field(default_factory=dict)

    def __eq__(self, other: 'Data'):
        return self.id == other.id

    def model_dump(self):
        return self.to_dict()

    @staticmethod
    def from_dict(data: dict) -> 'Data':
        return Data(**data)

    def to_dict(self):
        return {
            "block_id": self.block_id,
            "id": self.id,
            "value": to_serializable(self.value) if self.value is not None else None,
            "create_at": self.create_at,
            "update_at": self.update_at,
            "expires_at": self.expires_at,
            "meta_info": self.meta_info
        }
