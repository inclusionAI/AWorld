#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_ROOT="$(cd "${SCRIPT_DIR}/../../../../" && pwd)"

MCP_URL_VALUE="${FILESYSTEM_MCP_URL:-https://mcpgateway-pre.alipay.com/mcp}"
MCP_TOKEN_VALUE="${MCP_TOKEN:-${TOKEN_PRE:-}}"
TIMEOUT_VALUE="${FILEX_EXCEL_CASE_TIMEOUT:-300}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

usage() {
  cat <<'EOF'
Usage:
  run_mcp_filex_excel_case.sh [--mcp-url URL] [--token TOKEN] [--timeout SECONDS]

Description:
  Runs an Excel filex smoke test inside the MCP sandbox through terminal-server execute_command.
  This does not run host-local filex.

Environment:
  FILESYSTEM_MCP_URL      Default: https://mcpgateway-pre.alipay.com/mcp
  MCP_TOKEN or TOKEN_PRE  Bearer token for remote MCP gateway
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mcp-url)
      MCP_URL_VALUE="${2:-}"
      shift 2
      ;;
    --token)
      MCP_TOKEN_VALUE="${2:-}"
      shift 2
      ;;
    --timeout)
      TIMEOUT_VALUE="${2:-300}"
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

if [[ "${MCP_URL_VALUE}" != "http://127.0.0.1:8083/mcp" && "${MCP_URL_VALUE}" != "http://localhost:8083/mcp" && -z "${MCP_TOKEN_VALUE}" ]]; then
  echo "Remote MCP gateway requires --token or MCP_TOKEN/TOKEN_PRE" >&2
  exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python is not executable: ${PYTHON_BIN}" >&2
  exit 1
fi

SERVER_ROOT="${SERVER_ROOT}" \
MCP_URL_VALUE="${MCP_URL_VALUE}" \
MCP_TOKEN_VALUE="${MCP_TOKEN_VALUE}" \
TIMEOUT_VALUE="${TIMEOUT_VALUE}" \
"${PYTHON_BIN}" - <<'PY'
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any

server_root = Path(os.environ["SERVER_ROOT"]).resolve()
src_path = server_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from dotenv import load_dotenv
from aworld.sandbox import Sandbox

load_dotenv(server_root / ".env", override=False)
load_dotenv(server_root.parent / ".env", override=False)


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


def _sandbox_script() -> str:
    return textwrap.dedent(
        r'''
        set -euo pipefail
        cd /app/mcp_servers/filesystem_server
        mkdir -p /root/fs_workspace
        XLSX=/root/fs_workspace/filex-excel-case.xlsx
        python3 - <<'PYMAKE'
        from pathlib import Path
        from zipfile import ZipFile, ZIP_DEFLATED

        p = Path("/root/fs_workspace/filex-excel-case.xlsx")
        files = {
            "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>""",
            "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>""",
            "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sales" sheetId="1" r:id="rId1"/></sheets></workbook>""",
            "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>""",
            "xl/worksheets/sheet1.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>month</t></is></c><c r="B1" t="inlineStr"><is><t>revenue</t></is></c><c r="C1" t="inlineStr"><is><t>cost</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>2026-06</t></is></c><c r="B2"><v>12345</v></c><c r="C2"><v>6789</v></c></row><row r="3"><c r="A3" t="inlineStr"><is><t>2026-07</t></is></c><c r="B3"><v>23456</v></c><c r="C3"><v>9876</v></c></row></sheetData></worksheet>""",
        }
        with ZipFile(p, "w", ZIP_DEFLATED) as zf:
            for name, data in files.items():
                zf.writestr(name, data)
        print(p)
        PYMAKE

        OUT=$(mktemp /tmp/filex-excel-parse.XXXXXX.json)
        ./bin/filex parse \
          --workspace-path "$XLSX" \
          --file-type xlsx \
          --sync-mode sync \
          --asset-reference-mode remote_id > "$OUT"

        python3 - "$OUT" <<'PYSUM'
        import json
        import re
        import sys

        payload_path = sys.argv[1]
        payload = json.load(open(payload_path))
        markdown = payload.get("markdown") or ""
        print(json.dumps({
            "success": payload.get("success"),
            "message": payload.get("message"),
            "output_file_id": payload.get("output_file_id"),
            "markdown_len": len(markdown),
            "preview": markdown[:800],
        }, ensure_ascii=False, indent=2))
        PYSUM
        '''
    ).strip()


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


async def main() -> None:
    session_id = f"filex-excel-{uuid.uuid4().hex[:8]}"
    sandbox = Sandbox(
        sandbox_id=session_id,
        mcp_config=_mcp_config(session_id),
        mcp_servers=["terminal-server", "filesystem-server"],
        reuse=False,
    )
    try:
        tools = await sandbox.list_tools()
        print(f"Loaded {len(tools)} tool(s)")
        encoded = base64.b64encode(_sandbox_script().encode()).decode()
        command = f"printf %s {encoded!r} | base64 -d > /tmp/filex_excel_case.sh && bash /tmp/filex_excel_case.sh"
        result = await sandbox.call_tool(
            action_list=[
                {
                    "tool_name": "terminal-server",
                    "action_name": "execute_command",
                    "params": {
                        "command": command,
                        "timeout": int(os.environ["TIMEOUT_VALUE"]),
                        "output_format": "text",
                    },
                }
            ]
        )
        print(_extract_text(result))
    finally:
        await sandbox.cleanup()


asyncio.run(main())
PY
