"""
Command-line entry point for aworld-cli.
Provides CLI interface without requiring aworldappinfra.
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from aworld.plugins.discovery import discover_plugins

# Suppress DEBUG/INFO logs from third-party libraries (asyncio, mcp, etc.)
# Only show WARNING and above for non-aworld modules
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Explicitly suppress verbose third-party loggers
third_party_loggers = [
    'mcp',           # MCP server
    'mcp.server',    # MCP server module
    'mcp.shared',    # MCP shared module
    'asyncio',       # asyncio module
    'urllib3',       # HTTP library
    'httpx',         # HTTP library
    'httpcore',      # HTTP core
]

for logger_name in third_party_loggers:
    logging.getLogger(logger_name).setLevel(logging.WARNING)
    # Also disable propagation to avoid console output
    logging.getLogger(logger_name).propagate = False

# Keep aworld's own logging at INFO level (for file logs)
for aworld_logger in ['aworld', 'AWorld']:
    logging.getLogger(aworld_logger).setLevel(logging.INFO)

# Try to import init_middlewares, fallback to no-op if not available
try:
    from aworld.core.context.amni.config import init_middlewares
except ImportError:
    # Fallback: init_middlewares might not be available in all environments
    def init_middlewares():
        """No-op fallback for init_middlewares if not available."""
        pass


def _show_banner(console=None):
    """
    Display AWorld CLI banner with product features.
    """
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text
        
        if console is None:
            console = Console()
        
        # Create main title with gradient effect
        title = Text()
        title.append("\n", style="" )
        title.append("    █████╗ ██╗    ██╗ ██████╗ ██████╗ ██╗     ██████╗ \n", style="bold bright_cyan")
        title.append("   ██╔══██╗██║    ██║██╔═══██╗██╔══██╗██║     ██╔══██╗\n", style="bold bright_cyan")
        title.append("   ███████║██║ █╗ ██║██║   ██║██████╔╝██║     ██║  ██║\n", style="bold bright_blue")
        title.append("   ██╔══██║██║███╗██║██║   ██║██╔══██╗██║     ██║  ██║\n", style="bold bright_blue")
        title.append("   ██║  ██║╚███╔███╔╝╚██████╔╝██║  ██║███████╗██████╔╝\n", style="bold bright_magenta")
        title.append("   ╚═╝  ╚═╝ ╚══╝╚══╝  ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═════╝ \n", style="bold bright_magenta")
        
        # Subtitle
        subtitle = Text("\n   🚀 The Agent Runtime for Self-Improvement — Build & Orchestrate AI Agents\n", style="italic bright_white")
        
        # Create features table
        features_table = Table(show_header=False, box=None, padding=(0, 2))
        features_table.add_column("Icon", style="bright_yellow", justify="left")
        features_table.add_column("Feature", style="bold bright_green")
        features_table.add_column("Description", style="bright_white")
        
        features_table.add_row(
            "🎬",
            "[bold bright_green]Video Creation[/bold bright_green]",
            "[dim]One-sentence to blockbuster video generation[/dim]"
        )
        features_table.add_row(
            "💻",
            "[bold bright_green]Code Generation[/bold bright_green]",
            "[dim]AI-powered code generation and development[/dim]"
        )
        features_table.add_row(
            "🔬",
            "[bold bright_green]AI for Science[/bold bright_green]",
            "[dim]Automated scientific research exploration[/dim]"
        )
        
        # Get version info
        try:
            from . import __version__
            version_str = f"v{__version__}"
        except ImportError:
            version_str = "v1.0.0"
        
        # Combine all elements
        banner_content = Text()
        banner_content.append(title)
        banner_content.append(subtitle)
        banner_content.append(f"   Version {version_str}\n", style="dim")

        console.print(banner_content)
        
        # Print features
        console.print("[bold bright_cyan]🚀 Core Features:[/bold bright_cyan]")
        console.print(features_table)
        cli = AWorldCLI()
        cli._display_conf_info()
        
    except ImportError:
        # Fallback if rich is not available
        print("\nAWorld CLI - AI-Powered Content Creation & Scientific Research Platform\n")
        print("Core Features:")
        print("  🎬 Video Creation - One-sentence to blockbuster")
        print("  💻 Code Generation - AI-powered code development")
        print("  🔬 AI for Science - Automated research exploration")
        print("\nCore Advantages: 多(Versatile) 快(Fast) 好(Quality) 省(Efficient)\n")


def _suppress_keyboard_interrupt_traceback(exc_type, exc_value, exc_tb):
    """Suppress KeyboardInterrupt traceback; exit cleanly."""
    if exc_type is KeyboardInterrupt:
        sys.exit(0)
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _suppress_keyboard_interrupt_traceback

# Set default environment variable to disable console logging before importing aworld modules.
# Gateway mode may explicitly override this before importing this module.
os.environ.setdefault('AWORLD_DISABLE_CONSOLE_LOG', 'true')

# Import aworld modules (they will respect the environment variable)
from .runtime.cli import CliRuntime
from .console import AWorldCLI
from .models import AgentInfo
from .executors.continuous import ContinuousExecutor
from .runtime_bootstrap import RuntimeBootstrapError, bootstrap_runtime
from .core.top_level_command_system import (
    TopLevelCommandContext,
    TopLevelCommandRegistry,
)
from .plugin_capabilities.cli_commands import sync_plugin_cli_commands
from .top_level_commands import register_builtin_top_level_commands

# Import commands to trigger registration
from . import commands


async def load_all_agents(
    remote_backends: Optional[list[str]] = None,
    local_dirs: Optional[list[str]] = None,
    agent_files: Optional[list[str]] = None
) -> list[AgentInfo]:
    """
    Load all agents from local directories, agent files, and remote backends.
    
    This function uses CliRuntime to load agents from:
    1. Local directories (configured via LOCAL_AGENTS_DIR or AGENTS_DIR, or provided as parameter)
    2. Individual agent files (Python .py or Markdown .md files)
    3. Remote backends (configured via REMOTE_AGENT_BACKEND or REMOTE_AGENTS_BACKEND, or provided as parameter)
    
    Args:
        remote_backends: Optional list of remote backend URLs (overrides environment variables)
        local_dirs: Optional list of local agent directories (overrides environment variables)
        agent_files: Optional list of individual agent file paths (Python .py or Markdown .md)
    
    Returns:
        List of all loaded AgentInfo objects
        
    Example:
        >>> agents = await load_all_agents()
        >>> agents = await load_all_agents(remote_backends=["http://localhost:8000"])
        >>> agents = await load_all_agents(local_dirs=["./agents"], agent_files=["./my_agent.py"])
    """
    # Load individual agent files first if provided
    if agent_files:
        from .core.loader import init_agent_file
        for agent_file in agent_files:
            try:
                init_agent_file(agent_file)
            except Exception as e:
                print(f"⚠️ Failed to load agent file {agent_file}: {e}")
    
    # Use a short-lived CliRuntime to load agents from all supported sources.
    runtime = CliRuntime(remote_backends=remote_backends, local_dirs=local_dirs)
    return await runtime._load_agents()


def _resolve_agent_dirs(cli_agent_dirs: Optional[list[str]]) -> list[str]:
    """
    Resolve agent directories: CLI args > env (LOCAL_AGENTS_DIR/AGENTS_DIR) > default.

    When neither --agent-dir nor env is set, uses AWORLD_DEFAULT_AGENT_DIR (default: ./agents).

    Args:
        cli_agent_dirs: List from --agent-dir (None or [] when not specified).

    Returns:
        Non-empty list of directory paths.

    Example:
        >>> _resolve_agent_dirs(None)  # no CLI, no env -> ["./agents"]
        ["./agents"]
        >>> _resolve_agent_dirs(["./my_agents"])  # CLI wins
        ["./my_agents"]
    """
    if cli_agent_dirs:
        return [d.strip() for d in cli_agent_dirs if d and d.strip()]
    env_val = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR") or ""
    if env_val:
        return [d.strip() for d in env_val.split(";") if d.strip()]
    default = os.getenv("AWORLD_DEFAULT_AGENT_DIR", "./agents")
    return [default.strip()] if default.strip() else ["./agents"]


def _help_texts() -> tuple[str, str, str, str]:
    english_epilog = """
Examples:

Basic Usage:
  # Interactive mode (default: Aworld agent)
  aworld-cli
  
  # Use different agent
  aworld-cli --agent developer
  
  # List available agents
  aworld-cli list

Direct Run Mode:
  # Direct run mode with task
  aworld-cli --task "add unit tests" --agent MyAgent --max-runs 5
  
  # Run with cost limit
  aworld-cli --task "refactor code" --agent MyAgent --max-cost 10.00
  
  # Run with duration limit
  aworld-cli --task "add features" --agent MyAgent --max-duration 2h
  
  # Force an installed skill for this task
  aworld-cli --task "review this PR" --agent MyAgent --skill code-review

Remote Backends:
  # Use remote backend
  aworld-cli --remote-backend http://localhost:8000 list
  
  # Use multiple remote backends
  aworld-cli --remote-backend http://localhost:8000 --remote-backend http://localhost:8001 list

Agent Directories:
  # Use agent directory
  aworld-cli --agent-dir ./agents list
  
  # Use multiple agent directories
  aworld-cli --agent-dir ./agents --agent-dir ./more_agents list

Agent Files:
  # Use single agent file
  aworld-cli --agent-file ./my_agent.py list
  
  # Use multiple agent files
  aworld-cli --agent-file ./agent1.py --agent-file ./agent2.md list
  
  # Direct run with single agent file (auto-detect agent name)
  aworld-cli --task "test" --agent-file ./my_agent.py
  
  # Direct run with multiple agent files (must specify --agent)
  aworld-cli --task "test" --agent MyAgent --agent-file ./agent1.py --agent-file ./agent2.md
  
  # Direct run with agent name (explicit)
  aworld-cli --task "test" --agent MyAgent --agent-file ./my_agent.py

Skill Sources:
  # Use skill sources from command line
  aworld-cli --skill-path ./skills --skill-path https://github.com/user/repo list
  
  # Use multiple skill sources
  aworld-cli --skill-path ./skills --skill-path ../custom-skills --skill-path https://github.com/user/repo list

Combined Options:
  # Combine all options
  aworld-cli --agent-dir ./agents --agent-file ./custom_agent.py --remote-backend http://localhost:8000 --skill-path ./skills list

Server Mode:
  # Start HTTP server
  aworld-cli serve --http --http-port 8000
  
  # Start MCP server (stdio mode)
  aworld-cli serve --mcp
  
  # Start MCP server (streamable-http mode)
  aworld-cli serve --mcp --mcp-transport streamable-http --mcp-port 8001
  
  # Start both HTTP and MCP servers
  aworld-cli serve --http --http-port 8000 --mcp --mcp-transport streamable-http --mcp-port 8001
  
  # Start server with custom agent directory
  aworld-cli serve --http --agent-dir ./agents

Plugin Management:
  # Install a plugin from GitHub
  aworld-cli plugins install my-plugin --url https://github.com/user/repo
  
  # Install a plugin from local path
  aworld-cli plugins install local-plugin --local-path ./local/plugin
  
  # Install with force (overwrite existing)
  aworld-cli plugins install my-plugin --url https://github.com/user/repo --force
  
  # List installed plugins
  aworld-cli plugins list
  
  # Remove a plugin
  aworld-cli plugins remove my-plugin

Skill Management:
  # Install skills from a local directory
  aworld-cli skill install ./local-skills

  # Install skills from git
  aworld-cli skill install https://github.com/user/repo.git

  # List installed skill packages
  aworld-cli skill list

  # Remove or update an installed skill package
  aworld-cli skill remove my-skills
  aworld-cli skill update my-skills
"""

    chinese_epilog = """
示例：

基本用法：
  # 交互模式（默认）
  aworld-cli
  
  # 列出可用的 agents
  aworld-cli list

直接运行模式：
  # 使用任务直接运行
  aworld-cli --task "add unit tests" --agent MyAgent --max-runs 5
  
  # 带成本限制运行
  aworld-cli --task "refactor code" --agent MyAgent --max-cost 10.00
  
  # 带时长限制运行
  aworld-cli --task "add features" --agent MyAgent --max-duration 2h
  
  # 使用本地图片文件运行
  aworld-cli --task "分析这张图片 @photo.jpg" --agent MyAgent
  
  # 使用远程图片 URL 运行
  aworld-cli --task "分析这张图片 @https://example.com/image.png" --agent MyAgent
  
  # 显式指定本次任务使用的 skill
  aworld-cli --task "review this PR" --agent MyAgent --skill code-review

远程后端：
  # 使用远程后端
  aworld-cli --remote-backend http://localhost:8000 list
  
  # 使用多个远程后端
  aworld-cli --remote-backend http://localhost:8000 --remote-backend http://localhost:8001 list

Agent 目录：
  # 使用 agent 目录
  aworld-cli --agent-dir ./agents list
  
  # 使用多个 agent 目录
  aworld-cli --agent-dir ./agents --agent-dir ./more_agents list

Agent 文件：
  # 使用单个 agent 文件
  aworld-cli --agent-file ./my_agent.py list
  
  # 使用多个 agent 文件
  aworld-cli --agent-file ./agent1.py --agent-file ./agent2.md list
  
  # 使用单个 agent 文件直接运行（自动检测 agent 名称）
  aworld-cli --task "test" --agent-file ./my_agent.py
  
  # 使用多个 agent 文件直接运行（必须指定 --agent）
  aworld-cli --task "test" --agent MyAgent --agent-file ./agent1.py --agent-file ./agent2.md
  
  # 显式指定 agent 名称直接运行
  aworld-cli --task "test" --agent MyAgent --agent-file ./my_agent.py

技能源：
  # 从命令行使用技能源
  aworld-cli --skill-path ./skills --skill-path https://github.com/user/repo list
  
  # 使用多个技能源
  aworld-cli --skill-path ./skills --skill-path ../custom-skills --skill-path https://github.com/user/repo list

组合选项：
  # 组合所有选项
  aworld-cli --agent-dir ./agents --agent-file ./custom_agent.py --remote-backend http://localhost:8000 --skill-path ./skills list

批量任务：
  # 使用 YAML 配置运行批量任务
  aworld-cli batch-job batch.yaml

Batch Jobs:
  # Run batch job with YAML config
  aworld-cli batch-job batch.yaml

服务器模式：
  # 启动 HTTP 服务器
  aworld-cli serve --http --http-port 8000
  
  # 启动 MCP 服务器（stdio 模式）
  aworld-cli serve --mcp
  
  # 启动 MCP 服务器（streamable-http 模式）
  aworld-cli serve --mcp --mcp-transport streamable-http --mcp-port 8001
  
  # 同时启动 HTTP 和 MCP 服务器
  aworld-cli serve --http --http-port 8000 --mcp --mcp-transport streamable-http --mcp-port 8001
  
  # 使用自定义 agent 目录启动服务器
  aworld-cli serve --http --agent-dir ./agents

插件管理：
  # 从 GitHub 安装插件
  aworld-cli plugins install my-plugin --url https://github.com/user/repo
  
  # 从本地路径安装插件
  aworld-cli plugins install local-plugin --local-path ./local/plugin
  
  # 强制安装（覆盖已存在的插件）
  aworld-cli plugins install my-plugin --url https://github.com/user/repo --force
  
  # 列出已安装的插件
  aworld-cli plugins list
  
  # 移除插件
  aworld-cli plugins remove my-plugin

技能包管理：
  # 从本地目录安装技能包
  aworld-cli skill install ./local-skills

  # 从 git 安装技能包
  aworld-cli skill install https://github.com/user/repo.git

  # 列出已安装的技能包
  aworld-cli skill list

  # 移除或更新已安装的技能包
  aworld-cli skill remove my-skills
  aworld-cli skill update my-skills
"""

    description_en = "AWorld Agent CLI - Interact with agents directly from the terminal"
    description_zh = "AWorld Agent CLI - 从终端直接与 agents 交互"
    return english_epilog, chinese_epilog, description_en, description_zh


def build_parser(zh: bool = False) -> argparse.ArgumentParser:
    _, _, description_en, description_zh = _help_texts()
    parser = argparse.ArgumentParser(
        description=description_zh if zh else description_en,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-zh",
        "--zh",
        action="store_true",
        help="显示中文帮助" if zh else "Show help in Chinese / 显示中文帮助",
    )
    parser.add_argument(
        "--examples",
        action="store_true",
        help="显示使用示例" if zh else "Show usage examples / 显示使用示例",
    )
    parser.add_argument(
        "--no-banner",
        action="store_true",
        help="启动时不显示 banner" if zh else "Disable banner display on startup / 启动时不显示 banner",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="interactive",
        choices=_build_parser_command_choices(),
        help=(
            '要执行的命令（默认：interactive）。使用 "serve" 启动 HTTP/MCP 服务器，使用 "batch-job" 运行批量任务，使用 "plugins" 管理插件，使用 "skill" 管理已安装技能包，使用 "gateway" 管理网关。'
            if zh
            else 'Command to execute (default: interactive). Use "serve" to start HTTP/MCP servers, '
            '"batch-job" to run batch jobs, "plugins" to manage plugins, "skill" to manage installed skills, and "gateway" to manage the gateway.'
        ),
    )
    parser.add_argument("--task", type=str, help="发送给 agent 的任务（非交互模式）" if zh else "Task to send to agent (non-interactive mode)")
    parser.add_argument("--agent", type=str, help="要使用的 agent 名称（直接运行模式必需）" if zh else "Agent name (default: Aworld in interactive mode; required for direct run mode)")
    parser.add_argument("--skill", dest="skill", action="append", help="显式请求一个已安装的 skill 名称。可重复传入。" if zh else "Explicitly request an installed skill by name. Can be passed multiple times.")
    parser.add_argument("--max-runs", type=int, help="最大运行次数（直接运行模式）" if zh else "Maximum number of runs (for direct run mode)")
    parser.add_argument("--max-cost", type=float, help="最大成本（美元）（直接运行模式）" if zh else "Maximum cost in USD (for direct run mode)")
    parser.add_argument("--max-duration", type=str, help='最大时长（例如："1h", "30m", "2h30m"）（直接运行模式）' if zh else 'Maximum duration (e.g., "1h", "30m", "2h30m") (for direct run mode)')
    parser.add_argument("--completion-signal", type=str, help="查找的完成信号字符串（直接运行模式）" if zh else "Completion signal string to look for (for direct run mode)")
    parser.add_argument("--completion-threshold", type=int, default=3, help="需要的连续完成信号数量（默认：3）" if zh else "Number of consecutive completion signals needed (default: 3)")
    parser.add_argument("--session_id", "--session-id", type=str, dest="session_id", help="要使用的会话 ID（直接运行模式）" if zh else "Session ID to use for this task (for direct run mode)")
    parser.add_argument("--non-interactive", action="store_true", help="以非交互模式运行（无用户输入）" if zh else "Run in non-interactive mode (no user input)")
    parser.add_argument("--env-file", type=str, default=".env", help=".env 文件路径（默认：.env）" if zh else "Path to .env file (default: .env)")
    parser.add_argument("--remote-backend", type=str, action="append", help="远程后端 URL（可指定多次）。覆盖 REMOTE_AGENT_BACKEND 环境变量。" if zh else "Remote backend URL (can be specified multiple times). Overrides REMOTE_AGENT_BACKEND environment variable.")
    parser.add_argument("--agent-dir", type=str, action="append", help="包含 agents 的目录（可指定多次）。未指定时默认使用 LOCAL_AGENTS_DIR 或 AWORLD_DEFAULT_AGENT_DIR（默认 ./agents）。" if zh else "Directory containing agents (can be specified multiple times). Default: LOCAL_AGENTS_DIR or AWORLD_DEFAULT_AGENT_DIR (./agents) when not set.")
    parser.add_argument("--agent-file", type=str, action="append", help="单个 agent 文件路径（Python .py 或 Markdown .md，可指定多次）。" if zh else "Individual agent file path (Python .py or Markdown .md, can be specified multiple times).")
    parser.add_argument("--skill-path", type=str, action="append", help="技能源路径（本地目录或 GitHub URL，可指定多次）。覆盖 SKILLS_PATH 环境变量。" if zh else "Skill source path (local directory or GitHub URL, can be specified multiple times). Overrides SKILLS_PATH environment variable.")
    parser.add_argument("--config", action="store_true", help="启动交互式全局配置编辑器（模型提供商、API 密钥等）并退出。" if zh else "Launch interactive global configuration editor (model provider, API key, etc.) and exit.")
    return parser


def _build_top_level_command_registry() -> TopLevelCommandRegistry:
    registry = TopLevelCommandRegistry(
        reserved_names={
            "interactive",
            "list",
            "serve",
            "gateway",
        }
    )
    register_builtin_top_level_commands(registry)

    try:
        from .core.plugin_manager import PluginManager, get_builtin_plugin_roots

        builtin_plugin_roots = tuple(
            Path(root).resolve() for root in get_builtin_plugin_roots()
        )
        plugin_manager = PluginManager()
        if hasattr(plugin_manager, "get_runtime_plugin_roots"):
            plugin_roots = [
                Path(root).resolve() for root in plugin_manager.get_runtime_plugin_roots()
            ]
        else:
            plugin_roots = list(builtin_plugin_roots)
    except Exception:
        from .core.plugin_manager import get_builtin_plugin_roots

        builtin_plugin_roots = tuple(
            Path(root).resolve() for root in get_builtin_plugin_roots()
        )
        plugin_roots = list(builtin_plugin_roots)

    try:
        sync_plugin_cli_commands(
            registry,
            discover_plugins(plugin_roots),
            builtin_plugin_roots=builtin_plugin_roots,
        )
    except Exception:
        pass

    return registry


def _build_parser_command_choices() -> list[str]:
    command_names = ["interactive", "batch", "batch-job", "plugins"]
    registry = _build_top_level_command_registry()
    for command in registry.list_commands(include_hidden=False):
        if command.name not in command_names:
            command_names.append(command.name)
    return command_names


_GLOBAL_OPTIONS_WITH_VALUES = {
    "--task",
    "--agent",
    "--skill",
    "--max-runs",
    "--max-cost",
    "--max-duration",
    "--completion-signal",
    "--completion-threshold",
    "--session_id",
    "--session-id",
    "--env-file",
    "--remote-backend",
    "--agent-dir",
    "--agent-file",
    "--skill-path",
    "--http-host",
    "--http-port",
    "--mcp-name",
    "--mcp-transport",
    "--mcp-host",
    "--mcp-port",
}


def _find_top_level_command_index(argv: list[str], registry: TopLevelCommandRegistry) -> int | None:
    index = 1 if argv else 0

    while index < len(argv):
        token = argv[index]
        if token in _GLOBAL_OPTIONS_WITH_VALUES:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        if registry.canonical_name(token) is not None:
            return index
        return None

    return None


def _maybe_dispatch_top_level_command(argv: list[str]) -> bool:
    if len(argv) < 2:
        return False

    registry = _build_top_level_command_registry()
    command_index = _find_top_level_command_index(argv, registry)
    if command_index is None:
        return False

    canonical_name = registry.canonical_name(argv[command_index])
    command = registry.get(argv[command_index])
    if command is None:
        return False

    parser = argparse.ArgumentParser(prog="aworld-cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for item in registry.list_commands():
        item.register_parser(subparsers)

    parse_argv = list(argv[command_index:])
    if canonical_name is not None:
        parse_argv[0] = canonical_name

    try:
        args = parser.parse_args(parse_argv)
    except SystemExit:
        return True

    selected_command = registry.get(getattr(args, "command", ""))
    if selected_command is None:
        return False

    return _run_top_level_command(selected_command, args, argv)


def _run_top_level_command(command, args, argv: list[str]) -> bool:
    exit_code = command.run(
        args,
        TopLevelCommandContext(cwd=str(Path.cwd()), argv=tuple(argv)),
    )
    if exit_code not in (None, 0):
        sys.exit(exit_code)
    return True


def _dispatch_named_top_level_command(
    command_name: str,
    args,
    argv: list[str],
) -> bool:
    registry = _build_top_level_command_registry()
    command = registry.get(command_name)
    if command is None:
        return False
    return _run_top_level_command(command, args, argv)


def main():
    """
    Entry point for the AWorld CLI.
    Supports both interactive and non-interactive (direct run) modes.
    """
    # Check for --no-banner flag early (before parsing)
    show_banner_flag = "--no-banner" not in sys.argv
    
    english_epilog, chinese_epilog, _, _ = _help_texts()

    if _maybe_dispatch_top_level_command(sys.argv):
        return

    parser = build_parser()
    # Parse arguments normally, but keep unknown args for inner plugin commands
    args, remaining_argv = parser.parse_known_args()

    # Handle --config: run interactive config editor and exit
    if getattr(args, 'config', False):
        async def _run_config():
            cli = AWorldCLI()
            await cli._interactive_config_editor()
        asyncio.run(_run_config())
        return
    
    # Handle --examples flag: show examples and exit
    if args.examples:
        examples_text = chinese_epilog if args.zh else english_epilog
        title = "AWorld CLI 使用示例" if args.zh else "AWorld CLI Usage Examples"
        print(f"\n{title}")
        print("=" * len(title))
        print(examples_text)
        return
    
    # Handle -zh flag: if specified, show Chinese help and exit
    if args.zh:
        build_parser(zh=True).print_help()
        return

    if not args.task and args.command == "interactive":
        if _dispatch_named_top_level_command("interactive", args, sys.argv):
            return

    if args.task:
        if _dispatch_named_top_level_command("run", args, sys.argv):
            return
    
    try:
        bootstrap_runtime(
            env_file=args.env_file,
            skill_paths=args.skill_path,
            show_banner=show_banner_flag,
            init_middlewares_fn=init_middlewares,
            show_banner_fn=_show_banner,
        )
    except RuntimeBootstrapError:
        sys.exit(1)

    # Resolve default agent_dir when --agent-dir not specified (env LOCAL_AGENTS_DIR / AWORLD_DEFAULT_AGENT_DIR)
    args.agent_dir = _resolve_agent_dirs(args.agent_dir)

    # Interactive mode (default) - use AgentRuntime directly without AWorldApp
    agent_name = args.agent or "Aworld"
    asyncio.run(_run_interactive_mode(
        agent_name=agent_name,
        requested_skill_names=args.skill,
        remote_backends=args.remote_backend,
        local_dirs=args.agent_dir,
        agent_files=args.agent_file
    ))


async def _run_interactive_mode(
    agent_name: Optional[str] = None,
    requested_skill_names: Optional[list[str]] = None,
    remote_backends: Optional[list[str]] = None,
    local_dirs: Optional[list[str]] = None,
    agent_files: Optional[list[str]] = None
):
    """
    Run interactive mode using CliRuntime directly.
    
    Args:
        agent_name: Agent name to use at startup (default: Aworld; override with --agent)
        remote_backends: Optional list of remote backend URLs
        local_dirs: Optional list of local agent directories
        agent_files: Optional list of individual agent file paths
    """
    # Load individual agent files first if provided
    if agent_files:
        from .core.loader import init_agent_file
        for agent_file in agent_files:
            try:
                init_agent_file(agent_file)
            except Exception as e:
                print(f"⚠️ Failed to load agent file {agent_file}: {e}")
    
    runtime = CliRuntime(agent_name=agent_name, remote_backends=remote_backends, local_dirs=local_dirs)
    runtime.cli._pending_skill_overrides = list(requested_skill_names or [])
    try:
        await runtime.start()
    except KeyboardInterrupt:
        pass
    finally:
        await runtime.stop()


async def _run_serve_mode(
    http: bool = False,
    http_host: str = "0.0.0.0",
    http_port: int = 8000,
    mcp: bool = False,
    mcp_name: str = "AWorldAgent",
    mcp_transport: str = "stdio",
    mcp_host: str = "0.0.0.0",
    mcp_port: int = 8001,
    remote_backends: Optional[list[str]] = None,
    local_dirs: Optional[list[str]] = None,
    agent_files: Optional[list[str]] = None
) -> None:
    """
    Run server mode: start HTTP and/or MCP servers.
    
    Args:
        http: Whether to start HTTP server
        http_host: HTTP server host
        http_port: HTTP server port
        mcp: Whether to start MCP server
        mcp_name: MCP server name
        mcp_transport: MCP transport type (stdio, sse, or streamable-http)
        mcp_host: MCP server host (for SSE/streamable-http)
        mcp_port: MCP server port (for SSE/streamable-http)
        remote_backends: Optional list of remote backend URLs
        local_dirs: Optional list of local agent directories
        agent_files: Optional list of individual agent file paths
    """
    # Load individual agent files first if provided
    if agent_files:
        from .core.loader import init_agent_file
        for agent_file in agent_files:
            try:
                init_agent_file(agent_file)
            except Exception as e:
                print(f"⚠️ Failed to load agent file {agent_file}: {e}")
    
    # Load agents to ensure they are registered
    print("🔄 Loading agents...")
    all_agents = await load_all_agents(
        remote_backends=remote_backends,
        local_dirs=local_dirs,
        agent_files=agent_files
    )
    
    if all_agents:
        print(f"✅ Loaded {len(all_agents)} agent(s): {', '.join([a.name for a in all_agents])}")
    else:
        print("⚠️ No agents loaded. Servers will start but may not have any agents available.")
    
    # Import protocols
    from .protocal.http import HttpProtocol
    from .protocal.mcp import McpProtocol
    
    protocols = []
    
    # Create HTTP protocol if requested
    if http:
        http_protocol = HttpProtocol(
            host=http_host,
            port=http_port,
            title="AWorld Agent Server",
            version="1.0.0"
        )
        protocols.append(http_protocol)
        print(f"🌐 HTTP server will start on http://{http_host}:{http_port}")
    
    # Create MCP protocol if requested
    if mcp:
        mcp_kwargs = {
            "name": mcp_name,
            "transport": mcp_transport
        }
        if mcp_transport in ["sse", "streamable-http"]:
            mcp_kwargs["host"] = mcp_host
            mcp_kwargs["port"] = mcp_port
            print(f"📡 MCP server will start in {mcp_transport} mode on {mcp_host}:{mcp_port}")
        else:
            print(f"📡 MCP server will start in {mcp_transport} mode")
        
        mcp_protocol = McpProtocol(**mcp_kwargs)
        protocols.append(mcp_protocol)
    
    if not protocols:
        print("❌ Error: No protocols to start")
        return
    
    # Start all protocols concurrently
    print("\n🚀 Starting servers...")
    print("Press Ctrl+C to stop all servers\n")
    
    try:
        # Start all protocols
        start_tasks = [protocol.start() for protocol in protocols]
        await asyncio.gather(*start_tasks)
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down servers...")
    finally:
        # Stop all protocols
        stop_tasks = [protocol.stop() for protocol in protocols]
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        print("✅ All servers stopped")


async def _run_direct_mode(
    prompt: str,
    agent_name: str,
    requested_skill_names: Optional[list[str]] = None,
    max_runs: Optional[int] = None,
    max_cost: Optional[float] = None,
    max_duration: Optional[str] = None,
    completion_signal: Optional[str] = None,
    completion_threshold: int = 3,
    non_interactive: bool = False,
    session_id: Optional[str] = None,
    remote_backends: Optional[list[str]] = None,
    local_dirs: Optional[list[str]] = None,
    agent_files: Optional[list[str]] = None
) -> None:
    """
    Run agent in direct mode (non-interactive).
    
    Args:
        prompt: User prompt (may contain @ file references for images, supports both local files and remote URLs)
        agent_name: Agent name
        max_runs: Maximum number of runs (default: 1 if not specified)
        max_cost: Maximum cost in USD
        max_duration: Maximum duration (e.g., "1h", "30m")
        completion_signal: Completion signal string
        completion_threshold: Number of consecutive completion signals needed
        non_interactive: Whether to run in non-interactive mode
        session_id: Optional session ID to use for this direct run. If provided, the executor will
            restore or create this session before running.
        remote_backends: Optional list of remote backend URLs
        local_dirs: Optional list of local agent directories
        agent_files: Optional list of individual agent file paths
    """
    from ._globals import console

    # Load individual agent files first if provided
    if agent_files:
        from .core.loader import init_agent_file
        for agent_file in agent_files:
            try:
                init_agent_file(agent_file)
            except Exception as e:
                print(f"⚠️ Failed to load agent file {agent_file}: {e}")
    
    # Use CliRuntime to load agents and create executor
    runtime = CliRuntime(
        remote_backends=remote_backends, 
        local_dirs=local_dirs,
        session_id=session_id
    )
    all_agents = await runtime._load_agents()

    # Find the requested agent
    selected_agent = None
    for agent in all_agents:
        if agent.name == agent_name:
            selected_agent = agent
            break
    
    if not selected_agent:
        print(f"❌ Error: Agent '{agent_name}' not found")
        return
    
    # Create agent executor using CliRuntime (session_id is already passed to runtime)
    from aworld.core.scheduler import get_scheduler
    runtime._scheduler = get_scheduler()
    runtime._bind_scheduler_default_agent(selected_agent.name)
    agent_executor = await runtime._create_executor(selected_agent)

    if not agent_executor:
        print(f"❌ Error: Failed to create executor for agent '{agent_name}'")
        return
    
    # If session_id was provided, ensure it's properly restored (for session history management)
    if session_id and hasattr(agent_executor, 'restore_session'):
        try:
            # Restore session to ensure it's added to history if needed
            agent_executor.restore_session(session_id)
        except Exception:
            # If restore fails, session_id was already set during executor creation
            pass
    
    # Default to 10 runs if max_runs is not specified (allow multi-step tasks)
    if max_runs is None:
        max_runs = 10
    
    # File parsing is now handled by FileParseHook automatically
    # Just pass the prompt as-is, the hook will process @filename references
    # For direct mode, we still need to handle the format for backward compatibility
    # but FileParseHook will do the actual parsing
    multimodal_prompt = prompt

    # Create continuous executor and run
    # Use global console to ensure consistent output across all components
    # Ensure agent_executor uses the global console for output rendering
    if hasattr(agent_executor, 'console'):
        agent_executor.console = console

    continuous_executor = ContinuousExecutor(agent_executor, console=console)
    
    # Run task execution
    await continuous_executor.run_continuous(
        prompt=multimodal_prompt,
        agent_name=agent_name,
        requested_skill_names=requested_skill_names,
        max_runs=max_runs,
        max_cost=max_cost,
        max_duration=max_duration,
        completion_signal=completion_signal,
        completion_threshold=completion_threshold
    )


if __name__ == "__main__":
    main()
