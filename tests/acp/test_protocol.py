from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.protocol import decode_jsonrpc_line, encode_jsonrpc_message


def test_encode_jsonrpc_message_appends_single_newline() -> None:
    payload = encode_jsonrpc_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})

    assert payload.endswith(b"\n")
    assert payload.count(b"\n") == 1


def test_decode_jsonrpc_line_round_trips_message() -> None:
    message = decode_jsonrpc_line(b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n')

    assert message["method"] == "initialize"
