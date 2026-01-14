"""
Command-line entry point for aworld-cli.
Provides CLI interface without requiring aworldappinfra.
"""


import argparse
import os
import sys
from datetime import datetime

from aworld.logs.util import logger
from train.integration.trl.composite_reward import eval_prompt

# Set environment variable to disable console logging before importing aworld modules
# This ensures all AWorldLogger instances will disable console output
os.environ['AWORLD_DISABLE_CONSOLE_LOG'] = 'true'
import asyncio
from typing import Optional, Any

# Now import aworld modules (they will respect the environment variable)
from .runtime.mixed import MixedRuntime
from .console import AWorldCLI
from .models import AgentInfo
from .executors.continuous import ContinuousExecutor
from rich.console import Console

# Import evaluation types for type hints
try:
    from aworld.evaluations.base import EvalResult
except ImportError:
    # Fallback if not available
    EvalResult = Any


async def load_all_agents(
    remote_backends: Optional[list[str]] = None,
    local_dirs: Optional[list[str]] = None,
    agent_files: Optional[list[str]] = None
) -> list[AgentInfo]:
    """
    Load all agents from local directories, agent files, and remote backends.
    
    This function uses MixedRuntime to load agents from:
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
    
    # Use MixedRuntime to load agents (it handles all sources and backward compatibility)
    # Create a temporary runtime instance just for loading agents
    runtime = MixedRuntime(remote_backends=remote_backends, local_dirs=local_dirs)
    return await runtime._load_agents()


def main():
    from dotenv import load_dotenv

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
    
    parser.add_argument(
        'command',
        nargs='?',
        default='interactive',
        choices=['interactive', 'list', 'serve', 'eval'],
        help='Command to execute (default: interactive). Use "serve" to start HTTP/MCP servers, "eval" for batch evaluation.'
    )
    
    parser.add_argument(
        'file_path_positional',
        nargs='?',
        help='File path for eval command (positional argument)'
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
        help='Directory containing agents (can be specified multiple times). Overrides LOCAL_AGENTS_DIR environment variable.'
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
    
    # Eval command options
    parser.add_argument(
        '--file-path',
        type=str,
        help='Path to CSV/JSONL file for batch evaluation (required for eval command)'
    )
    parser.add_argument(
        '--query-column',
        type=str,
        default='query',
        help='Column name in CSV file that contains the query/task content (default: query)'
    )
    parser.add_argument(
        '--parallel-num',
        type=int,
        default=10,
        help='Number of parallel evaluation tasks (default: 10)'
    )
    parser.add_argument(
        '--repeat-times',
        type=int,
        default=1,
        help='Number of times to repeat each evaluation case (default: 1)'
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default=None,
        help='Output directory for evaluation results (default: current directory)'
    )
    parser.add_argument(
        '--skip-passed',
        action='store_true',
        default=False,
        help='Skip cases that have already passed evaluation'
    )
    
    args = parser.parse_args()
    
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
        parser_zh.add_argument('command', nargs='?', default='interactive', choices=['interactive', 'list', 'serve'], help='要执行的命令（默认：interactive）。使用 "serve" 启动 HTTP/MCP 服务器。')
        parser_zh.add_argument('--task', type=str, help='发送给 agent 的任务（非交互模式）')
        parser_zh.add_argument('--agent', type=str, help='要使用的 agent 名称（直接运行模式必需）')
        parser_zh.add_argument('--max-runs', type=int, help='最大运行次数（直接运行模式）')
        parser_zh.add_argument('--max-cost', type=float, help='最大成本（美元）（直接运行模式）')
        parser_zh.add_argument('--max-duration', type=str, help='最大时长（例如："1h", "30m", "2h30m"）（直接运行模式）')
        parser_zh.add_argument('--completion-signal', type=str, help='查找的完成信号字符串（直接运行模式）')
        parser_zh.add_argument('--completion-threshold', type=int, default=3, help='需要的连续完成信号数量（默认：3）')
        parser_zh.add_argument('--non-interactive', action='store_true', help='以非交互模式运行（无用户输入）')
        parser_zh.add_argument('--env-file', type=str, default='.env', help='.env 文件路径（默认：.env）')
        parser_zh.add_argument('--remote-backend', type=str, action='append', help='远程后端 URL（可指定多次）。覆盖 REMOTE_AGENT_BACKEND 环境变量。')
        parser_zh.add_argument('--agent-dir', type=str, action='append', help='包含 agents 的目录（可指定多次）。覆盖 LOCAL_AGENTS_DIR 环境变量。')
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
        parser_zh.print_help()
        return
    
    # Load environment variables
    load_dotenv(args.env_file)
    
    # Initialize skill registry early with command-line arguments (overrides env vars)
    # This ensures skill registry is ready before agents are loaded
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
        print(f"📚 Loaded {len(skill_names)} global skill(s): {', '.join(skill_names)}")
    else:
        print("📚 No global skills loaded")

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
    
    # Handle 'eval' command: batch evaluation
    if args.command == "eval":
        # Support both positional argument and --file-path option
        file_path = args.file_path_positional or args.file_path
        if not file_path:
            print("❌ Error: File path is required for eval command (use positional argument or --file-path)")
            parser.print_help()
            return
        
        if not args.agent:
            print("❌ Error: --agent is required for eval command")
            parser.print_help()
            return
        
        # Determine remote backend
        remote_backend = None
        if args.remote_backend:
            remote_backend = args.remote_backend[0]  # Use first remote backend
        
        asyncio.run(_run_eval_mode(
            file_path=file_path,
            agent_name=args.agent,
            remote_backend=remote_backend,
            query_column=args.query_column,
            parallel_num=args.parallel_num,
            repeat_times=args.repeat_times,
            output_dir=args.output_dir,
            skip_passed=args.skip_passed,
            remote_backends=args.remote_backend,
            local_dirs=args.agent_dir,
            agent_files=args.agent_file
        ))
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
                print("❌ Error: --agent is required when using multiple --agent-file or when not using --agent-file")
                parser.print_help()
                return
        elif not agent_name:
            print("❌ Error: --agent is required when using --task (or use --agent-file with a single file)")
            parser.print_help()
            return
        
        asyncio.run(_run_direct_mode(
            prompt=args.task,
            agent_name=agent_name,
            max_runs=args.max_runs,
            max_cost=args.max_cost,
            max_duration=args.max_duration,
            completion_signal=args.completion_signal,
            completion_threshold=args.completion_threshold,
            non_interactive=args.non_interactive,
            remote_backends=args.remote_backend,
            local_dirs=args.agent_dir,
            agent_files=args.agent_file
        ))
        return

    # Interactive mode (default) - use MixedRuntime directly without AWorldApp
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
    Run interactive mode using MixedRuntime directly.
    
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
    
    runtime = MixedRuntime(remote_backends=remote_backends, local_dirs=local_dirs)
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
    remote_backends: Optional[list[str]] = None,
    local_dirs: Optional[list[str]] = None,
    agent_files: Optional[list[str]] = None
) -> None:
    """
    Run agent in direct mode (non-interactive).
    
    Args:
        prompt: User prompt
        agent_name: Agent name
        max_runs: Maximum number of runs (default: 1 if not specified)
        max_cost: Maximum cost in USD
        max_duration: Maximum duration (e.g., "1h", "30m")
        completion_signal: Completion signal string
        completion_threshold: Number of consecutive completion signals needed
        non_interactive: Whether to run in non-interactive mode
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
    
    # Use MixedRuntime to load agents and create executor
    runtime = MixedRuntime(remote_backends=remote_backends, local_dirs=local_dirs)
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
    
    # Create agent executor using MixedRuntime
    agent_executor = await runtime._create_executor(selected_agent)
    if not agent_executor:
        print(f"❌ Error: Failed to create executor for agent '{agent_name}'")
        return
    
    # Default to 1 run if max_runs is not specified
    if max_runs is None:
        max_runs = 1
    
    # Create continuous executor and run
    console = Console()
    continuous_executor = ContinuousExecutor(agent_executor, console=console)
    
    await continuous_executor.run_continuous(
        prompt=prompt,
        agent_name=agent_name,
        max_runs=max_runs,
        max_cost=max_cost,
        max_duration=max_duration,
        completion_signal=completion_signal,
        completion_threshold=completion_threshold
    )


async def _run_eval_mode(
    file_path: str,
    agent_name: str,
    remote_backend: Optional[str] = None,
    query_column: str = "query",
    parallel_num: int = 10,
    repeat_times: int = 1,
    output_dir: Optional[str] = None,
    skip_passed: bool = False,
    remote_backends: Optional[list[str]] = None,
    local_dirs: Optional[list[str]] = None,
    agent_files: Optional[list[str]] = None
) -> None:
    """
    Run batch evaluation on a dataset file.
    
    Args:
        file_path: Path to CSV/JSONL file containing evaluation data.
        agent_name: Name of the agent to use.
        remote_backend: Optional remote backend URL.
        query_column: Column name containing queries.
        parallel_num: Number of parallel tasks.
        repeat_times: Number of times to repeat each case.
        output_dir: Output directory for results.
        skip_passed: Whether to skip already passed cases.
        remote_backends: Optional list of remote backend URLs.
        local_dirs: Optional list of local agent directories.
        agent_files: Optional list of individual agent file paths.
    """
    import logging
    from aworld.core.context.amni.config import init_middlewares
    from aworld.runners.evaluate_runner import EvaluateRunner
    from aworld.config import EvaluationConfig, DataLoaderConfig
    from aworld.evaluations.base import EvalTask, EvalResult
    from .eval_target import AWorldCliEvalTarget
    
    # Initialize middlewares
    init_middlewares()
    
    # Setup output directory
    if output_dir is None:
        output_dir = os.getcwd()
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    
    # Build eval target
    eval_target = AWorldCliEvalTarget(
        agent_name=agent_name,
        remote_backend=remote_backend,
        query_column=query_column,
        output_dir=output_dir,
    )
    
    # Create evaluation task
    eval_task_id = f"eval_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    # Convert file path to absolute path
    abs_file_path = os.path.abspath(file_path)
    
    # Run evaluation
    logging.info(f"🚀 Starting evaluation on {abs_file_path}")
    logging.info(f"📊 Agent: {agent_name}, Query column: {query_column}, Parallel: {parallel_num}, Repeat: {repeat_times}")
    if remote_backend:
        logging.info(f"🌐 Remote backend: {remote_backend}")
    
    result: EvalResult = await EvaluateRunner(
        task=EvalTask(task_id=eval_task_id),
        config=EvaluationConfig(
            eval_target=eval_target,
            eval_criterias=[
                # {
                #     "metric_name": "answer_accuracy",
                #     "threshold": 0.5,
                # }
            ],
            eval_dataset_id_or_file_path=abs_file_path,
            eval_dataset_query_column=query_column,
            eval_dataset_load_config=DataLoaderConfig(),
            repeat_times=repeat_times,
            parallel_num=parallel_num,
            skip_passed_cases=skip_passed,
        )
    ).run()
    
    # Save results
    _save_eval_results(result, output_dir, eval_task_id)
    
    logging.info(f"✅ Evaluation completed! Results saved to {output_dir}")


def _save_eval_results(result: EvalResult, output_dir: str, eval_task_id: str):
    """
    Save evaluation results to file.
    
    Args:
        result: Evaluation result object.
        output_dir: Directory to save results.
        eval_task_id: Task ID for naming files.
    """
    result_file_path = os.path.join(output_dir, "results", eval_task_id)
    os.makedirs(result_file_path, exist_ok=True)
    
    result_file = os.path.join(result_file_path, "results.txt")
    with open(result_file, "w", encoding="utf-8") as f:
        f.write(f"{result.run_id}\n")
        f.write(f"START: {datetime.fromtimestamp(int(result.create_time)).strftime('%Y%m%d %H%M%S')}\n")
        f.write(f"END: {datetime.now().strftime('%Y%m%d %H%M%S')}\n")
        
        f.write(f"---------- SUMMARY --------------\n")
        if result.summary:
            for scorer_name, summary in result.summary.items():
                f.write(f"{scorer_name}: {summary}\n")
        f.write("\n")
        
        f.write("---------- DETAIL -------------\n")
        for case_result in result.eval_case_results:
            if not case_result.score_rows:
                continue

            logger.info(f"Case Result:{type(case_result)}: {case_result}")
            # Extract case ID
            case_id = case_result.eval_case_id
            input_id = case_result.input.get('case_data').get('id', 'N/A')
            
            # Extract scores
            score_info = []
            for scorer_name, scorer_result in case_result.score_rows.items():
                if scorer_name == 'TimeCostScorer':
                    time_cost = scorer_result.metric_results.get('predict_time_cost_ms', {})
                    if isinstance(time_cost, dict):
                        time_seconds = int(time_cost.get('value', 0) / 1000)
                        score_info.append(f"time:{time_seconds}s")
                else:
                    # Extract main metric
                    for metric_name, metric_result in scorer_result.metric_results.items():
                        if isinstance(metric_result, dict):
                            status = metric_result.get('eval_status', 'N/A')
                            value = metric_result.get('value', 'N/A')
                            score_info.append(f"{metric_name}:{value}({status})")
            
            f.write(f"{case_id}|{input_id}|{'|'.join(score_info)}\n")


if __name__ == "__main__":
    main()
