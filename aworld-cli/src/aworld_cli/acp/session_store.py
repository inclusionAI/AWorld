from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import AWORLD_ACP_INVALID_CWD
from .session_runtime import normalize_requested_mcp_servers


@dataclass(slots=True)
class AcpSessionRecord:
    acp_session_id: str
    aworld_session_id: str
    cwd: str
    requested_mcp_servers: Any = field(default_factory=list)


class AcpSessionStore:
    def __init__(self) -> None:
        self._records: dict[str, AcpSessionRecord] = {}

    def create_session(
        self,
        cwd: str,
        requested_mcp_servers: Any,
    ) -> AcpSessionRecord:
        resolved_cwd = Path(cwd).expanduser().resolve()
        if not resolved_cwd.exists() or not resolved_cwd.is_dir():
            raise ValueError(AWORLD_ACP_INVALID_CWD)
        normalize_requested_mcp_servers(requested_mcp_servers)

        record = AcpSessionRecord(
            acp_session_id=f"acp_{uuid4().hex}",
            aworld_session_id=f"aworld_{uuid4().hex}",
            cwd=str(resolved_cwd),
            requested_mcp_servers=requested_mcp_servers,
        )
        self._records[record.acp_session_id] = record
        return record

    def get(self, acp_session_id: str) -> AcpSessionRecord | None:
        return self._records.get(acp_session_id)
