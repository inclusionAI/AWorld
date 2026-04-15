from __future__ import annotations

from pydantic import BaseModel


class AgentResolver(BaseModel):
    global_default_agent_id: str

    def resolve(
        self,
        *,
        explicit_agent_id: str | None = None,
        session_agent_id: str | None = None,
        channel_default_agent_id: str | None = None,
        matched_route_agent_id: str | None = None,
    ) -> str:
        for candidate in (
            explicit_agent_id,
            session_agent_id,
            channel_default_agent_id,
            matched_route_agent_id,
            self.global_default_agent_id,
        ):
            if candidate:
                return candidate

        raise ValueError("No agent id could be resolved.")
