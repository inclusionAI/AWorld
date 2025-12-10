"""
Command-line entry point for aworld-cli.
Provides CLI interface without requiring aworldappinfra.
"""


import argparse
import os

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


def main():
    from dotenv import load_dotenv

    """
    Entry point for the AWorld CLI.
    Supports both interactive and non-interactive (direct run) modes.
    """
    parser = argparse.ArgumentParser(
        description="AWorld Agent CLI - Interact with agents directly from the terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode (default)
  aworld-cli
  
  # List available agents
  aworld-cli list
  
  # Direct run mode with prompt
  aworld-cli --prompt "add unit tests" --agent MyAgent --max-runs 5
  
  # Run with cost limit
  aworld-cli --prompt "refactor code" --agent MyAgent --max-cost 10.00
  
  # Run with duration limit
  aworld-cli --prompt "add features" --agent MyAgent --max-duration 2h
        """
    )
    
    parser.add_argument(
        'command',
        nargs='?',
        default='interactive',
        choices=['interactive', 'list'],
        help='Command to execute (default: interactive)'
    )
    
    parser.add_argument(
        '--prompt',
        type=str,
        help='Direct prompt to send to agent (non-interactive mode)'
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
    
    args = parser.parse_args()
    
    # Load environment variables
    load_dotenv(args.env_file)

    # Handle 'list' command separately before setting up the full app loop if possible
    if args.command == "list":
        cli = AWorldCLI()
        all_agents = []
        
        # Load from local directories
        local_dirs_str = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR") or ""
        local_dirs = [d.strip() for d in local_dirs_str.split(";") if d.strip()]
        
        # If no local dirs configured, use current working directory as default
        if not local_dirs:
            local_dirs = [os.getcwd()]
        
        for local_dir in local_dirs:
            try:
                # Try new @agent decorator first
                from .core.loader import init_agents as new_init_agents
                from .core.registry import LocalAgentRegistry
                new_init_agents(local_dir)
                local_agents = LocalAgentRegistry.list_agents()
                for agent in local_agents:
                    agent_info = AgentInfo.from_source(agent)
                    all_agents.append(agent_info)
            except Exception as e:
                print(f"⚠️ Failed to load with @agent decorator from {local_dir}: {e}")
            
        # Load from remote backend if configured
        remote_backend = os.getenv("REMOTE_AGENTS_BACKEND")
        if remote_backend:
            try:
                from .runtime.remote import RemoteRuntime
                remote_runtime = RemoteRuntime(backend_url=remote_backend)
                remote_agents = asyncio.run(remote_runtime.list_agents())
                for agent in remote_agents:
                    all_agents.append(agent)
            except Exception as e:
                print(f"⚠️ Failed to load remote agents: {e}")
        
        # Display agents
        if all_agents:
            cli.display_agents(all_agents)
        else:
            print("❌ No agents found from any source.")
            print("❌ No agents available.")
        return

    # Handle direct run mode (参考 continuous-claude)
    if args.prompt:
        if not args.agent:
            print("❌ Error: --agent is required when using --prompt")
            parser.print_help()
            return
        
        asyncio.run(_run_direct_mode(
            prompt=args.prompt,
            agent_name=args.agent,
            max_runs=args.max_runs,
            max_cost=args.max_cost,
            max_duration=args.max_duration,
            completion_signal=args.completion_signal,
            completion_threshold=args.completion_threshold,
            non_interactive=args.non_interactive
        ))
        return

    # Interactive mode (default) - use MixedRuntime directly without AWorldApp
    asyncio.run(_run_interactive_mode())


async def _run_interactive_mode():
    """Run interactive mode using MixedRuntime directly."""
    runtime = MixedRuntime()
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
    non_interactive: bool = False
) -> None:
    """
    Run agent in direct mode (non-interactive).
    
    Args:
        prompt: User prompt
        agent_name: Agent name
        max_runs: Maximum number of runs
        max_cost: Maximum cost in USD
        max_duration: Maximum duration (e.g., "1h", "30m")
        completion_signal: Completion signal string
        completion_threshold: Number of consecutive completion signals needed
        non_interactive: Whether to run in non-interactive mode
    """
    # Load agents (similar to list command)
    all_agents = []
    
    # Load from local directories
    local_dirs_str = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR") or ""
    local_dirs = [d.strip() for d in local_dirs_str.split(";") if d.strip()]
    
    # If no local dirs configured, use current working directory as default
    if not local_dirs:
        local_dirs = [os.getcwd()]
    
    for local_dir in local_dirs:
        try:
            from .core.loader import init_agents as new_init_agents
            from .core.registry import LocalAgentRegistry
            new_init_agents(local_dir)
            local_agents = LocalAgentRegistry.list_agents()
            for agent in local_agents:
                agent_info = AgentInfo.from_source(agent)
                all_agents.append(agent_info)
        except Exception as e:
            print(f"⚠️ Failed to load with @agent decorator from {local_dir}: {e}")
    
    # Find the requested agent
    selected_agent = None
    for agent in all_agents:
        if agent.name == agent_name:
            selected_agent = agent
            break
    
    if not selected_agent:
        print(f"❌ Error: Agent '{agent_name}' not found")
        return
    
    # Create executor and run
    executor = ContinuousExecutor(
        agent=selected_agent,
        max_runs=max_runs,
        max_cost=max_cost,
        max_duration=max_duration,
        completion_signal=completion_signal,
        completion_threshold=completion_threshold,
        non_interactive=non_interactive
    )
    
    await executor.run(prompt)


if __name__ == "__main__":
    main()
