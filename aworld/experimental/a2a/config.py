# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Type, Union

from a2a.server.apps import A2AFastAPIApplication, A2AStarletteApplication
from a2a.server.tasks import PushNotificationConfigStore, TaskStore, BasePushNotificationSender, InMemoryTaskStore
from a2a.types import AgentSkill

from aworld.config import BaseConfig

SERVER_APP_MAPPING = {"fastapi": A2AFastAPIApplication, "starlette": A2AStarletteApplication, "grpc": None}


class ServingConfig(BaseConfig):
    model_config = {"extra": "ignore", "arbitrary_types_allowed": True}

    host: str = "localhost"
    port: int = 0
    endpoint: str = "/"
    streaming: bool = False
    keep_running: bool = True
    skills: list[AgentSkill] = []
    input_modes: list[str] = ["text"]
    output_modes: list[str] = ["text"]
    notify_config_store: PushNotificationConfigStore = None
    notify_sender_cls_type: Type[BasePushNotificationSender] | None = None
    task_store: TaskStore = InMemoryTaskStore()
    server_app: str = "fastapi"  # Options: "fastapi", "starlette" or "grpc"
    uvicorn_config: dict = {}
    version: str = "0.0.1"
