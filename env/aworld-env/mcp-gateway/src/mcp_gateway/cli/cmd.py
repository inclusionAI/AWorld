import asyncio
import click
from mcp_gateway.mcp_gateway import MCPGateway
from mcp_gateway.sessions.session_connection import SessionId


@click.group()
def cli():
    """MCP Gateway CLI工具"""
    pass


@cli.command()
@click.option(
    "--session-id",
    "-s",
    required=True,
    help="Session ID (用于多步骤会话亲和性)",
)
@click.option(
    "--mcp-session-id",
    "-m",
    help="MCP Session ID (MCP客户端会话ID)",
)
def release(session_id: str, mcp_session_id: str | None):
    """释放MCP会话"""
    asyncio.run(_release_session(session_id, mcp_session_id))


async def _release_session(session_id: str, mcp_session_id: str | None):
    """释放会话的异步实现"""
    gateway = MCPGateway()
    try:
        await gateway.startup()
        session = SessionId(session_id=session_id, mcp_session_id=mcp_session_id)
        await gateway.session_connection_manager.release_mcp_session(session)
        click.echo(
            f"成功释放会话: session_id={session_id}, mcp_session_id={mcp_session_id}"
        )
    except Exception as e:
        click.echo(f"释放会话失败: {e}", err=True)
        raise
    finally:
        await gateway.shutdown()


if __name__ == "__main__":
    cli()
