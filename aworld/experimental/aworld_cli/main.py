"""
Command-line entry point for aworld-cli.
Provides CLI interface without requiring aworldappinfra.
"""


import argparse
import os
import sys

# Set environment variable to disable console logging before importing aworld modules
# This ensures all AWorldLogger instances will disable console output
os.environ['AWORLD_DISABLE_CONSOLE_LOG'] = 'true'
import asyncio
from typing import Optional

# Now import aworld modules (they will respect the environment variable)
from .runtime.mixed import MixedRuntime
from .console import AWorldCLI
from .models import AgentInfo
from .executors.continuous import ContinuousExecutor
from rich.console import Console


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
        choices=['interactive', 'list'],
        help='Command to execute (default: interactive)'
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
        parser_zh.add_argument('command', nargs='?', default='interactive', choices=['interactive', 'list'], help='要执行的命令（默认：interactive）')
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


if __name__ == "__main__":
    main()
