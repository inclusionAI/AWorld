from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


AWORLD_ACP_SESSION_NOT_FOUND = "AWORLD_ACP_SESSION_NOT_FOUND"
AWORLD_ACP_SESSION_BUSY = "AWORLD_ACP_SESSION_BUSY"
AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT = "AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT"
AWORLD_ACP_INVALID_CWD = "AWORLD_ACP_INVALID_CWD"
AWORLD_ACP_UNSUPPORTED_MCP_SERVERS = "AWORLD_ACP_UNSUPPORTED_MCP_SERVERS"
AWORLD_ACP_REQUIRES_HUMAN = "AWORLD_ACP_REQUIRES_HUMAN"
AWORLD_ACP_APPROVAL_UNSUPPORTED = "AWORLD_ACP_APPROVAL_UNSUPPORTED"


@dataclass(slots=True)
class AcpErrorDetail:
    code: str
    message: str
    retryable: bool | None = None
    data: dict[str, Any] | None = None


class AcpBusyError(RuntimeError):
    """Raised when a session already has an active turn."""


def build_error_data(detail: AcpErrorDetail) -> dict[str, Any]:
    payload = asdict(detail)
    return {key: value for key, value in payload.items() if value is not None}
