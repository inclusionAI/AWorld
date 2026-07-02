from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class MacUIError(Exception):
    code: str
    message: str
    details: Optional[dict[str, Any]] = None

    def __post_init__(self) -> None:
        super().__init__(self.message)


def error_payload(error: MacUIError) -> dict[str, Any]:
    payload = {"code": error.code, "message": error.message}
    if error.details is not None:
        payload["details"] = error.details
    return payload
