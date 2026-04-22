from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass(slots=True)
class AcpSessionRecord:
    acp_session_id: str
    aworld_session_id: str
    cwd: str
    requested_mcp_servers: list[dict] = field(default_factory=list)


class AcpSessionStore:
    def __init__(self) -> None:
        self._records: dict[str, AcpSessionRecord] = {}

    def create_session(
        self,
        cwd: str,
        requested_mcp_servers: list[dict],
    ) -> AcpSessionRecord:
        record = AcpSessionRecord(
            acp_session_id=f"acp_{uuid4().hex}",
            aworld_session_id=f"aworld_{uuid4().hex}",
            cwd=str(Path(cwd).expanduser()),
            requested_mcp_servers=list(requested_mcp_servers),
        )
        self._records[record.acp_session_id] = record
        return record

    def get(self, acp_session_id: str) -> AcpSessionRecord | None:
        return self._records.get(acp_session_id)
