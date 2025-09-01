"""
训练环境工具：提供在本地一键启动 VirtualPC MCP Server，并生成 Agent 需要的
`mcp_config` 与 `mcp_servers` 配置。

用法示例（参照 train/README.md 55-77 行）：

from train.train_env import create

gaia_env = create(name="GAIA", mode="local")

gaia_agent = Agent(
    conf=AgentConfig(...),
    name="gaia_super_agent",
    system_prompt="...",
    mcp_config=gaia_env["mcp_config"],
    mcp_servers=gaia_env["mcp_servers"],
)
"""

from __future__ import annotations

import os
import time
import subprocess
from typing import Dict, Any, List, Optional

from train.adapter.verl.common import get_agent_tool_env_and_servers


def _project_root() -> str:
    # train/ -> repo_root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_dir() -> str:
    return os.path.join(_project_root(), "env")


def _run_local_gateway_background() -> None:
    """后台启动 VirtualPC MCP Server（Docker）。

    等价于在 repo 根目录执行：
        sh env/run-docker.sh

    注意：该命令会触发镜像构建与 docker compose up，耗时较长。
    本函数以后台进程方式启动，不阻塞当前进程。
    """
    script = os.path.join(_env_dir(), "run-docker.sh")
    if not os.path.exists(script):
        raise FileNotFoundError(f"未找到启动脚本: {script}")

    # 后台启动，避免阻塞训练流程
    subprocess.Popen(
        ["sh", script],
        cwd=_env_dir(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _generate_bearer_token(secret: str, app: str = "aworldcore-agent") -> str:
    """使用 HS256 生成与网关一致的 JWT，并返回 Authorization 头值。

    mcp-gateway 在 docker-compose 中使用环境变量 `MCP_GATEWAY_TOKEN_SECRET`
    作为校验密钥。默认 compose 使用 123321。
    """
    try:
        import jwt  # type: ignore
    except Exception as e:  # pragma: no cover - 明确提示缺依赖
        raise RuntimeError(
            "需要依赖 pyjwt 才能生成本地网关认证 Token，请先安装: pip install pyjwt"
        ) from e

    payload = {"app": app, "version": 1, "time": time.time()}
    token = jwt.encode(payload=payload, key=secret, algorithm="HS256")
    return f"Bearer {token}"


def create(
    name: str = "GAIA",
    mode: str = "local",
    *,
    url: str = "http://localhost:8000/mcp",
    scope_servers: str = "readweb-server,browser-server",
    server_name: str = "aworld-mcp",
    timeout: int | float = 600,
    sse_read_timeout: int | float = 600,
    client_session_timeout_seconds: int | float = 600,
    token_secret: Optional[str] = None,
    auto_start: bool = True,
) -> Dict[str, Any]:
    """创建训练所需的 MCP 环境配置。

    - mode="local": 后台启动本地 VirtualPC MCP Server（docker compose up），并返回连接配置。
    - 返回值包含：
        - mcp_config: Agent 需要的 mcpServers 配置
        - mcp_servers: 供 Agent 选择的 server 列表（与 mcp_config 的 key 对应）

    Args:
        name: 逻辑名称（用于区分不同实验，不影响配置结构）
        mode: "local"/其他（当前仅实现 local）
        url: MCP 网关地址，默认 http://localhost:8000/mcp
        scope_servers: 透传到网关的 MCP_SERVERS 作用域（逗号分隔）
        server_name: 暴露给 Agent 的逻辑 server 名（mcp_config 的 key）
        timeout: 连接超时（秒）
        sse_read_timeout: SSE 读取超时（秒）
        client_session_timeout_seconds: 客户端会话超时（秒）
        token_secret: JWT 密钥；默认读取环境变量 MCP_GATEWAY_TOKEN_SECRET，否则使用 123321
        auto_start: 是否自动后台启动本地网关
    """

    if mode.lower() == "local":
        if auto_start:
            _run_local_gateway_background()

        # token secret 优先级：入参 > env > 默认 compose 值
        secret = token_secret or os.getenv("MCP_GATEWAY_TOKEN_SECRET", "123321")
        authorization = _generate_bearer_token(secret)

        tool_env_config: Dict[str, Any] = {
            "url": url,
            "authorization": authorization,
            "mcp_servers": scope_servers,
            "server_name": server_name,
            "type": "streamable-http",
            "timeout": timeout,
            "sse_read_timeout": sse_read_timeout,
            "client_session_timeout_seconds": client_session_timeout_seconds,
        }

        mcp_config, servers = get_agent_tool_env_and_servers(tool_env_config)
        return {"mcp_config": mcp_config, "mcp_servers": servers}

    raise ValueError(f"不支持的 mode: {mode}")

