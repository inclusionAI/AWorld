# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import signal
from typing import Union

import httpx
import uvicorn

from a2a.server.apps import JSONRPCApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.logs.util import logger
from aworld.experimental.a2a.agent_executor import AworldAgentExecutor
from aworld.experimental.a2a.config import ServingConfig, SERVER_APP_MAPPING
from aworld.core.context.base import Context


class AgentServer:
    def __init__(self, agent: Union[Agent, Swarm], config: ServingConfig):
        self.agent = agent
        self.config = config
        self.serve = None
        self.server = None

    async def start(self):
        if self.serve is None:
            await self.start_server()

        if self.config.keep_running:
            await self.serve
        return self.serve

    async def stop(self):
        if self.server is None:
            return

        await self.server.shutdown()
        self.serve.cancel()

    @property
    def address(self):
        return f"{self.config.host}:{self.config.port}"

    async def start_server(self):
        agent_card = await self._build_agent_card()
        self.agent_card = agent_card
        app = await self.create_app()
        uv_server = await self.create_server(app)

        serve = asyncio.create_task(uv_server.serve())
        while not uv_server.started:
            await asyncio.sleep(1)

        if self.config.port <= 0:
            server_port = uv_server.servers[0].sockets[0].getsockname()[1]
            self.config.port = server_port
            app.agent_card.url = f"http://{self.config.host}:{server_port}/{self.config.endpoint.lstrip('/')}"

        if self.config.server_app == "grpc":
            grpc_server = await self.create_grpc_server()
            loop = asyncio.get_running_loop()

            async def shutdown(sig: signal.Signals) -> None:
                """Gracefully shutdown the servers."""
                logger.warning(f'Received exit signal {sig.name}...')
                # Uvicorn server shutdown
                uv_server.should_exit = True

                await grpc_server.stop(5)
                logger.warning('Servers stopped.')

            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))

            await grpc_server.start()
            server_port = uv_server.servers[0].sockets[0].getsockname()[1]
            app.agent_card.url = f"{self.config.host}:{server_port + 1}/{self.config.endpoint.lstrip('/')}"
            self.agent_card.url = f"{self.config.host}:{server_port + 1}/{self.config.endpoint.lstrip('/')}"

        logger.info(f"Agent server started on {self.address} with id: {self.agent.id()}")
        self.serve = serve
        self.server = uv_server

    async def create_server(self, app: JSONRPCApplication) -> uvicorn.Server:
        root = self.config.endpoint.lstrip("/").rstrip("/")
        a2a_app = app.build()
        if self.config.server_app == "starlette":
            from starlette.applications import Starlette
            from starlette.routing import Mount

            internal_router = Starlette(routes=[Mount(f"/{root}", routes=a2a_app.routes)])
        elif self.config.server_app == "fastapi":
            from fastapi import FastAPI
            from starlette.routing import Mount

            internal_router = FastAPI(
                title="A2A Server",
                description="A2A Server",
                version=self.config.version,
                routes=[Mount(f"/{root}", routes=a2a_app.routes)]
            )
        elif self.config.server_app == "grpc":
            from starlette.applications import Starlette
            from starlette.routing import Mount, Route
            from starlette.requests import Request
            from starlette.responses import JSONResponse, Response

            from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

            def get_agent_card_http(request: Request) -> Response:
                return JSONResponse(
                    self.agent_card.model_dump(mode='json', exclude_none=True)
                )

            routes = [Route(AGENT_CARD_WELL_KNOWN_PATH, endpoint=get_agent_card_http)]
            internal_router = Starlette(routes=routes)
        else:
            raise ValueError("Unknown server app")

        config = uvicorn.Config(app=internal_router,
                                host=self.config.host,
                                port=self.config.port,
                                log_level=self.config.uvicorn_config.get("log_level", "info"))
        return uvicorn.Server(config)

    async def create_grpc_server(self):
        """Creates the gRPC server."""
        import grpc
        from a2a.grpc import a2a_pb2, a2a_pb2_grpc
        from a2a.server.request_handlers import GrpcHandler
        from grpc_reflection.v1alpha import reflection

        server = grpc.aio.server()
        a2a_pb2_grpc.add_A2AServiceServicer_to_server(
            GrpcHandler(self.agent_card, self._build_request_handler()),
            server,
        )

        SERVICE_NAMES = (
            a2a_pb2.DESCRIPTOR.services_by_name['A2AService'].full_name,
            reflection.SERVICE_NAME,
        )
        reflection.enable_server_reflection(SERVICE_NAMES, server)
        port = self.config.port + 1
        server.add_insecure_port(f'{self.config.host}:{port}')
        logger.info(f'Starting gRPC server on port {port}')
        return server

    async def create_app(self):
        handler = self._build_request_handler()

        server_app = self.config.server_app
        app = SERVER_APP_MAPPING[server_app](agent_card=self.agent_card, http_handler=handler)
        return app

    async def _build_agent_card(self) -> AgentCard:
        """Build the AgentCard for the agent server."""

        skills = self.config.skills
        agent = self.agent
        if not skills:
            skills = []
            await agent.async_desc_transform(Context())
            for tool in agent.tools:
                func_info = tool.get('function')
                if not func_info:
                    continue

                skill = AgentSkill(
                    id=f"{agent.id()}_{func_info.get('name')}",
                    name=func_info.get("name"),
                    description=func_info.get("description"),
                    tags=[],
                )
                skills.append(skill)

        if not agent.desc():
            msg = "Agent description is not set"
            raise ValueError(msg)

        endpoint = self.config.endpoint.lstrip("/")
        streaming = self.config.streaming
        return AgentCard(
            name=agent.id(),
            description=agent.desc(),
            version=self.config.version,
            default_input_modes=["text"],
            default_output_modes=["text"],
            url=f"http://{self.config.host}:{self.config.port}/{endpoint}",
            capabilities=AgentCapabilities(
                streaming=streaming, push_notifications=True, state_transition_history=False
            ),
            skills=skills,
        )

    def _build_request_handler(self):
        """Build the request handler of a2a for the server application."""

        agent_executor = AworldAgentExecutor(agent=self.agent, streaming=self.config.streaming)
        notify_config_store = self.config.notify_config_store
        notify_sender = None
        if notify_config_store and self.config.notify_sender_cls_type:
            notify_sender = self.config.notify_sender_cls_type(
                httpx_client=httpx.AsyncClient(),
                config_store=notify_config_store,
            )

        # custom
        request_handler = DefaultRequestHandler(
            agent_executor=agent_executor,
            task_store=self.config.task_store,
            push_config_store=notify_config_store,
            push_sender=notify_sender,
        )
        return request_handler
