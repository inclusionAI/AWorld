from typing import List

from aworld.cmd import AgentModel, ChatCompletionMessage, ChatCompletionRequest
from aworld.session.base_session_service import BaseSessionService
from aworld.session.simple_session_service import SimpleSessionService


class AgentServer:
    def __init__(
        self,
        server_id: str,
        server_name: str,
        session_service: BaseSessionService = None,
    ):
        """
        Initialize AgentServer
        """
        self.server_id = server_id
        self.server_name = server_name
        self.agent_list = []
        self.session_service = session_service or SimpleSessionService()

    def get_session_service(self) -> BaseSessionService:
        return self.session_service

    def get_agent_list(self) -> List[AgentModel]:
        return self.agent_list

    async def on_chat_completion_request(self, request: ChatCompletionRequest):
        await self.get_session_service().append_messages(
            request.user_id,
            request.session_id,
            request.messages[-1:],
        )

    async def on_chat_completion_end(
        self, request: ChatCompletionRequest, final_response: str
    ):
        await self.get_session_service().append_messages(
            request.user_id,
            request.session_id,
            [
                ChatCompletionMessage(
                    role="assistant",
                    content=final_response,
                    trace_id=request.trace_id,
                ),
            ],
        )


CURRENT_SERVER = AgentServer(server_id="default", server_name="default")
