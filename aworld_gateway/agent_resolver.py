from __future__ import annotations

class AgentResolver:
    def __init__(self, default_agent_id: str) -> None:
        self.default_agent_id = default_agent_id

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
            self.default_agent_id,
        ):
            if candidate:
                return candidate

        raise ValueError("No agent id could be resolved.")
