"""
Command-line entry point for aworld-cli.
Provides CLI interface without requiring aworldappinfra.
"""
import argparse
import os
import sys
import time
import asyncio
from typing import Optional

# Set environment variable to disable console logging before importing aworld modules
# This ensures all AWorldLogger instances will disable console output
os.environ['AWORLD_DISABLE_CONSOLE_LOG'] = 'true'

# Import aworld modules (they will respect the environment variable)
from aworld.logs.util import logger
from .runtime.cli import CliRuntime
from .console import AWorldCLI
from .models import AgentInfo
from .executors.continuous import ContinuousExecutor


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
    
    # Use CliRuntime to load agents (it handles all sources)
    # Create a temporary runtime instance just for loading agents
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


def main():
    """
    Entry point for the AWorld CLI.
    Supports both interactive and non-interactive (direct run) modes.
    """
    # English help text
    english_epilog = """
Examples:

Basic Usage:
  # Interactive mode (default)
  aworld-cli
  
  # List available agents
  aworld-cli list

Direct Run Mode:
  # Direct run mode with task
  aworld-cli --task "add unit tests" --agent MyAgent --max-runs 5
  
  # Run with cost limit
  aworld-cli --task "refactor code" --agent MyAgent --max-cost 10.00
  
  # Run with duration limit
  aworld-cli --task "add features" --agent MyAgent --max-duration 2h

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
  aworld-cli plugin install my-plugin --url https://github.com/user/repo
  
  # Install a plugin from local path
  aworld-cli plugin install local-plugin --local-path ./local/plugin
  
  # Install with force (overwrite existing)
  aworld-cli plugin install my-plugin --url https://github.com/user/repo --force
  
  # List installed plugins
  aworld-cli plugin list
  
  # Remove a plugin
  aworld-cli plugin remove my-plugin
"""
    
    # Chinese help text
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
  aworld-cli plugin install my-plugin --url https://github.com/user/repo
  
  # 从本地路径安装插件
  aworld-cli plugin install local-plugin --local-path ./local/plugin
  
  # 强制安装（覆盖已存在的插件）
  aworld-cli plugin install my-plugin --url https://github.com/user/repo --force
  
  # 列出已安装的插件
  aworld-cli plugin list
  
  # 移除插件
  aworld-cli plugin remove my-plugin
"""
    
    description_en = "AWorld Agent CLI - Interact with agents directly from the terminal"
    description_zh = "AWorld Agent CLI - 从终端直接与 agents 交互"
    
    parser = argparse.ArgumentParser(
        description=description_en,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '-zh', '--zh',
        action='store_true',
        help='Show help in Chinese / 显示中文帮助'
    )
    
    parser.add_argument(
        '--examples',
        action='store_true',
        help='Show usage examples / 显示使用示例'
    )

    # Create a minimal parser to check if command is 'plugin'
    minimal_parser = argparse.ArgumentParser(add_help=False)
    minimal_parser.add_argument('command', nargs='?', default='interactive')
    minimal_args, _ = minimal_parser.parse_known_args()

    # Handle plugin command specially
    if minimal_args.command == "plugin":
        plugin_parser = argparse.ArgumentParser(description="Plugin management commands", prog="aworld-cli plugin")
        plugin_subparsers = plugin_parser.add_subparsers(dest='plugin_action', help='Plugin action to perform', required=True)

        # install subcommand
        install_parser = plugin_subparsers.add_parser('install', help='Install a plugin')
        install_parser.add_argument('plugin_name', help='Name of the plugin to install')
        install_parser.add_argument('--url', type=str, help='Plugin repository URL (GitHub or other git URL)')
        install_parser.add_argument('--local-path', type=str, help='Local plugin path')
        install_parser.add_argument('--force', action='store_true', help='Force reinstall/overwrite existing plugin')

        # remove subcommand
        remove_parser = plugin_subparsers.add_parser('remove', help='Remove a plugin')
        remove_parser.add_argument('plugin_name', help='Name of the plugin to remove')

        # list subcommand
        list_parser = plugin_subparsers.add_parser('list', help='List installed plugins')

        # Parse plugin subcommand arguments
        try:
            plugin_args = plugin_parser.parse_args()
        except SystemExit:
            return

        # Handle plugin commands
        from .core.plugin_manager import PluginManager

        manager = PluginManager()

        if plugin_args.plugin_action == "install":
            if not plugin_args.url and not plugin_args.local_path:
                print("❌ Error: Either --url or --local-path must be provided")
                install_parser.print_help()
                return

            try:
                success = manager.install(
                    plugin_name=plugin_args.plugin_name,
                    url=plugin_args.url,
                    local_path=plugin_args.local_path,
                    force=plugin_args.force
                )
                if success:
                    print(f"✅ Plugin '{plugin_args.plugin_name}' installed successfully")
                    print(f"📍 Location: {manager.plugin_dir / plugin_args.plugin_name}")
                else:
                    print(f"❌ Failed to install plugin '{plugin_args.plugin_name}'")
            except Exception as e:
                print(f"❌ Error installing plugin: {e}")
                return

        elif plugin_args.plugin_action == "remove":
            success = manager.remove(plugin_args.plugin_name)
            if not success:
                return

        elif plugin_args.plugin_action == "list":
            plugins = manager.list_plugins()

            if not plugins:
                print("📦 No plugins installed")
                print(f"📍 Plugin directory: {manager.plugin_dir}")
                return

            print(f"📦 Installed plugins ({len(plugins)}):")
            print(f"📍 Plugin directory: {manager.plugin_dir}\n")

            from rich.console import Console
            from rich.table import Table

            console = Console()
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Name", style="cyan")
            table.add_column("Source", style="green")
            table.add_column("Has Agents", justify="center")
            table.add_column("Has Skills", justify="center")
            table.add_column("Path", style="dim")

            for plugin in plugins:
                table.add_row(
                    plugin['name'],
                    plugin['source'],
                    "✅" if plugin['has_agents'] else "❌",
                    "✅" if plugin['has_skills'] else "❌",
                    plugin['path']
                )

            console.print(table)

        return

    # Continue with normal argument parsing for other commands
    parser.add_argument(
        'command',
        nargs='?',
        default='interactive',
        choices=['interactive', 'list', 'serve', 'batch', 'batch-job'],
        help='Command to execute (default: interactive). Use "serve" to start HTTP/MCP servers, '
             '"batch-job" to run batch jobs.'
    )
    
    parser.add_argument(
        '--task',
        type=str,
        help='Task to send to agent (non-interactive mode)'
    )
    
    parser.add_argument(
        '--agent',
        type=str,
        help='Agent name to use (required for direct run mode)'
    )
    
    parser.add_argument(
        '--max-runs',
        type=int,
        help='Maximum number of runs (for direct run mode)'
    )
    
    parser.add_argument(
        '--max-cost',
        type=float,
        help='Maximum cost in USD (for direct run mode)'
    )
    
    parser.add_argument(
        '--max-duration',
        type=str,
        help='Maximum duration (e.g., "1h", "30m", "2h30m") (for direct run mode)'
    )
    
    parser.add_argument(
        '--completion-signal',
        type=str,
        help='Completion signal string to look for (for direct run mode)'
    )
    
    parser.add_argument(
        '--completion-threshold',
        type=int,
        default=3,
        help='Number of consecutive completion signals needed (default: 3)'
    )
    
    parser.add_argument(
        '--session_id',
        '--session-id',
        type=str,
        dest='session_id',
        help='Session ID to use for this task (for direct run mode)'
    )
    
    parser.add_argument(
        '--non-interactive',
        action='store_true',
        help='Run in non-interactive mode (no user input)'
    )
    
    parser.add_argument(
        '--env-file',
        type=str,
        default='.env',
        help='Path to .env file (default: .env)'
    )
    
    parser.add_argument(
        '--remote-backend',
        type=str,
        action='append',
        help='Remote backend URL (can be specified multiple times). Overrides REMOTE_AGENT_BACKEND environment variable.'
    )
    
    parser.add_argument(
        '--agent-dir',
        type=str,
        action='append',
        help='Directory containing agents (can be specified multiple times). Default: LOCAL_AGENTS_DIR or AWORLD_DEFAULT_AGENT_DIR (./agents) when not set.'
    )
    
    parser.add_argument(
        '--agent-file',
        type=str,
        action='append',
        help='Individual agent file path (Python .py or Markdown .md, can be specified multiple times).'
    )
    
    parser.add_argument(
        '--skill-path',
        type=str,
        action='append',
        help='Skill source path (local directory or GitHub URL, can be specified multiple times). Overrides SKILLS_PATH environment variable.'
    )
    
    # Server options (for 'serve' command)
    parser.add_argument(
        '--http',
        action='store_true',
        help='Start HTTP server (for serve command)'
    )
    
    parser.add_argument(
        '--http-host',
        type=str,
        default='0.0.0.0',
        help='HTTP server host (default: 0.0.0.0)'
    )
    
    parser.add_argument(
        '--http-port',
        type=int,
        default=8000,
        help='HTTP server port (default: 8000)'
    )
    
    parser.add_argument(
        '--mcp',
        action='store_true',
        help='Start MCP server (for serve command)'
    )
    
    parser.add_argument(
        '--mcp-name',
        type=str,
        default='AWorldAgent',
        help='MCP server name (default: AWorldAgent)'
    )
    
    parser.add_argument(
        '--mcp-transport',
        type=str,
        choices=['stdio', 'sse', 'streamable-http'],
        default='stdio',
        help='MCP transport type: stdio, sse, or streamable-http (default: stdio)'
    )
    
    parser.add_argument(
        '--mcp-host',
        type=str,
        default='0.0.0.0',
        help='MCP server host for SSE/streamable-http transport (default: 0.0.0.0)'
    )
    
    parser.add_argument(
        '--mcp-port',
        type=int,
        default=8001,
        help='MCP server port for SSE/streamable-http transport (default: 8001)'
    )
    
    parser.add_argument(
        '--config',
        action='store_true',
        help='Launch interactive global configuration editor (model provider, API key, etc.) and exit.'
    )
    
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
        parser_zh = argparse.ArgumentParser(
            description=description_zh,
            formatter_class=argparse.RawDescriptionHelpFormatter
        )
        parser_zh.add_argument('-zh', '--zh', action='store_true', help='显示中文帮助')
        parser_zh.add_argument('--examples', action='store_true', help='显示使用示例')
        parser_zh.add_argument('command', nargs='?', default='interactive', choices=['interactive', 'list', 'serve', 'batch', 'batch-job', 'plugin'], help='要执行的命令（默认：interactive）。使用 "serve" 启动 HTTP/MCP 服务器，使用 "batch-job" 运行批量任务，使用 "plugin" 管理插件。')
        parser_zh.add_argument('--task', type=str, help='发送给 agent 的任务（非交互模式）')
        parser_zh.add_argument('--agent', type=str, help='要使用的 agent 名称（直接运行模式必需）')
        parser_zh.add_argument('--max-runs', type=int, help='最大运行次数（直接运行模式）')
        parser_zh.add_argument('--max-cost', type=float, help='最大成本（美元）（直接运行模式）')
        parser_zh.add_argument('--max-duration', type=str, help='最大时长（例如："1h", "30m", "2h30m"）（直接运行模式）')
        parser_zh.add_argument('--completion-signal', type=str, help='查找的完成信号字符串（直接运行模式）')
        parser_zh.add_argument('--completion-threshold', type=int, default=3, help='需要的连续完成信号数量（默认：3）')
        parser_zh.add_argument('--session_id', '--session-id', type=str, dest='session_id', help='要使用的会话 ID（直接运行模式）')
        parser_zh.add_argument('--non-interactive', action='store_true', help='以非交互模式运行（无用户输入）')
        parser_zh.add_argument('--env-file', type=str, default='.env', help='.env 文件路径（默认：.env）')
        parser_zh.add_argument('--remote-backend', type=str, action='append', help='远程后端 URL（可指定多次）。覆盖 REMOTE_AGENT_BACKEND 环境变量。')
        parser_zh.add_argument('--agent-dir', type=str, action='append', help='包含 agents 的目录（可指定多次）。未指定时默认使用 LOCAL_AGENTS_DIR 或 AWORLD_DEFAULT_AGENT_DIR（默认 ./agents）。')
        parser_zh.add_argument('--agent-file', type=str, action='append', help='单个 agent 文件路径（Python .py 或 Markdown .md，可指定多次）。')
        parser_zh.add_argument('--skill-path', type=str, action='append', help='技能源路径（本地目录或 GitHub URL，可指定多次）。覆盖 SKILLS_PATH 环境变量。')
        parser_zh.add_argument('--http', action='store_true', help='启动 HTTP 服务器（用于 serve 命令）')
        parser_zh.add_argument('--http-host', type=str, default='0.0.0.0', help='HTTP 服务器主机（默认：0.0.0.0）')
        parser_zh.add_argument('--http-port', type=int, default=8000, help='HTTP 服务器端口（默认：8000）')
        parser_zh.add_argument('--mcp', action='store_true', help='启动 MCP 服务器（用于 serve 命令）')
        parser_zh.add_argument('--mcp-name', type=str, default='AWorldAgent', help='MCP 服务器名称（默认：AWorldAgent）')
        parser_zh.add_argument('--mcp-transport', type=str, choices=['stdio', 'sse', 'streamable-http'], default='stdio', help='MCP 传输类型：stdio、sse 或 streamable-http（默认：stdio）')
        parser_zh.add_argument('--mcp-host', type=str, default='0.0.0.0', help='MCP 服务器主机（用于 SSE/streamable-http 传输，默认：0.0.0.0）')
        parser_zh.add_argument('--mcp-port', type=int, default=8001, help='MCP 服务器端口（用于 SSE/streamable-http 传输，默认：8001）')
        parser_zh.add_argument('--config', action='store_true', help='启动交互式全局配置编辑器（模型提供商、API 密钥等）并退出。')
        parser_zh.print_help()
        return
    
    # Load configuration (priority: local .env > global config)
    from .core.config import load_config_with_env, has_model_config
    config_dict, source_type, source_path = load_config_with_env(args.env_file)
    
    # Display configuration source
    from ._globals import console
    if source_type == "local":
        console.print(f"[dim]📁 Using local config: {source_path}[/dim]")
    else:
        console.print(f"[dim]🌐 Using global config: {source_path}[/dim]")
    
    # Require model config for commands that use the agent (skip for 'list' and plugin)
    if args.command != "list" and not has_model_config(config_dict):
        console.print("[yellow]No model configuration (API key, etc.) detected. Please configure before starting.[/yellow]")
        console.print("[dim]Run: aworld-cli --config[/dim]")
        sys.exit(1)
    
    # Initialize skill registry early with command-line arguments (overrides env vars)
    # This ensures skill registry is ready before agents are loaded
    from ._globals import console
    from .core.skill_registry import get_skill_registry

    if args.skill_path:
        # Initialize registry with command-line skill paths (these take precedence)
        registry = get_skill_registry(skill_paths=args.skill_path)
    else:
        # Still initialize registry to load from env vars and defaults
        registry = get_skill_registry()
    
    # Display global skills loading information
    all_skills = registry.get_all_skills()
    if all_skills:
        skill_names = list(all_skills.keys())
        logger.info("Loaded %d global skill(s): %s", len(skill_names), ", ".join(skill_names))

    # Resolve default agent_dir when --agent-dir not specified (env LOCAL_AGENTS_DIR / AWORLD_DEFAULT_AGENT_DIR)
    args.agent_dir = _resolve_agent_dirs(args.agent_dir)

    # Handle 'list' command separately before setting up the full app loop if possible
    if args.command == "list":
        cli = AWorldCLI()
        all_agents = asyncio.run(load_all_agents(
            remote_backends=args.remote_backend,
            local_dirs=args.agent_dir,
            agent_files=args.agent_file
        ))
        
        # Display agents
        if all_agents:
            cli.display_agents(all_agents)
        else:
            print("❌ No agents found from any source.")
        return
    
    # Handle 'serve' command: start HTTP and/or MCP servers
    if args.command == "serve":
        if not args.http and not args.mcp:
            print("❌ Error: At least one of --http or --mcp must be specified for serve command")
            parser.print_help()
            return
        
        asyncio.run(_run_serve_mode(
            http=args.http,
            http_host=args.http_host,
            http_port=args.http_port,
            mcp=args.mcp,
            mcp_name=args.mcp_name,
            mcp_transport=args.mcp_transport,
            mcp_host=args.mcp_host,
            mcp_port=args.mcp_port,
            remote_backends=args.remote_backend,
            local_dirs=args.agent_dir,
            agent_files=args.agent_file
        ))
        return

    # Handle inner plugin commands (e.g. 'batch-job', 'batch')
    from .inner_plugins.batch import get_commands as get_batch_commands
    batch_commands = get_batch_commands()
    if args.command in batch_commands:
        handler = batch_commands[args.command]
        # remaining_argv already contains arguments that were not parsed by the main parser
        exit_code = handler(remaining_argv)
        # Ensure consistent process exit code behavior
        if exit_code != 0:
            sys.exit(exit_code)
        return

    # Handle direct run mode (参考 continuous-claude)
    if args.task:
        # Auto-detect agent name from agent_file if only one file is specified
        agent_name = args.agent
        if not agent_name and args.agent_file:
            if len(args.agent_file) == 1:
                # Load the single agent file to get its name
                from .core.loader import init_agent_file
                try:
                    agent_name = init_agent_file(args.agent_file[0])
                    if not agent_name:
                        print(f"❌ Error: Could not extract agent name from {args.agent_file[0]}")
                        parser.print_help()
                        return
                    print(f"ℹ️  Auto-detected agent name: {agent_name}")
                except Exception as e:
                    print(f"❌ Error: Failed to load agent file {args.agent_file[0]}: {e}")
                    return
            else:
                print("❌ Error: --agent is required when using multiple --agent-file")
                parser.print_help()
                return
        elif not agent_name:
            # Default to "Aworld" agent if no agent is specified
            agent_name = "Aworld"
            print(f"ℹ️  Using default agent: {agent_name}")
        
        asyncio.run(_run_direct_mode(
            prompt=args.task,
            agent_name=agent_name,
            max_runs=args.max_runs,
            max_cost=args.max_cost,
            max_duration=args.max_duration,
            completion_signal=args.completion_signal,
            completion_threshold=args.completion_threshold,
            non_interactive=args.non_interactive,
            session_id=args.session_id,
            remote_backends=args.remote_backend,
            local_dirs=args.agent_dir,
            agent_files=args.agent_file
        ))
        return

    # Interactive mode (default) - use AgentRuntime directly without AWorldApp
    asyncio.run(_run_interactive_mode(
        remote_backends=args.remote_backend,
        local_dirs=args.agent_dir,
        agent_files=args.agent_file
    ))


async def _run_interactive_mode(
    remote_backends: Optional[list[str]] = None,
    local_dirs: Optional[list[str]] = None,
    agent_files: Optional[list[str]] = None
):
    """
    Run interactive mode using CliRuntime directly.
    
    Args:
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
    
    runtime = CliRuntime(remote_backends=remote_backends, local_dirs=local_dirs)
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
    
    # Default to 1 run if max_runs is not specified
    if max_runs is None:
        max_runs = 1
    
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
        max_runs=max_runs,
        max_cost=max_cost,
        max_duration=max_duration,
        completion_signal=completion_signal,
        completion_threshold=completion_threshold
    )


if __name__ == "__main__":
    main()
