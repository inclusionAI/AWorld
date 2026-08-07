#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_ROOT="$(cd "${SCRIPT_DIR}/../../../../" && pwd)"

SOURCE_DIR_VALUE=""
MCP_URL_VALUE="${FILESYSTEM_MCP_URL:-https://mcpgateway-pre.alipay.com/mcp}"
MCP_TOKEN_VALUE="${MCP_TOKEN:-${TOKEN_PRE:-}}"
TIMEOUT_VALUE="${FILEX_DIRECTORY_CASE_TIMEOUT:-1200}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SESSION_ID_VALUE="${FILEX_DIRECTORY_CASE_SESSION_ID:-}"

usage() {
  cat <<'EOF'
Usage:
  run_mcp_filex_directory_case.sh --source-dir DIR [--mcp-url URL] [--token TOKEN] [--timeout SECONDS] [--session-id SESSION_ID]

Description:
  Reads a local directory and sends each supported file through one MCP terminal-server command.
  Each command writes the file into /root/fs_workspace and runs filex parse inside the sandbox.
  The host machine does not run filex.

Supported file types:
  md, csv, docx, xlsx, pptx, pdf

Environment:
  FILESYSTEM_MCP_URL              Default: https://mcpgateway-pre.alipay.com/mcp
  MCP_TOKEN or TOKEN_PRE          Bearer token for remote MCP gateway
  FILEX_DIRECTORY_CASE_TIMEOUT    Default: 1200
  FILEX_DIRECTORY_CASE_SESSION_ID Optional fixed MCP session id
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir)
      SOURCE_DIR_VALUE="${2:-}"
      shift 2
      ;;
    --mcp-url)
      MCP_URL_VALUE="${2:-}"
      shift 2
      ;;
    --token)
      MCP_TOKEN_VALUE="${2:-}"
      shift 2
      ;;
    --timeout)
      TIMEOUT_VALUE="${2:-1200}"
      shift 2
      ;;
    --session-id)
      SESSION_ID_VALUE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "${SOURCE_DIR_VALUE}" || ! -d "${SOURCE_DIR_VALUE}" ]]; then
  echo "Missing or invalid --source-dir: ${SOURCE_DIR_VALUE}" >&2
  usage >&2
  exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python is not executable: ${PYTHON_BIN}" >&2
  exit 1
fi

SERVER_ROOT="${SERVER_ROOT}" \
SOURCE_DIR_VALUE="${SOURCE_DIR_VALUE}" \
MCP_URL_VALUE="${MCP_URL_VALUE}" \
MCP_TOKEN_VALUE="${MCP_TOKEN_VALUE}" \
TIMEOUT_VALUE="${TIMEOUT_VALUE}" \
SESSION_ID_VALUE="${SESSION_ID_VALUE}" \
"${PYTHON_BIN}" - <<'PY'
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shlex
import sys
import time
import uuid
from pathlib import Path
from typing import Any

server_root = Path(os.environ["SERVER_ROOT"]).resolve()
src_path = server_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

try:
    from dotenv import load_dotenv

    load_dotenv(server_root / ".env", override=False)
    load_dotenv(server_root.parent / ".env", override=False)
except Exception:
    pass

from aworld.sandbox import Sandbox

SUPPORTED_TYPES = {
    ".md": "md",
    ".csv": "csv",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
    ".pdf": "pdf",
}

REMOTE_ROOT = "/root/fs_workspace/filex-directory-case"


def _mcp_config(session_id: str) -> dict[str, Any]:
    mcp_url = os.environ["MCP_URL_VALUE"]
    headers: dict[str, str] = {}
    if mcp_url not in {"http://127.0.0.1:8083/mcp", "http://localhost:8083/mcp"}:
        token = os.environ["MCP_TOKEN_VALUE"] or os.getenv("MCP_TOKEN") or os.getenv("TOKEN_PRE")
        if not token:
            raise RuntimeError("Missing required env for pre gateway: MCP_TOKEN or TOKEN_PRE")
        sandbox_env = {
            "OSS_ACCESS_KEY_ID": os.getenv("OSS_ACCESS_KEY_ID") or os.getenv("LINGGUANG_OSS_ACCESS_KEY_KEY"),
            "OSS_ACCESS_KEY_SECRET": os.getenv("OSS_ACCESS_KEY_SECRET") or os.getenv("LINGGUANG_OSS_ACCESS_KEY_SECRET"),
            "OSS_ENDPOINT": os.getenv(
                "OSS_ENDPOINT",
                os.getenv("LINGGUANG_OSS_ENDPOINT", "cn-shanghai-ant-internal.oss-alipay.aliyuncs.com"),
            ),
            "OSS_BUCKET": os.getenv("OSS_BUCKET") or os.getenv("LINGGUANG_OSS_BUCKET"),
            "WORKSPACE_PATH": os.getenv("WORKSPACE_PATH", "/leopard/sandbox/workspace"),
            "TEMPLATES_PATH": os.getenv("TEMPLATES_PATH", "/leopard/sandbox/templates"),
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "SESSION_ID": session_id,
            "MCP_SERVERS": "terminal,filesystem",
            "TRACE_ID": uuid.uuid4().hex,
            "SANDBOX_ENV": json.dumps(sandbox_env),
            "OS_SYSTEM": "linux",
            "tenant": "ARCA",
            "sandbox_template_id": "ARCA-TEMPLATE-000000004aabb199",
        }

    server = {
        "type": "streamable-http",
        "url": mcp_url,
        "headers": headers,
        "timeout": float(os.environ["TIMEOUT_VALUE"]),
        "client_session_timeout_seconds": float(os.environ["TIMEOUT_VALUE"]),
        "sse_read_timeout": float(os.environ["TIMEOUT_VALUE"]),
    }
    return {"mcpServers": {"terminal-server": server, "filesystem-server": server}}


def _extract_text(result: list[Any] | None) -> str:
    if not result:
        return ""
    raw = getattr(result[0], "content", result[0])
    if not isinstance(raw, str):
        return str(raw)
    try:
        outer = json.loads(raw)
        if isinstance(outer, list) and outer:
            inner = json.loads(outer[0])
            if isinstance(inner, dict):
                return inner.get("stdout") or inner.get("output") or json.dumps(inner, ensure_ascii=False)
    except Exception:
        pass
    return raw


async def _call_terminal(sandbox: Sandbox, command: str, timeout: int) -> str:
    result = await sandbox.call_tool(
        action_list=[
            {
                "tool_name": "terminal-server",
                "action_name": "execute_command",
                "params": {
                    "command": command,
                    "timeout": timeout,
                    "output_format": "text",
                },
            }
        ]
    )
    return _extract_text(result)


def _collect_source(source_dir: Path) -> list[dict[str, str]]:
    files = [
        path
        for path in sorted(source_dir.iterdir())
        if path.is_file() and path.name != ".DS_Store" and path.suffix.lower() in SUPPORTED_TYPES
    ]
    return [
        {
            "name": path.name,
            "type": SUPPORTED_TYPES[path.suffix.lower()],
            "path": str(path),
            "size": str(path.stat().st_size),
        }
        for path in files
    ]


def _parse_command(item: dict[str, str], task_prefix: str) -> str:
    local_path = Path(item["path"])
    encoded = base64.b64encode(local_path.read_bytes()).decode("ascii")
    remote_path = f"{REMOTE_ROOT}/input/{local_path.name}"
    task_id = f"{task_prefix}-{local_path.stem[:24]}-{item['type']}".replace(" ", "-")
    cmd = [
        "./bin/filex",
        "parse",
        "--workspace-path",
        remote_path,
        "--file-type",
        item["type"],
        "--sync-mode",
        "sync",
        "--task-id",
        task_id,
        "--asset-reference-mode",
        "remote_id",
    ]
    if item["type"] == "pdf":
        cmd += ["--env-content-json", '{"pdf_parse_provider":"paddle_ocr"}']
    parse_cmd = " ".join(shlex.quote(part) for part in cmd)
    return (
        "set -euo pipefail"
        f" && mkdir -p {shlex.quote(REMOTE_ROOT + '/input')} {shlex.quote(REMOTE_ROOT + '/results')}"
        f" && printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(remote_path)}"
        " && cd /app/mcp_servers/filesystem_server"
        " && export DOCUMENT_PARSE_LOG_LEVEL=\"${DOCUMENT_PARSE_LOG_LEVEL:-INFO}\""
        " && export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True"
        f" && {parse_cmd}"
    )


def _terminal_message(output: str) -> tuple[bool, str]:
    try:
        payload = json.loads(output)
        return bool(payload.get("success")), str(payload.get("message") or payload.get("output") or "")
    except Exception:
        return True, output


def _filex_payload_from_terminal(output: str) -> tuple[bool, dict[str, Any], str]:
    terminal_success, message = _terminal_message(output)
    marker = "\nOutput:\n"
    stdout = message.split(marker, 1)[1].strip() if marker in message else message.strip()
    candidates = [stdout]
    json_blocks = re.findall(r"\{\s*\"success\"\s*:\s*(?:true|false).*?\n\}", stdout, flags=re.DOTALL)
    candidates.extend(reversed(json_blocks))
    for candidate in candidates:
        try:
            return terminal_success, json.loads(candidate), message
        except Exception:
            continue
    return terminal_success, {}, message


async def main() -> None:
    source_dir = Path(os.environ["SOURCE_DIR_VALUE"]).resolve()
    manifest = _collect_source(source_dir)
    session_id = os.environ["SESSION_ID_VALUE"] or f"filex-dir-{uuid.uuid4().hex[:8]}"
    timeout = int(os.environ["TIMEOUT_VALUE"])

    print("=== MCP filex directory case ===")
    print(f"session_id={session_id}")
    print(f"source_dir={source_dir}")
    print(f"files={len(manifest)}")
    print(f"total_bytes={sum(int(item['size']) for item in manifest)}")

    sandbox = Sandbox(
        sandbox_id=session_id,
        mcp_config=_mcp_config(session_id),
        mcp_servers=["terminal-server", "filesystem-server"],
        reuse=False,
    )
    try:
        tools = await sandbox.list_tools()
        print(f"Loaded {len(tools)} tool(s)")
        results: list[dict[str, Any]] = []
        for index, item in enumerate(manifest, start=1):
            started = time.time()
            output = await _call_terminal(
                sandbox,
                "bash -lc " + json.dumps(_parse_command(item, f"filex-dir-{index:02d}")),
                timeout=timeout,
            )
            elapsed = round(time.time() - started, 2)
            terminal_success, payload, message = _filex_payload_from_terminal(output)
            markdown = payload.get("markdown") or ""
            row = {
                "name": item["name"],
                "type": item["type"],
                "size": int(item["size"]),
                "success": bool(terminal_success and payload.get("success")),
                "seconds": elapsed,
                "message": payload.get("message") or ("" if terminal_success else message[-500:]),
                "output_file_id": payload.get("output_file_id"),
                "file_url": payload.get("file_url"),
                "file_path": payload.get("file_path"),
                "markdown_len": len(markdown),
            }
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)

        print(json.dumps({
            "total": len(results),
            "success": sum(1 for row in results if row["success"]),
            "failed": [row for row in results if not row["success"]],
        }, ensure_ascii=False, indent=2))
    finally:
        await sandbox.cleanup()


asyncio.run(main())
PY
