# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import time
import uuid

from dataclasses import dataclass, field
from dataclasses_json import dataclass_json
from typing import Any


@dataclass
class DataBlock:
    """The base definition structure of AWorld data block."""
    id: str = field(default=None)
    create_at: float = field(default=time.time())
    meta_info: dict = field(default_factory=dict)


@dataclass_json
@dataclass
class Data:
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
