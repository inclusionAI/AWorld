import uvicorn
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from aworld.experimental.bidi_streaming.config import ServingConfig
from aworld.logs.util import logger
from aworld.experimental.bidi_streaming.transport import Transport, WebSocketTransport, BidiMessage
from aworld.experimental.bidi_streaming.session import InMemoryBidiSessionService

from aworld.session.base_session_service import BaseSessionService
from aworld.core.task import Task
from aworld.events.inmemory import InMemoryEventbus
from aworld.events import streaming_eventbus
from aworld.runner import Runners


class StreamingServer():

    def __init__(self, config: ServingConfig, session_service: BaseSessionService = None):
        self.config = config
        self.serve = None
        self.session_service = session_service or InMemoryBidiSessionService()

    async def start_server(self):
        uv_server = await self.create_fastapi_server()
        serve = asyncio.create_task(uv_server.serve())
        while not uv_server.started:
            await asyncio.sleep(1)

        logger.info(f"Streaming server started on {self.config.host}:{self.config.port}")
        self.serve = serve

    async def create_fastapi_server(self) -> uvicorn.Server:
        app = FastAPI(title="Bidi Streaming Server",
                      description="Bidi Streaming Server")
        app.websocket("/ws/create_session/{user_id}/{session_id}")(self.ws_connect)

        config = uvicorn.Config(app, host=self.config.host, port=self.config.port, **self.config.uvicorn_config)
        server = uvicorn.Server(config)
        return server

    async def ws_connect(self, websocket: WebSocket, user_id: str, session_id: str = None):
        """WebSocket endpoint handler for agent communication."""
        transport = WebSocketTransport(websocket)

        message_task = None
        try:
            if session_id:
                session = await self.session_service.get_session(user_id, session_id)
                if not session:
                    raise ValueError(f"Session {session_id} does not exist")
            else:
                session = await self.session_service.create_session(user_id, transport)

            task = Task(user_id=user_id, session_id=session.session_id)

            await transport.connect()

            async def _message_handler_warpper(task: Task):
                """Handle incoming messages from clients."""

                async def _handle_message(streaming_eventbus):
                    while transport.is_connected:
                        # Receive message from client
                        bidi_message: BidiMessage = await transport.receive()
                        bidi_message.task_id = task.id
                        await streaming_eventbus.publish(bidi_message)
                        # Small delay to prevent busy looping
                        await asyncio.sleep(0.01)

                return _handle_message

            # message_task = asyncio.create_task(self._message_handler(transport, task))

            Runners.streaming_run_task(task, message_handler=_message_handler_warpper)
        except WebSocketDisconnect:
            logger.info("WebSocket connection closed")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            # Clean up the connection
            message_task.cancel()
            await transport.close()
