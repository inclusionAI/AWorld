# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""
MCP config for builtin tools: streamable-http, URL / TOKEN / WORKSPACE from env.

Sandbox 连接工具服务时从环境变量读取配置；
启动 script/start_tool_servers.sh 或工具服务进程时，也会读取这些变量（如 WORKSPACE 传给 filesystem/terminal）。

.env 填写示例（项目根或 script 同层）：
  # 内置工具服务 URL（本地启动脚本会默认 http://127.0.0.1:8084 / 8081）
  AWORLD_FILESYSTEM_ENDPOINT=http://127.0.0.1:8084
  AWORLD_TERMINAL_ENDPOINT=http://127.0.0.1:8081
  # 可选：认证 Token
  AWORLD_FILESYSTEM_TOKEN=
  AWORLD_TERMINAL_TOKEN=
  # 工作目录：filesystem 允许访问的目录（逗号分隔），terminal 的工作目录
  AWORLD_WORKSPACE=/path/to/workspace
"""

import os
from typing import Optional, Dict, Any

# ============ 环境变量名（与 .env、start_tool_servers.sh、tool_servers 内一致）============
ENV_FILESYSTEM_ENDPOINT = "AWORLD_FILESYSTEM_ENDPOINT"
ENV_FILESYSTEM_TOKEN = "AWORLD_FILESYSTEM_TOKEN"
ENV_TERMINAL_ENDPOINT = "AWORLD_TERMINAL_ENDPOINT"
ENV_TERMINAL_TOKEN = "AWORLD_TERMINAL_TOKEN"
ENV_WORKSPACE = "AWORLD_WORKSPACE"

# Server type and timeouts
STREAMABLE_HTTP_TYPE = "streamable-http"
DEFAULT_TIMEOUT = 9999.0
DEFAULT_SSE_READ_TIMEOUT = 9999.0
DEFAULT_CLIENT_SESSION_TIMEOUT = 9999.0


def get_server_env() -> Dict[str, str]:
    """从当前环境读取要传给 MCP 服务进程的 env（如 workspace）。启动脚本需导出这些变量，服务内从 os.getenv 读取。"""
    env: Dict[str, str] = {}
    v = os.environ.get(ENV_WORKSPACE, "").strip()
    if v:
        env[ENV_WORKSPACE] = v
    return env


def build_server_config(
    url: str,
    token: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> dict:
    """Build one mcpServer entry: streamable-http with url, optional Bearer token, and optional env 供服务端读取。"""
    cfg: Dict[str, Any] = {
        "type": STREAMABLE_HTTP_TYPE,
        "url": url,
        "timeout": DEFAULT_TIMEOUT,
        "client_session_timeout_seconds": DEFAULT_CLIENT_SESSION_TIMEOUT,
        "sse_read_timeout": DEFAULT_SSE_READ_TIMEOUT,
    }
    if token and token.strip():
        cfg["headers"] = {"Authorization": f"Bearer {token.strip()}"}
    if env:
        cfg["env"] = dict(env)
    return cfg
