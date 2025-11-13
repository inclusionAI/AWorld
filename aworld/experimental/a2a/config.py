# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Type

from a2a.server.apps import A2AFastAPIApplication, A2AStarletteApplication
from a2a.server.tasks import PushNotificationConfigStore, TaskStore, BasePushNotificationSender, InMemoryTaskStore
from a2a.types import AgentSkill, TransportProtocol, PushNotificationConfig
from a2a.client.optionals import Channel
from a2a.client.client import Consumer
from collections.abc import Callable

from aworld.config import BaseConfig
from aworld.core.common import StreamingMode

SERVER_APP_MAPPING = {"fastapi": A2AFastAPIApplication,
                      "starlette": A2AStarletteApplication,
                      # grpc use starlette for http server
                      "grpc": A2AStarletteApplication}


class ServingConfig(BaseConfig):
    model_config = {"extra": "ignore", "arbitrary_types_allowed": True}

    host: str = "localhost"
    port: int = 0
    endpoint: str = "/"
    streaming: bool = False
    serving_type: str = "a2a"  # options: a2a, mcp
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


class ClientConfig(BaseConfig):
    streaming: bool = False
    streaming_mode: str = StreamingMode.OUTPUT.value
    # Whether client prefers to poll for updates from message:send. It is the callers job to check if the response is completed and if not run a polling loop.
    polling: bool = False
    timeout: float = 600.0
    supported_transports: list[TransportProtocol] = [TransportProtocol.jsonrpc]
    grpc_channel_factory: Callable[[str], Channel] | None = None
    # Whether to use client transport preferences over server preferences. Recommended to use server preferences in most situations.
    use_client_preference: bool = False
    accepted_output_modes: list[str] = []
    push_notification_configs: list[PushNotificationConfig] = []
    consumers: list[Consumer] = []
