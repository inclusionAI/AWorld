from __future__ import annotations

import json
from typing import Any


def encode_jsonrpc_message(message: dict[str, Any]) -> bytes:
    return (
        json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def decode_jsonrpc_line(line: bytes) -> dict[str, Any]:
    return json.loads(line.decode("utf-8").strip())
