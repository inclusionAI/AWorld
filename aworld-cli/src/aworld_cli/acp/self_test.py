from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


def build_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for case in cases if case["ok"])
    failed = sum(1 for case in cases if not case["ok"])
    return {
        "ok": failed == 0,
        "summary": {"passed": passed, "failed": failed, "skipped": 0},
        "cases": cases,
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _server_env() -> dict[str, str]:
    repo_root = _repo_root()
    env = dict(os.environ)
    pythonpath = str(repo_root / "aworld-cli" / "src") + os.pathsep + str(repo_root)
    env["PYTHONPATH"] = pythonpath
    return env


async def _read_json_line(reader: asyncio.StreamReader) -> dict[str, Any]:
    line = await reader.readline()
    if not line:
        raise RuntimeError("ACP self-test expected a JSON line but got EOF.")
    return json.loads(line.decode("utf-8"))


async def _run_case_matrix() -> list[dict[str, Any]]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "aworld_cli.main",
        "--no-banner",
        "acp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(_repo_root()),
        env=_server_env(),
    )

    assert proc.stdin is not None
    assert proc.stdout is not None

    cases: list[dict[str, Any]] = []
    session_id: str | None = None

    try:
        proc.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        await proc.stdin.drain()
        initialize = await _read_json_line(proc.stdout)
        cases.append(
            {
                "id": "initialize_handshake",
                "ok": initialize.get("result", {}).get("serverInfo", {}).get("name") == "aworld-cli",
                "detail": initialize,
            }
        )

        proc.stdin.write(
            b'{"jsonrpc":"2.0","id":2,"method":"newSession","params":{"cwd":".","mcpServers":[]}}\n'
        )
        await proc.stdin.drain()
        new_session = await _read_json_line(proc.stdout)
        session_id = new_session.get("result", {}).get("sessionId")
        cases.append(
            {
                "id": "new_session_created",
                "ok": bool(session_id),
                "detail": new_session,
            }
        )

        if session_id:
            prompt_request = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": {"content": [{"type": "text", "text": "self-test"}]},
                },
            }
            proc.stdin.write((json.dumps(prompt_request) + "\n").encode("utf-8"))
            await proc.stdin.drain()
            notification = await _read_json_line(proc.stdout)
            prompt_result = await _read_json_line(proc.stdout)
            cases.append(
                {
                    "id": "prompt_round_trip",
                    "ok": (
                        notification.get("method") == "sessionUpdate"
                        and notification.get("params", {})
                        .get("update", {})
                        .get("content", {})
                        .get("text")
                        == "self-test"
                        and prompt_result.get("result", {}).get("status") == "completed"
                    ),
                    "detail": {
                        "notification": notification,
                        "result": prompt_result,
                    },
                }
            )
    finally:
        proc.kill()
        await proc.wait()

    return cases


async def run_self_test() -> int:
    cases = await _run_case_matrix()
    payload = build_summary(cases)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0 if payload["ok"] else 1
