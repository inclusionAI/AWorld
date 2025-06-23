from typing import List
from pydantic import BaseModel

from aworld.cmd import AgentModel
from aworld.session.base_session_service import BaseSessionService


class AgentServer(BaseModel):
    def __init__(
        self,
        server_id: str,
        server_name: str,
        session_service: BaseSessionService,
    ):
        """
        Initialize AgentServer
        """
        self.server_id = server_id
        self.server_name = server_name
        self.agent_list = []
        self.session_service = session_service

    def get_session_service(self) -> BaseSessionService:
        return self.session_service

    def get_agent_list(self) -> List[AgentModel]:
        return self.agent_list
