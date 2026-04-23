from __future__ import annotations

import argparse
import asyncio

from aworld_cli.runtime_bootstrap import RuntimeBootstrapError, bootstrap_runtime
from aworld_cli.top_level_commands.invocation import parse_command_invocation_args


def _register_serve_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--http",
        action="store_true",
        help="Start HTTP server.",
    )
    parser.add_argument(
        "--http-host",
        type=str,
        default="0.0.0.0",
        help="HTTP server host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=8000,
        help="HTTP server port (default: 8000)",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Start MCP server.",
    )
    parser.add_argument(
        "--mcp-name",
        type=str,
        default="AWorldAgent",
        help="MCP server name (default: AWorldAgent)",
    )
    parser.add_argument(
        "--mcp-transport",
        type=str,
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport type.",
    )
    parser.add_argument(
        "--mcp-host",
        type=str,
        default="0.0.0.0",
        help="MCP server host for SSE/streamable-http transport (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--mcp-port",
        type=int,
        default=8001,
        help="MCP server port for SSE/streamable-http transport (default: 8001)",
    )


def _build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aworld-cli serve",
        description="Start HTTP and/or MCP servers.",
    )
    _register_serve_options(parser)
    return parser


def _build_serve_invocation_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--remote-backend", action="append")
    parser.add_argument("--agent-dir", action="append")
    parser.add_argument("--agent-file", action="append")
    parser.add_argument("--skill-path", action="append")
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--http-host", type=str, default="0.0.0.0")
    parser.add_argument("--http-port", type=int, default=8000)
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--mcp-name", type=str, default="AWorldAgent")
    parser.add_argument(
        "--mcp-transport",
        type=str,
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
    )
    parser.add_argument("--mcp-host", type=str, default="0.0.0.0")
    parser.add_argument("--mcp-port", type=int, default=8001)
    return parser


def _parse_serve_invocation_args(argv) -> argparse.Namespace:
    from aworld_cli.main import _GLOBAL_OPTIONS_WITH_VALUES

    return parse_command_invocation_args(
        argv,
        command_name="serve",
        parser=_build_serve_invocation_parser(),
        options_with_values=_GLOBAL_OPTIONS_WITH_VALUES,
    )


class ServeTopLevelCommand:
    @property
    def name(self) -> str:
        return "serve"

    @property
    def description(self) -> str:
        return "Start HTTP and/or MCP servers."

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    def register_parser(self, subparsers) -> None:
        command_parser = subparsers.add_parser(
            "serve",
            help=self.description,
            description=self.description,
            prog="aworld-cli serve",
        )
        _register_serve_options(command_parser)

    def run(self, args, context) -> int | None:
        from aworld_cli.main import (
            _resolve_agent_dirs,
            _run_serve_mode,
            _show_banner,
            init_middlewares,
        )

        invocation_args = _parse_serve_invocation_args(context.argv)
        if not invocation_args.http and not invocation_args.mcp:
            print(
                "❌ Error: At least one of --http or --mcp must be specified for serve command"
            )
            _build_serve_parser().print_help()
            return None

        try:
            bootstrap_runtime(
                env_file=invocation_args.env_file,
                skill_paths=invocation_args.skill_path,
                show_banner="--no-banner" not in context.argv,
                init_middlewares_fn=init_middlewares,
                show_banner_fn=_show_banner,
            )
        except RuntimeBootstrapError:
            return 1

        asyncio.run(
            _run_serve_mode(
                http=invocation_args.http,
                http_host=invocation_args.http_host,
                http_port=invocation_args.http_port,
                mcp=invocation_args.mcp,
                mcp_name=invocation_args.mcp_name,
                mcp_transport=invocation_args.mcp_transport,
                mcp_host=invocation_args.mcp_host,
                mcp_port=invocation_args.mcp_port,
                remote_backends=invocation_args.remote_backend,
                local_dirs=_resolve_agent_dirs(invocation_args.agent_dir),
                agent_files=invocation_args.agent_file,
            )
        )
        return 0
