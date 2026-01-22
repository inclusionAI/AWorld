#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Debug script for aworld-cli main.py

This script allows you to debug the aworld-cli main function directly
without going through the command-line entry point.

In debug mode, it uses await instead of asyncio.run() to support debugging
in async environments (e.g., Jupyter notebooks, async debuggers).

Usage:
    # Debug interactive mode
    python debug_main.py
    
    # Debug list command
    python debug_main.py list
    
    # Debug direct run mode
    python debug_main.py --prompt "Hello" --agent SimpleAgent
    
    # In async environment (e.g., Jupyter notebook)
    await debug_main_async()
"""
import sys
import os
import asyncio
import argparse
from typing import Optional

# Add the project root to Python path so we can import aworld modules
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Set working directory to the demo directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# os.environ['AWORLD_DISABLE_CONSOLE_LOG'] = 'true'


async def debug_main_async():
    """
    Async version of main() for debugging.
    This allows using await in async environments (e.g., Jupyter notebooks).
    """
    from dotenv import load_dotenv
    from aworld.experimental.aworld_cli.console import AWorldCLI
    from aworld.experimental.aworld_cli.models import AgentInfo
    from aworld.experimental.aworld_cli.executors.continuous import ContinuousExecutor
    from aworld.experimental.aworld_cli.runtime.mixed import MixedRuntime
    from aworld.experimental.aworld_cli.core.loader import init_agents
    from aworld.experimental.aworld_cli.core.agent_registry import LocalAgentRegistry
    
    parser = argparse.ArgumentParser(
        description="AWorld Agent CLI - Debug Mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
    
    # Handle 'list' command
    if args.command == "list":
        print("🔧 Debug mode: Listing agents...")
        cli = AWorldCLI()
        all_agents = []
        
        # Load from local directories
        local_dirs_str = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR") or ""
        local_dirs = [d.strip() for d in local_dirs_str.split(";") if d.strip()]
        
        # If no local dirs configured, use current working directory as default
        if not local_dirs:
            local_dirs = [os.getcwd()]
        
        print(f"🔧 Debug mode: Loading agents from local directories: {local_dirs}")
        for local_dir in local_dirs:
            try:
                print(f"🔧 Debug mode: Initializing agents from {local_dir}...")
                init_agents(local_dir)
                local_agents = LocalAgentRegistry.list_agents()
                print(f"🔧 Debug mode: Found {len(local_agents)} agents in {local_dir}")
                for agent in local_agents:
                    agent_info = AgentInfo.from_source(agent)
                    all_agents.append(agent_info)
            except Exception as e:
                print(f"⚠️ Failed to load with @agent decorator from {local_dir}: {e}")
                import traceback
                traceback.print_exc()
        
        # Load from remote backend if configured
        remote_backend = os.getenv("REMOTE_AGENTS_BACKEND")
        if remote_backend:
            try:
                print(f"🔧 Debug mode: Loading agents from remote backend: {remote_backend}")
                from aworld.experimental.aworld_cli.runtime.remote import RemoteRuntime
                remote_runtime = RemoteRuntime(backend_url=remote_backend)
                remote_agents = await remote_runtime.list_agents()
                print(f"🔧 Debug mode: Found {len(remote_agents)} remote agents")
                for agent in remote_agents:
                    all_agents.append(agent)
            except Exception as e:
                print(f"⚠️ Failed to load remote agents: {e}")
                import traceback
                traceback.print_exc()
        
        # Display agents
        print(f"🔧 Debug mode: Total agents found: {len(all_agents)}")
        if all_agents:
            cli.display_agents(all_agents)
        else:
            print("❌ No agents found from any source.")
            print("❌ No agents available.")
        return
    
    # Handle direct run mode
    if args.prompt:
        print(f"🔧 Debug mode: Direct run mode with prompt: {args.prompt}")
        if not args.agent:
            print("❌ Error: --agent is required when using --prompt")
            parser.print_help()
            return
        
        all_agents = []
        
        # Load from local directories
        local_dirs_str = os.getenv("LOCAL_AGENTS_DIR") or os.getenv("AGENTS_DIR") or ""
        local_dirs = [d.strip() for d in local_dirs_str.split(";") if d.strip()]
        
        if not local_dirs:
            local_dirs = [os.getcwd()]
        
        print(f"🔧 Debug mode: Loading agents from local directories: {local_dirs}")
        for local_dir in local_dirs:
            try:
                print(f"🔧 Debug mode: Initializing agents from {local_dir}...")
                init_agents(local_dir)
                local_agents = LocalAgentRegistry.list_agents()
                print(f"🔧 Debug mode: Found {len(local_agents)} agents in {local_dir}")
                for agent in local_agents:
                    agent_info = AgentInfo.from_source(agent)
                    all_agents.append(agent_info)
            except Exception as e:
                print(f"⚠️ Failed to load with @agent decorator from {local_dir}: {e}")
                import traceback
                traceback.print_exc()
        
        # Find the requested agent
        print(f"🔧 Debug mode: Looking for agent: {args.agent}")
        selected_agent = None
        for agent in all_agents:
            if agent.name == args.agent:
                selected_agent = agent
                break
        
        if not selected_agent:
            print(f"❌ Error: Agent '{args.agent}' not found")
            print(f"🔧 Debug mode: Available agents: {[a.name for a in all_agents]}")
            return
        
        print(f"🔧 Debug mode: Found agent: {selected_agent.name}")
        # Create executor and run
        print(f"🔧 Debug mode: Creating executor...")
        executor = ContinuousExecutor(
            agent=selected_agent,
            max_runs=args.max_runs,
            max_cost=args.max_cost,
            max_duration=args.max_duration,
            completion_signal=args.completion_signal,
            completion_threshold=args.completion_threshold,
            non_interactive=args.non_interactive
        )
        
        print(f"🔧 Debug mode: Running executor...")
        await executor.run(args.prompt)
        print(f"🔧 Debug mode: Executor completed.")
        return
    
    # Interactive mode (default)
    print("🔧 Debug mode: Starting interactive runtime...")
    runtime = MixedRuntime()
    try:
        print("🔧 Debug mode: Calling runtime.start()...")
        await runtime.start()
        print("🔧 Debug mode: runtime.start() completed.")
    except KeyboardInterrupt:
        print("\n🔧 Debug mode: Interrupted by user")
        pass
    except Exception as e:
        print(f"🔧 Debug mode: Error in runtime.start(): {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("🔧 Debug mode: Stopping runtime...")
        await runtime.stop()
        print("🔧 Debug mode: Runtime stopped.")


def debug_main():
    """
    Synchronous wrapper for debug_main_async().
    In debug mode, we use asyncio.run() but with better error handling.
    If you're already in an async context, call debug_main_async() directly with await.
    """
    try:
        # Check if we're already in an event loop
        loop = asyncio.get_running_loop()
        # If we're here, we're in an async context
        print("⚠️  Already in an event loop. Use 'await debug_main_async()' instead.")
        print("   Or run this script directly (not from within an async function).")
        return
    except RuntimeError:
        # No running event loop, safe to use asyncio.run()
        pass
    
    # Use asyncio.run() to execute the async function
    try:
        asyncio.run(debug_main_async())
    except KeyboardInterrupt:
        print("\n👋 Interrupted by user")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # Use await-compatible version in debug mode
    debug_main()

