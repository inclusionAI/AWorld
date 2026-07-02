from __future__ import annotations

class SessionBinding:
    def build(
        self,
        *,
        agent_id: str,
        channel: str,
        account_id: str,
        conversation_type: str,
        conversation_id: str,
    ) -> str:
        return f"gw:{agent_id}:{channel}:{account_id}:{conversation_type}:{conversation_id}"
