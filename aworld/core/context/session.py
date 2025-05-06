# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from pydantic import BaseModel


class Session(BaseModel):
    session_id: str
