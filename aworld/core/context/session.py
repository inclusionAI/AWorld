# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import time
import uuid
from dataclasses import field
from typing import List

from pydantic import BaseModel


class Session(BaseModel):
    session_id: str = field(default_factory=lambda: str(uuid.uuid4().hex))
    last_update_time: float = time.time()
    trajectories: List = []
