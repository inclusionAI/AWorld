#!/usr/bin/env python3
"""
Minimal example: create a Sandbox directly and call filesystem-server.file_parse.

Run with the built-in sample docx:
    python examples/document_parse/direct_sandbox_file_parse_example.py

Parse a remote AFTS file:
    python examples/document_parse/direct_sandbox_file_parse_example.py --file-id <AFTS_FILE_ID> --file-type docx

Parse a local workspace file:
    python examples/document_parse/direct_sandbox_file_parse_example.py --workspace-path /Users/.../fs_workspace/demo.docx

By default this example connects to the pre MCP gateway. To use a local server:
    FILESYSTEM_MCP_URL=http://127.0.0.1:8083/mcp python examples/document_parse/direct_sandbox_file_parse_example.py ...

Required env for pre gateway:
    MCP_TOKEN or TOKEN_PRE

Required env for --file-id:
    AFTS_LEOPARD_FILE_KEY
    AFTS_LEOPARD_FILE_SECRET

Optional env:
    FILESYSTEM_MCP_URL (default: https://mcpgateway-pre.alipay.com/mcp)
    AFTS_APP_ID        (default: apwallet)
    AFTS_BASE_URL      (default: http://mmtcapi.stable.alipay.net/meta/1.0/query)
    SANDBOX_FILE_ID    (default: built-in sample docx file id)
    SANDBOX_FILE_TYPE  (default: docx)
    SANDBOX_WORKSPACE_PATH
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

SERVER_ROOT = Path(__file__).resolve().parents[2]
if str(SERVER_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT / "src"))

from dotenv import load_dotenv

from aworld.sandbox import Sandbox


DEFAULT_MCP_URL = "https://mcpgateway-pre.alipay.com/mcp"
DEFAULT_FILE_ID = "F6eGTrb0aNgAAAAARTAAAAgAeuI7AQFr"
DEFAULT_FILE_TYPE = "docx"
LOCAL_MCP_URLS = {"http://127.0.0.1:8083/mcp", "http://localhost:8083/mcp"}


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env: {name}")
    return value


def load_env_files() -> None:
    load_dotenv()
    load_dotenv(SERVER_ROOT / ".env", override=False)
    load_dotenv(SERVER_ROOT.parent / ".env", override=False)


def build_env_content(require_afts: bool) -> dict[str, str]:
    if not require_afts:
        return {}

    return {
        "afts_biz_key": require_env("AFTS_LEOPARD_FILE_KEY"),
        "afts_biz_secret": require_env("AFTS_LEOPARD_FILE_SECRET"),
        "afts_app_id": os.getenv("AFTS_APP_ID", "apwallet"),
        "afts_base_url": os.getenv(
            "AFTS_BASE_URL",
            "http://mmtcapi.stable.alipay.net/meta/1.0/query",
        ),
    }


def build_mcp_config(session_id: str) -> dict[str, Any]:
    mcp_url = os.getenv("FILESYSTEM_MCP_URL", DEFAULT_MCP_URL)
    headers: dict[str, str] = {}
    if mcp_url not in LOCAL_MCP_URLS:
        token = os.getenv("MCP_TOKEN") or os.getenv("TOKEN_PRE")
        if not token:
            raise RuntimeError("Missing required env for pre gateway: MCP_TOKEN or TOKEN_PRE")

        sandbox_env = {
            "OSS_ACCESS_KEY_ID": os.getenv("OSS_ACCESS_KEY_ID") or os.getenv("LINGGUANG_OSS_ACCESS_KEY_KEY"),
            "OSS_ACCESS_KEY_SECRET": os.getenv("OSS_ACCESS_KEY_SECRET") or os.getenv("LINGGUANG_OSS_ACCESS_KEY_SECRET"),
            "OSS_ENDPOINT": os.getenv("OSS_ENDPOINT") or os.getenv(
                "LINGGUANG_OSS_ENDPOINT",
                "cn-shanghai-ant-internal.oss-alipay.aliyuncs.com",
            ),
            "OSS_BUCKET": os.getenv("OSS_BUCKET") or os.getenv("LINGGUANG_OSS_BUCKET"),
            "WORKSPACE_PATH": os.getenv("WORKSPACE_PATH", "/leopard/sandbox/workspace"),
            "TEMPLATES_PATH": os.getenv("TEMPLATES_PATH", "/leopard/sandbox/templates"),
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "SESSION_ID": session_id,
            "MCP_SERVERS": "filesystem",
            "TRACE_ID": uuid.uuid4().hex,
            "SANDBOX_ENV": json.dumps(sandbox_env),
            "OS_SYSTEM": "linux",
            "env_name": "",
            "tenant": "ARCA",
            "sandbox_template_id": "ARCA-TEMPLATE-000000004aabb199",
        }

    return {
        "mcpServers": {
            "filesystem-server": {
                "type": "streamable-http",
                "url": mcp_url,
                "headers": headers,
                "timeout": 9999.0,
                "client_session_timeout_seconds": 9999.0,
                "sse_read_timeout": 9999.0,
            }
        }
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Sandbox directly and call filesystem-server.file_parse."
    )
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--file-id", default=os.getenv("SANDBOX_FILE_ID"))
    source.add_argument("--workspace-path", default=os.getenv("SANDBOX_WORKSPACE_PATH"))
    parser.add_argument("--file-type", default=os.getenv("SANDBOX_FILE_TYPE", DEFAULT_FILE_TYPE))
    parser.add_argument("--sync-mode", choices=["sync", "async"], default="sync")
    parser.add_argument(
        "--asset-reference-mode",
        choices=["remote_id", "local_path"],
        default="remote_id",
    )
    args = parser.parse_args()

    if args.file_id and args.workspace_path:
        parser.error("use only one of --file-id/SANDBOX_FILE_ID or --workspace-path/SANDBOX_WORKSPACE_PATH")
    if not args.file_id and not args.workspace_path:
        args.file_id = DEFAULT_FILE_ID

    return args


def print_tool_result(result: list[Any] | None) -> None:
    if not result:
        print("No result returned")
        return

    raw_content = getattr(result[0], "content", result[0])
    print("Raw result:")
    print(raw_content)

    try:
        content_list = json.loads(raw_content)
        payload = json.loads(content_list[0]) if content_list else {}
    except (TypeError, json.JSONDecodeError, IndexError):
        return

    print("\nParsed payload:")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def main() -> None:
    load_env_files()
    args = parse_args()
    session_id = f"direct-filesystem-{uuid.uuid4().hex[:8]}"

    sandbox = Sandbox(
        sandbox_id=session_id,
        mcp_config=build_mcp_config(session_id),
        mcp_servers=["filesystem-server"],
        reuse=False,
        env_content=build_env_content(require_afts=bool(args.file_id)),
    )

    params = {
        "file_id": args.file_id,
        "workspace_path": args.workspace_path,
        "file_type": args.file_type,
        "sync_mode": args.sync_mode,
        "asset_reference_mode": args.asset_reference_mode,
    }
    params = {key: value for key, value in params.items() if value is not None}

    try:
        tools = await sandbox.list_tools()
        print(f"Loaded {len(tools)} tool(s)")

        result = await sandbox.call_tool(
            action_list=[
                {
                    "tool_name": "filesystem-server",
                    "action_name": "file_parse",
                    "params": params,
                }
            ]
        )
        print_tool_result(result)
    finally:
        await sandbox.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
