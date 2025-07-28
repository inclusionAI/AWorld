# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import time
import uuid
from dataclasses import field
from typing import List, Optional

from aworld.memory.models import MemoryMessage
from pydantic import BaseModel, Field


class Session(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4().hex))
    last_update_time: float = time.time()
    trajectories: List = []
    # Process Log: Session History of actions and reasoning steps
    history_messages: Optional[list[MemoryMessage]] = []
