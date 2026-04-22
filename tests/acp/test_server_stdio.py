from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ENV = {
    "PYTHONPATH": str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT),
}


def _spawn_acp_server() -> subprocess.Popen[str]:
    env = dict(os.environ)
    env.update(ENV)
    return subprocess.Popen(
        [sys.executable, "-m", "aworld_cli.main", "--no-banner", "acp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )


def _read_json_line(proc: subprocess.Popen[str]) -> dict:
    line = proc.stdout.readline()
    assert line, proc.stderr.read()
    return json.loads(line)


def test_acp_server_initialize_round_trip() -> None:
    proc = _spawn_acp_server()
    try:
        assert proc.stdin is not None
        proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        proc.stdin.flush()

        payload = _read_json_line(proc)

        assert payload["id"] == 1
        assert payload["result"]["serverInfo"]["name"] == "aworld-cli"
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_acp_server_new_session_and_prompt_emit_session_update() -> None:
    proc = _spawn_acp_server()
    try:
        assert proc.stdin is not None
        proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        proc.stdin.write('{"jsonrpc":"2.0","id":2,"method":"newSession","params":{"cwd":".","mcpServers":[]}}\n')
        proc.stdin.flush()

        _ = _read_json_line(proc)
        new_session = _read_json_line(proc)
        session_id = new_session["result"]["sessionId"]

        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "prompt",
                    "params": {
                        "sessionId": session_id,
                        "prompt": {"content": [{"type": "text", "text": "hello"}]},
                    },
                }
            )
            + "\n"
        )
        proc.stdin.flush()

        notification = _read_json_line(proc)
        response = _read_json_line(proc)

        assert notification["method"] == "sessionUpdate"
        assert notification["params"]["sessionId"] == session_id
        assert notification["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
        assert notification["params"]["update"]["content"]["text"] == "hello"
        assert response["id"] == 3
        assert response["result"]["status"] == "completed"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_acp_server_cancel_missing_session_returns_error() -> None:
    proc = _spawn_acp_server()
    try:
        assert proc.stdin is not None
        proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"cancel","params":{"sessionId":"missing"}}\n')
        proc.stdin.flush()

        payload = _read_json_line(proc)

        assert payload["id"] == 1
        assert payload["error"]["message"] == "AWORLD_ACP_SESSION_NOT_FOUND"
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_acp_server_invalid_cwd_returns_structured_error_data(tmp_path: Path) -> None:
    proc = _spawn_acp_server()
    try:
        assert proc.stdin is not None
        missing_dir = tmp_path / "missing"
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "newSession",
                    "params": {"cwd": str(missing_dir), "mcpServers": []},
                }
            )
            + "\n"
        )
        proc.stdin.flush()

        payload = _read_json_line(proc)

        assert payload["id"] == 1
        assert payload["error"]["message"] == "AWORLD_ACP_INVALID_CWD"
        assert payload["error"]["data"]["code"] == "AWORLD_ACP_INVALID_CWD"
        assert payload["error"]["data"]["message"] == "AWORLD_ACP_INVALID_CWD"
    finally:
        proc.kill()
        proc.wait(timeout=5)
