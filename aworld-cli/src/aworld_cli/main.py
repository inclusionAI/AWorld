"""
Command-line entry point for aworld-cli.
Provides CLI interface without requiring aworldappinfra.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional

from aworld.plugins.discovery import discover_plugins

from .async_runtime import (
    DirectRunDeadlineExceeded,
    run_with_first_provider_start_watchdog,
)
from .run_outcome import (
    DirectRunErrorCode,
    DirectRunOutcome,
    DirectRunStage,
    DirectRunStatus,
)


_AWORLD_PRE_PROVIDER_MAX_ATTEMPTS = 2
_CONTROL_DETAIL_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_LOGGER = logging.getLogger(__name__)


def _direct_run_summary(value: object) -> dict | None:
    if isinstance(value, DirectRunOutcome):
        return value.summary
    return value if isinstance(value, dict) else None


def _direct_run_has_provider_evidence(summary: object) -> bool:
    """Return whether a direct run captured evidence that execution reached the model."""
    summary = _direct_run_summary(summary)
    if summary is None:
        return False
    for result in summary.get("results") or []:
        if not isinstance(result, dict):
            continue
        trajectory = result.get("trajectory")
        if isinstance(trajectory, list) and trajectory:
            return True
        llm_calls = result.get("llm_calls")
        if isinstance(llm_calls, list) and llm_calls:
            return True
    return False


def _live_provider_evidence_cursor(agent_executor: object) -> tuple[object, int]:
    """Snapshot explicit provider-boundary evidence for the active task."""

    context = getattr(agent_executor, "context", None)
    calls: object = None
    get_calls = getattr(context, "get_reconciled_llm_calls", None)
    if not callable(get_calls):
        get_calls = getattr(context, "get_llm_calls", None)
    if callable(get_calls):
        # Let probe failures reach the watchdog's fail-open boundary.  Treating
        # an observability exception as zero evidence turns a healthy task into
        # a false provider-start timeout.
        calls = get_calls()
        if not isinstance(calls, list):
            raise TypeError("provider evidence probe must return a list")
    elif context is not None:
        context_info = getattr(context, "context_info", None)
        if isinstance(context_info, dict):
            calls = context_info.get("llm_calls")
    task_id = getattr(context, "task_id", None)
    provider_evidence_count = sum(
        1
        for record in (calls if isinstance(calls, list) else ())
        if isinstance(record, dict)
        and (task_id is None or record.get("task_id") == task_id)
        and (
            record.get("provider_invoked") is True
            or record.get("provider_attempt_status") == "attempted"
        )
    )
    return context, provider_evidence_count


def _new_live_provider_evidence(
    agent_executor: object,
    *,
    cursor: tuple[object, int],
) -> bool:
    """Detect the current task's first explicit provider invocation."""

    initial_context, initial_count = cursor
    context, evidence_count = _live_provider_evidence_cursor(agent_executor)
    return evidence_count > 0 and (
        context is not initial_context or evidence_count > initial_count
    )


def _initial_live_provider_evidence_cursor(
    agent_executor: object,
) -> tuple[object, int] | None:
    """Capture the baseline or disable the watchdog when observation is broken."""

    try:
        return _live_provider_evidence_cursor(agent_executor)
    except Exception as exc:
        # Provider evidence is advisory. An unavailable initial snapshot must
        # not turn a healthy task into an orchestration failure or timeout.
        _LOGGER.warning(
            "Direct-run initial provider-evidence probe failed open; error_type=%s",
            type(exc).__name__,
        )
        return None


def _direct_run_succeeded(summary: object) -> bool:
    """Return whether the direct executor completed at least one successful run."""
    summary = _direct_run_summary(summary)
    if summary is None:
        return False
    results = summary.get("results") or []
    return bool(results) and all(
        isinstance(result, dict) and bool(result.get("success"))
        for result in results
    )


def _direct_run_cancelled(summary: object) -> bool:
    """Return whether an executor preserved a typed cancellation signal."""

    summary = _direct_run_summary(summary)
    if summary is None:
        return False
    return any(
        isinstance(result, dict)
        and (
            result.get("termination_status") == "cancelled"
            or result.get("failure_origin") == "cancelled"
            or result.get("task_status") in {"cancelled", "interrupted"}
        )
        for result in summary.get("results") or []
    )


def _direct_run_has_explicit_task_failure(summary: object) -> bool:
    """Return whether every failed result is explicitly agent/task-owned."""

    summary = _direct_run_summary(summary)
    if summary is None:
        return False
    results = summary.get("results") or []
    if not isinstance(results, list) or not results or any(
        not isinstance(result, dict) for result in results
    ):
        return False
    failed_results = [result for result in results if not bool(result.get("success"))]
    return bool(failed_results) and all(
        result.get("failure_origin") == "task" for result in failed_results
    )


def _direct_run_infrastructure_failure(summary: object) -> dict[str, str] | None:
    """Return typed infrastructure evidence without inspecting response text."""

    summary = _direct_run_summary(summary)
    if summary is None:
        return None
    for result in summary.get("results") or []:
        if not isinstance(result, dict):
            continue
        if result.get("failure_origin") != "infrastructure":
            continue
        evidence: dict[str, str] = {}
        for key in ("failure_code", "error_type"):
            value = result.get(key)
            if isinstance(value, str) and _CONTROL_DETAIL_IDENTIFIER.fullmatch(value):
                evidence[key] = value
        return evidence
    return None


def _trajectory_from_direct_run_summary(
    summary: object,
    *,
    prompt: str,
    agent_name: str,
) -> list[dict]:
    summary = _direct_run_summary(summary)
    if summary is None:
        return []
    trajectory: list[dict] = []
    for index, result in enumerate(summary.get("results") or [], start=1):
        if not isinstance(result, dict):
            continue
        response = result.get("response")
        content = response if isinstance(response, str) else str(response)
        success = bool(result.get("success"))
        completed = bool(result.get("completed"))
        trajectory.append(
            {
                "meta": {
                    "step": int(result.get("iteration") or index),
                    "agent_id": agent_name,
                    "pre_agent": "runner",
                },
                "state": {"input": {"content": prompt}},
                "action": {
                    "content": content,
                    "is_agent_finished": "True" if completed else "False",
                    "tool_calls": [],
                },
                "reward": {"status": "ok" if success else "failed"},
            }
        )
    return trajectory


def _validated_complete_llm_usage_summary(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    call_count = value.get("call_count")
    usage_call_count = value.get("usage_call_count")
    total_tokens = value.get("total_tokens")
    if (
        value.get("schema_version") != "aworld.llm_usage_summary.v1"
        or value.get("coverage_complete") is not True
        or value.get("ledger_consistent") is not True
        or isinstance(call_count, bool)
        or not isinstance(call_count, int)
        or call_count <= 0
        or usage_call_count != call_count
        or isinstance(total_tokens, bool)
        or not isinstance(total_tokens, int)
        or total_tokens < 0
    ):
        return None
    normalized = {
        "schema_version": "aworld.llm_usage_summary.v1",
        "call_count": call_count,
        "usage_call_count": usage_call_count,
        "total_tokens": total_tokens,
        "coverage_complete": True,
        "ledger_consistent": True,
    }
    input_tokens = value.get("input_tokens")
    output_tokens = value.get("output_tokens")
    if (
        not isinstance(input_tokens, bool)
        and isinstance(input_tokens, int)
        and input_tokens >= 0
        and not isinstance(output_tokens, bool)
        and isinstance(output_tokens, int)
        and output_tokens >= 0
    ):
        normalized["input_tokens"] = input_tokens
        normalized["output_tokens"] = output_tokens
    return normalized


def _complete_llm_usage_from_direct_run_summary(summary: dict) -> dict | None:
    results = summary.get("results")
    if not isinstance(results, list) or not results:
        return None
    summaries: list[dict] = []
    for result in results:
        if not isinstance(result, dict):
            return None
        usage = _validated_complete_llm_usage_summary(result.get("llm_usage"))
        if usage is None:
            return None
        summaries.append(usage)
    aggregate = {
        "schema_version": "aworld.llm_usage_summary.v1",
        "call_count": sum(item["call_count"] for item in summaries),
        "usage_call_count": sum(
            item["usage_call_count"] for item in summaries
        ),
        "total_tokens": sum(item["total_tokens"] for item in summaries),
        "coverage_complete": True,
        "ledger_consistent": True,
        "iteration_count": len(summaries),
    }
    if all("input_tokens" in item and "output_tokens" in item for item in summaries):
        aggregate["input_tokens"] = sum(
            item["input_tokens"] for item in summaries
        )
        aggregate["output_tokens"] = sum(
            item["output_tokens"] for item in summaries
        )
    return aggregate


def _trajectory_payload_from_direct_run_summary(
    summary: object,
    *,
    prompt: str,
    agent_name: str,
) -> dict:
    summary = _direct_run_summary(summary)
    if summary is not None:
        task_response_trajectory: list[dict] = []
        llm_calls: list[dict] = []
        trajectory_build_results: list[dict] = []
        saw_task_response_capture = False
        capture_modes: list[str] = []
        fidelities: list[str] = []
        raw_summary_activation_evidence = summary.get(
            "skill_activation_evidence"
        )
        skill_activation_evidence: list[dict] = (
            [
                item
                for item in raw_summary_activation_evidence
                if isinstance(item, dict)
            ]
            if isinstance(raw_summary_activation_evidence, list)
            else []
        )
        for result in summary.get("results") or []:
            if not isinstance(result, dict):
                continue
            trajectory = result.get("trajectory")
            if isinstance(trajectory, list):
                saw_task_response_capture = True
                task_response_trajectory.extend(
                    item for item in trajectory if isinstance(item, dict)
                )
            raw_llm_calls = result.get("llm_calls")
            if isinstance(raw_llm_calls, list):
                saw_task_response_capture = True
                llm_calls.extend(item for item in raw_llm_calls if isinstance(item, dict))
            if result.get("trajectory_capture_mode") == "task_response":
                saw_task_response_capture = True
            capture_mode = result.get("trajectory_capture_mode")
            if isinstance(capture_mode, str) and capture_mode:
                capture_modes.append(capture_mode)
            raw_build_result = result.get("trajectory_build_result")
            if isinstance(raw_build_result, dict):
                saw_task_response_capture = True
                trajectory_build_results.append(raw_build_result)
                if raw_build_result.get("fidelity"):
                    fidelities.append(str(raw_build_result["fidelity"]))
            elif callable(getattr(raw_build_result, "to_dict", None)):
                saw_task_response_capture = True
                serialized_build_result = raw_build_result.to_dict()
                if isinstance(serialized_build_result, dict):
                    trajectory_build_results.append(serialized_build_result)
                    if serialized_build_result.get("fidelity"):
                        fidelities.append(str(serialized_build_result["fidelity"]))
            raw_activation_evidence = result.get("skill_activation_evidence")
            if isinstance(raw_activation_evidence, list):
                skill_activation_evidence.extend(
                    item
                    for item in raw_activation_evidence
                    if isinstance(item, dict)
                )

        if saw_task_response_capture:
            payload = {
                "trajectory": task_response_trajectory,
                "trajectory_capture_mode": (
                    "live_context"
                    if "live_context" in capture_modes
                    else "task_response"
                ),
            }
            payload["llm_calls"] = llm_calls
            if trajectory_build_results:
                payload["trajectory_build_results"] = trajectory_build_results
            if any(value in {"partial", "placeholder", "build_failed"} for value in fidelities):
                payload["trajectory_fidelity"] = "partial"
            elif fidelities and all(value == "complete" for value in fidelities):
                payload["trajectory_fidelity"] = "complete"
            llm_usage = _complete_llm_usage_from_direct_run_summary(summary)
            if llm_usage is not None:
                payload["llm_usage"] = llm_usage
            if llm_calls:
                payload["llm_calls"] = llm_calls
            if skill_activation_evidence:
                unique_activation_evidence: list[dict] = []
                for item in skill_activation_evidence:
                    if item not in unique_activation_evidence:
                        unique_activation_evidence.append(item)
                payload["skill_activation_evidence"] = unique_activation_evidence
            return payload

    return {
        "trajectory": _trajectory_from_direct_run_summary(
            summary,
            prompt=prompt,
            agent_name=agent_name,
        ),
        "trajectory_capture_mode": "summary_synthetic",
    }

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


def _judge_config_from_cli_selectors(
    *,
    judge_agent: str | None = None,
    judge_agent_name: str | None = None,
    judge_backend_ref: str | None = None,
    judge_model_profile: str | None = None,
):
    selector_count = sum(
        bool(value) for value in (judge_agent, judge_agent_name, judge_backend_ref)
    )
    if selector_count > 1:
        raise ValueError("use only one of --judge-agent, --judge-agent-name, or --judge-backend-ref")
    if selector_count == 0:
        return None

    from aworld.config.conf import SelfEvolveJudgeConfig

    if judge_agent:
        return SelfEvolveJudgeConfig(
            mode="agent_md",
            agent_path=judge_agent,
            model_profile=judge_model_profile,
        )
    if judge_agent_name:
        return SelfEvolveJudgeConfig(
            mode="custom_agent",
            agent_id=judge_agent_name,
            model_profile=judge_model_profile,
        )
    return SelfEvolveJudgeConfig(
        mode="backend_ref",
        backend_ref=judge_backend_ref,
        model_profile=judge_model_profile,
    )


def _self_evolve_config_from_cli_mode(
    mode: str | None,
    *,
    judge_agent: str | None = None,
    judge_agent_name: str | None = None,
    judge_backend_ref: str | None = None,
    judge_model_profile: str | None = None,
):
    if mode is None:
        return None
    normalized = mode.strip().lower()
    judge_config = _judge_config_from_cli_selectors(
        judge_agent=judge_agent,
        judge_agent_name=judge_agent_name,
        judge_backend_ref=judge_backend_ref,
        judge_model_profile=judge_model_profile,
    )
    if normalized in {"off", "offline"}:
        from aworld.config.conf import SelfEvolveConfig

        kwargs = {"judge_config": judge_config} if judge_config is not None else {}
        return SelfEvolveConfig(mode="off", apply_policy="proposal", **kwargs)
    if normalized == "shadow":
        from aworld.config.conf import SelfEvolveConfig

        kwargs = {"judge_config": judge_config} if judge_config is not None else {}
        return SelfEvolveConfig(mode="shadow", apply_policy="proposal", **kwargs)
    if normalized == "online":
        from aworld.config.conf import SelfEvolveConfig

        kwargs = {"judge_config": judge_config} if judge_config is not None else {}
        return SelfEvolveConfig(mode="online", apply_policy="auto_verified", **kwargs)
    raise ValueError("--evolve must be one of: off, shadow, online")


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
                print(
                    "⚠️ Failed to load an agent file "
                    f"({type(e).__name__}); path and exception text were omitted"
                )
    
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


def print_usage_examples(*, zh: bool = False) -> None:
    english_epilog, chinese_epilog, _, _ = _help_texts()
    examples_text = chinese_epilog if zh else english_epilog
    title = "AWorld CLI 使用示例" if zh else "AWorld CLI Usage Examples"
    print(f"\n{title}")
    print("=" * len(title))
    print(examples_text)


def print_help_text(*, zh: bool = False) -> None:
    build_parser(zh=zh).print_help()


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
    parser.add_argument(
        "--evolve",
        nargs="?",
        const="shadow",
        choices=("off", "offline", "shadow", "online"),
        default=None,
        help=(
            "为当前 CLI 会话启用后台 self-evolve；--evolve 等同 shadow，--evolve=online 允许 auto_verified apply。"
            if zh
            else "Enable background self-evolve for this CLI session; --evolve means shadow, --evolve=online allows auto_verified apply."
        ),
    )
    parser.add_argument("--judge-agent", type=str, help="self-evolve judge agent markdown path used with --evolve." if zh else "Self-evolve judge agent markdown path used with --evolve.")
    parser.add_argument("--judge-agent-name", type=str, help="self-evolve judge agent name used with --evolve." if zh else "Self-evolve judge agent name used with --evolve.")
    parser.add_argument("--judge-backend-ref", type=str, help="self-evolve judge backend reference used with --evolve." if zh else "Self-evolve judge backend reference used with --evolve.")
    parser.add_argument("--judge-model-profile", type=str, help="model profile for the self-evolve judge used with --evolve." if zh else "Model profile for the self-evolve judge used with --evolve.")
    parser.add_argument("--emit-trajectory", action="store_true", help="任务完成后输出 ATIF trajectory JSON。" if zh else "Emit ATIF trajectory JSON after the task completes.")
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
    command_names = ["interactive", "batch", "batch-job", "plugins", "acp"]
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
    "--judge-agent",
    "--judge-agent-name",
    "--judge-backend-ref",
    "--judge-model-profile",
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

    selected_command = registry.get(canonical_name or getattr(args, "command", ""))
    if selected_command is None:
        return False

    return _run_top_level_command(selected_command, args, argv)


def _run_top_level_command(command, args, argv: list[str]) -> bool:
    exit_code = command.run(
        args,
        TopLevelCommandContext(cwd=str(Path.cwd()), argv=tuple(argv)),
    )
    normalized_exit_code = 0 if exit_code is None else int(exit_code)
    if command.name == "run":
        from aworld_cli.async_runtime import hard_exit_direct_run_if_configured

        # The run command has already finalized its outcome and ATIF before it
        # returns.  The one-shot process may now use its process boundary to
        # terminate provider-owned non-daemon threads.
        hard_exit_direct_run_if_configured(
            normalized_exit_code,
            one_shot=bool(getattr(args, "non_interactive", False)),
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
    
    if _maybe_dispatch_top_level_command(sys.argv):
        return

    parser = build_parser()
    # Parse arguments normally, but keep unknown args for inner plugin commands
    args, remaining_argv = parser.parse_known_args()

    if getattr(args, 'config', False):
        if _dispatch_named_top_level_command("config", args, sys.argv):
            return
    
    if args.examples:
        if _dispatch_named_top_level_command("examples", args, sys.argv):
            return

    if args.zh:
        if _dispatch_named_top_level_command("help-zh", args, sys.argv):
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
        agent_files=args.agent_file,
        self_evolve_config=_self_evolve_config_from_cli_mode(
            args.evolve,
            judge_agent=args.judge_agent,
            judge_agent_name=args.judge_agent_name,
            judge_backend_ref=args.judge_backend_ref,
            judge_model_profile=args.judge_model_profile,
        ),
    ))


async def _run_interactive_mode(
    agent_name: Optional[str] = None,
    requested_skill_names: Optional[list[str]] = None,
    remote_backends: Optional[list[str]] = None,
    local_dirs: Optional[list[str]] = None,
    agent_files: Optional[list[str]] = None,
    session_id: Optional[str] = None,
    resume_record=None,
    session_store=None,
    require_same_resume_agent: bool = True,
    resume_cwd: str | None = None,
    fail_on_missing_agent: bool = False,
    self_evolve_config=None,
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
                print(
                    "⚠️ Failed to load an agent file "
                    f"({type(e).__name__}); path and exception text were omitted"
                )
    
    runtime = CliRuntime(
        agent_name=agent_name,
        remote_backends=remote_backends,
        local_dirs=local_dirs,
        session_id=session_id,
        resume_record=resume_record,
        session_store=session_store,
        require_same_resume_agent=require_same_resume_agent,
        resume_cwd=resume_cwd,
        fail_on_missing_agent=fail_on_missing_agent,
        self_evolve_config=self_evolve_config,
    )
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
                print(
                    "⚠️ Failed to load an agent file "
                    f"({type(e).__name__}); path and exception text were omitted"
                )
    
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


def _direct_run_control_details(details: Optional[dict]) -> dict:
    """Project diagnostics onto a small, content-free control-plane schema."""

    if not isinstance(details, dict):
        return {}
    projected: dict[str, object] = {}
    for key in ("attempts",):
        value = details.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            projected[key] = value
    for key in ("provider_evidence",):
        value = details.get(key)
        if isinstance(value, bool):
            projected[key] = value
    for key in ("error_type", "failure_code", "phase", "trajectory_capture_mode"):
        value = details.get(key)
        if isinstance(value, str) and _CONTROL_DETAIL_IDENTIFIER.fullmatch(value):
            projected[key] = value

    available_agents = details.get("available_agents")
    if isinstance(available_agents, (list, tuple)):
        projected["available_agent_count"] = len(available_agents)

    load_failures = details.get("load_failures")
    if isinstance(load_failures, (list, tuple)):
        projected["load_failure_count"] = len(load_failures)
        error_types = sorted(
            {
                item.get("error_type")
                for item in load_failures
                if isinstance(item, dict)
                and isinstance(item.get("error_type"), str)
                and _CONTROL_DETAIL_IDENTIFIER.fullmatch(item["error_type"])
            }
        )
        if error_types:
            projected["load_failure_error_types"] = error_types
    return projected


def _emit_direct_run_failure(
    *,
    stage: str,
    error_code: str,
    agent_name: str,
    details: Optional[dict] = None,
    summary: dict | None = None,
    status: DirectRunStatus = DirectRunStatus.INFRASTRUCTURE_FAILED,
) -> dict:
    """Emit a stable failure record that benchmark adapters can preserve."""
    metrics = DirectRunOutcome.from_summary(summary, status=status)
    payload = {
        "schema_version": "aworld.run.failure.v1",
        "status": "failed",
        "stage": stage,
        "error_code": error_code,
        "agent_name": agent_name,
        "trajectory_fidelity": metrics.trajectory_fidelity,
        "llm_call_count": metrics.llm_call_count,
        "tool_call_count": metrics.tool_call_count,
        "action_count": metrics.action_count,
        "last_successful_checkpoint": metrics.last_successful_checkpoint,
    }
    control_details = _direct_run_control_details(details)
    if control_details:
        payload["details"] = control_details
    print(
        "AWORLD_RUN_FAILURE=" + json.dumps(payload, ensure_ascii=False, sort_keys=True),
        file=sys.stderr,
    )
    return payload


def _emit_direct_run_agent_termination(
    *,
    agent_name: str,
    summary: dict | None,
    reason: str,
) -> None:
    """Log an unsuccessful Agent stop without relabelling it as a harness failure."""

    metrics = DirectRunOutcome.from_summary(
        summary,
        status=DirectRunStatus.SUCCEEDED,
    )
    payload = {
        "schema_version": "aworld.run.agent-termination.v1",
        "status": "completed",
        "agent_name": agent_name,
        "reason": reason,
        "llm_call_count": metrics.llm_call_count,
        "tool_call_count": metrics.tool_call_count,
        "action_count": metrics.action_count,
        "last_successful_checkpoint": metrics.last_successful_checkpoint,
    }
    failed_results = [
        result
        for result in (summary or {}).get("results", [])
        if isinstance(result, dict) and not bool(result.get("success"))
    ]
    if failed_results:
        terminal = failed_results[-1]
        for source_key, target_key in (
            ("failure_origin", "failure_origin"),
            ("failure_code", "failure_code"),
            ("error_type", "error_type"),
            ("semantic_status", "semantic_status"),
        ):
            value = terminal.get(source_key)
            if isinstance(value, str) and _CONTROL_DETAIL_IDENTIFIER.fullmatch(value):
                payload[target_key] = value
    print(
        "AWORLD_AGENT_TERMINATION="
        + json.dumps(payload, ensure_ascii=False, sort_keys=True),
        file=sys.stderr,
    )


def _direct_run_failure_outcome(
    *,
    stage: DirectRunStage | str,
    error_code: DirectRunErrorCode | str,
    agent_name: str,
    details: Optional[dict] = None,
    summary: dict | None = None,
    status: DirectRunStatus = DirectRunStatus.INFRASTRUCTURE_FAILED,
    process_exit_code: int | None = None,
) -> DirectRunOutcome:
    if process_exit_code is None:
        if status is DirectRunStatus.TASK_FAILED:
            from .run_outcome import task_failure_exit_code

            process_exit_code = task_failure_exit_code()
        else:
            process_exit_code = 1
    normalized_stage = stage.value if isinstance(stage, DirectRunStage) else str(stage)
    normalized_error = (
        error_code.value if isinstance(error_code, DirectRunErrorCode) else str(error_code)
    )
    failure = _emit_direct_run_failure(
        stage=normalized_stage,
        error_code=normalized_error,
        agent_name=agent_name,
        details=details,
        summary=summary,
        status=status,
    )
    return DirectRunOutcome.from_summary(
        summary,
        status=status,
        failure_record=failure,
        process_exit_code=process_exit_code,
    )


def _partial_summary_from_agent_executor(agent_executor: object) -> dict | None:
    """Recover finalized or live task evidence after orchestration failure.

    A caller-owned deadline can stop direct mode before EventRunner publishes a
    ``TaskResponse``. Provider records are journaled earlier, on transport
    copies of the task context, so use the reconciled fan-in as the live
    fallback. Evidence recovery is observability and must always fail open.
    """

    try:
        return _build_partial_summary_from_agent_executor(agent_executor)
    except Exception as exc:
        _LOGGER.warning(
            "Direct-run live summary recovery failed open; error_type=%s",
            type(exc).__name__,
        )
        return None


def _build_partial_summary_from_agent_executor(
    agent_executor: object,
) -> dict | None:
    """Build a partial summary; callers must wrap this best-effort projection."""

    task_response = getattr(agent_executor, "last_task_response", None)
    result = {
        "iteration": 1,
        "response": "",
        "cost": 0.0,
        "completed": False,
        "success": False,
    }
    if task_response is not None:
        ContinuousExecutor._attach_task_response_evidence(result, task_response)

    context = getattr(agent_executor, "context", None)
    live_calls = _live_provider_call_records(context)
    captured_calls = result.get("llm_calls")
    if live_calls and (
        not isinstance(captured_calls, list)
        or len(live_calls) > len(captured_calls)
    ):
        result["llm_calls"] = live_calls
        result["trajectory_capture_mode"] = "live_context"

    captured_trajectory = result.get("trajectory")
    if not isinstance(captured_trajectory, list) or not captured_trajectory:
        live_trajectory = _live_trajectory_from_llm_calls(live_calls, context=context)
        if live_trajectory:
            result["trajectory"] = live_trajectory
            result["trajectory_capture_mode"] = "live_context"

    if task_response is None and not live_calls and not result.get("trajectory"):
        return None
    return {
        "total_runs": 1,
        "successful_runs": 0,
        "total_cost": 0.0,
        "results": [result],
    }


def _live_provider_call_records(context: object) -> list[dict]:
    """Copy task-scoped provider attempts from a live Context, fail open."""

    if context is None:
        return []
    get_calls = getattr(context, "get_reconciled_llm_calls", None)
    if not callable(get_calls):
        get_calls = getattr(context, "get_llm_calls", None)
    try:
        calls = get_calls() if callable(get_calls) else []
        if not isinstance(calls, list):
            return []
        task_id = getattr(context, "task_id", None)
        selected = [
            record
            for record in calls
            if isinstance(record, dict)
            and (task_id is None or record.get("task_id") in {None, task_id})
            and record.get("request_id")
            and (
                record.get("provider_invoked") is True
                or record.get("provider_attempt_status") == "attempted"
            )
        ]
        return copy.deepcopy(selected)
    except Exception as exc:
        _LOGGER.warning(
            "Direct-run live evidence recovery failed open; error_type=%s",
            type(exc).__name__,
        )
        return []


def _live_trajectory_from_llm_calls(
    calls: list[dict],
    *,
    context: object,
) -> list[dict]:
    """Project completed provider responses into the native trajectory shape."""

    trajectory: list[dict] = []
    task_id = getattr(context, "task_id", None)
    session_id = getattr(context, "session_id", None)
    for record in calls:
        response = record.get("response")
        if not isinstance(response, dict):
            continue
        message = response.get("message")
        if not isinstance(message, dict):
            continue
        raw_tool_calls = message.get("tool_calls")
        tool_calls = (
            [copy.deepcopy(call) for call in raw_tool_calls if isinstance(call, dict)]
            if isinstance(raw_tool_calls, list)
            else []
        )
        content = message.get("content")
        if content is None and not tool_calls:
            continue
        meta = {
            "step": len(trajectory) + 1,
            "task_id": record.get("task_id") or task_id,
            "session_id": session_id,
            "agent_id": record.get("agent_id"),
            "execute_time": record.get("finished_at") or record.get("started_at"),
        }
        trajectory.append(
            {
                "meta": {key: value for key, value in meta.items() if value is not None},
                "action": {
                    "content": content if isinstance(content, str) else str(content or ""),
                    "tool_calls": tool_calls,
                },
            }
        )
    return trajectory


class DirectRunLiveSummary:
    """Invocation-local bridge from the running executor to its supervisor."""

    def __init__(
        self,
        *,
        checkpoint_writer: Callable[[dict], object] | None = None,
        checkpoint_interval_seconds: float = 30.0,
    ) -> None:
        self._agent_executor: object | None = None
        self._checkpoint_writer = checkpoint_writer
        self._checkpoint_interval_seconds = max(0.01, checkpoint_interval_seconds)
        self._checkpoint_task: asyncio.Task[None] | None = None

    def bind(self, agent_executor: object) -> None:
        self._agent_executor = agent_executor
        if self._checkpoint_writer is not None and self._checkpoint_task is None:
            self._checkpoint_task = asyncio.create_task(
                self._checkpoint_live_trajectory()
            )
            owner = asyncio.current_task()
            if owner is not None:
                owner.add_done_callback(lambda _task: self.stop_checkpointing())

    def stop_checkpointing(self) -> None:
        task = self._checkpoint_task
        if task is not None and not task.done():
            task.cancel()

    async def _checkpoint_live_trajectory(self) -> None:
        """Periodically replace the startup ATIF with best-effort live evidence."""

        while True:
            await asyncio.sleep(self._checkpoint_interval_seconds)
            summary = self.snapshot()
            if summary is None or not _direct_run_has_provider_evidence(summary):
                continue
            try:
                receipt = self._checkpoint_writer(summary) if self._checkpoint_writer else None
                payload = {
                    "schema_version": "aworld.live-atif-checkpoint.v1",
                    "status": getattr(getattr(receipt, "status", None), "value", "written"),
                    "trajectory_fidelity": getattr(receipt, "trajectory_fidelity", "partial"),
                }
                print(
                    "AWORLD_LIVE_ATIF_CHECKPOINT="
                    + json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    file=sys.stderr,
                )
            except Exception as exc:
                # Live trajectory persistence is observability only. The next
                # interval or terminal writer may still succeed.
                _LOGGER.warning(
                    "Live ATIF checkpoint failed open; error_type=%s",
                    type(exc).__name__,
                )

    def snapshot(self) -> dict | None:
        if self._agent_executor is None:
            return None
        return _partial_summary_from_agent_executor(self._agent_executor)


def _prefer_captured_summary(
    summary: dict | None,
    *,
    agent_executor: object,
) -> dict | None:
    recovered = _partial_summary_from_agent_executor(agent_executor)
    if recovered is None:
        return summary
    if summary is None or not _direct_run_has_provider_evidence(summary):
        return recovered
    return summary


async def _run_direct_mode(
    prompt: str,
    agent_name: str,
    requested_skill_names: Optional[list[str]] = None,
    skill_paths: Optional[list[str]] = None,
    max_runs: Optional[int] = None,
    max_cost: Optional[float] = None,
    max_duration: Optional[str] = None,
    completion_signal: Optional[str] = None,
    completion_threshold: int = 3,
    non_interactive: bool = False,
    session_id: Optional[str] = None,
    remote_backends: Optional[list[str]] = None,
    local_dirs: Optional[list[str]] = None,
    agent_files: Optional[list[str]] = None,
    session_mode: str = "direct",
    resume_record=None,
    session_store=None,
    require_same_resume_agent: bool = True,
    resume_cwd: str | None = None,
    fail_on_missing_agent: bool = False,
    show_start_banner: bool = True,
    show_iteration_header: bool = True,
    echo_prompt_as_turn: bool = False,
    self_evolve_config=None,
    live_summary: DirectRunLiveSummary | None = None,
) -> object:
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
                print(
                    "⚠️ Failed to load an agent file "
                    f"({type(e).__name__}); path and exception text were omitted"
                )
    
    # Use CliRuntime to load agents and create executor
    try:
        runtime = CliRuntime(
            remote_backends=remote_backends,
            local_dirs=local_dirs,
            session_id=session_id,
            resume_record=resume_record,
            session_store=session_store,
            require_same_resume_agent=require_same_resume_agent,
            resume_cwd=resume_cwd,
            fail_on_missing_agent=fail_on_missing_agent,
            self_evolve_config=self_evolve_config,
            skill_paths=skill_paths,
        )
    except Exception as exc:
        return _direct_run_failure_outcome(
            stage=DirectRunStage.AGENT_LOAD,
            error_code=DirectRunErrorCode.AGENT_LOAD_FAILED,
            agent_name=agent_name,
            details={"error_type": type(exc).__name__, "message": str(exc)[:1000]},
        )
    try:
        all_agents = await runtime._load_agents()
    except Exception as exc:
        print(f"❌ Error: Failed to load agents ({type(exc).__name__})")
        return _direct_run_failure_outcome(
            stage=DirectRunStage.AGENT_LOAD,
            error_code=DirectRunErrorCode.AGENT_LOAD_FAILED,
            agent_name=agent_name,
            details={
                "error_type": type(exc).__name__,
                "message": str(exc)[:1000],
            },
        )
    # Find the requested agent
    selected_agent = None
    for agent in all_agents:
        if agent.name == agent_name:
            selected_agent = agent
            break
    
    if not selected_agent:
        print(f"❌ Error: Agent '{agent_name}' not found")
        failure_details = {
            "available_agents": sorted(agent.name for agent in all_agents),
        }
        load_failures = getattr(runtime, "_agent_load_failures", None)
        if load_failures:
            failure_details["load_failures"] = load_failures
        return _direct_run_failure_outcome(
            stage=DirectRunStage.AGENT_LOAD,
            error_code=DirectRunErrorCode.AGENT_NOT_FOUND,
            agent_name=agent_name,
            details=failure_details,
        )
    
    # Create agent executor using CliRuntime (session_id is already passed to runtime)
    try:
        from aworld.core.scheduler import get_scheduler

        runtime._scheduler = get_scheduler()
        runtime._bind_scheduler_default_agent(selected_agent.name)
        agent_executor = await runtime._create_executor(selected_agent)
    except Exception as exc:
        print(
            f"❌ Error: Failed to create executor for agent '{agent_name}': "
            f"{type(exc).__name__}"
        )
        return _direct_run_failure_outcome(
            stage=DirectRunStage.EXECUTOR_CREATE,
            error_code=DirectRunErrorCode.EXECUTOR_CREATION_FAILED,
            agent_name=agent_name,
            details={
                "error_type": type(exc).__name__,
                "message": str(exc)[:1000],
            },
        )

    if not agent_executor:
        print(f"❌ Error: Failed to create executor for agent '{agent_name}'")
        return _direct_run_failure_outcome(
            stage=DirectRunStage.EXECUTOR_CREATE,
            error_code=DirectRunErrorCode.EXECUTOR_CREATION_FAILED,
            agent_name=agent_name,
        )

    if live_summary is not None:
        live_summary.bind(agent_executor)

    # Match interactive mode so direct runs can access runtime-scoped features
    # such as steering checkpoints and HUD state.
    try:
        agent_executor._base_runtime = runtime
        agent_executor._session_mode = session_mode
        runtime._restore_executor_session(
            agent_executor,
            current_agent_name=selected_agent.name,
        )
    except Exception as exc:
        return _direct_run_failure_outcome(
            stage=DirectRunStage.EXECUTOR_CREATE,
            error_code=DirectRunErrorCode.EXECUTOR_CREATION_FAILED,
            agent_name=agent_name,
            details={"error_type": type(exc).__name__, "message": str(exc)[:1000]},
        )
    restored_replay = getattr(agent_executor, "_aworld_cli_restored_transcript", None)
    restored_text = getattr(restored_replay, "rendered_text", None)
    if restored_text:
        try:
            agent_executor._aworld_cli_restored_transcript = None
        except Exception:
            pass
        console.print(str(restored_text).strip())
        console.print()
    
    # If session_id was provided, ensure it's properly restored for legacy direct runs.
    # Resume mode already selected a known session from CliSessionStore; BaseAgentExecutor
    # would create a fresh session when the ID is not present in its legacy local history.
    if session_id and session_mode != "interactive" and hasattr(agent_executor, 'restore_session'):
        try:
            # Restore session to ensure it's added to history if needed
            agent_executor.restore_session(session_id)
        except Exception:
            # If restore fails, session_id was already set during executor creation
            pass
    
    # One direct CLI invocation represents one agent task. The agent owns its
    # internal multi-step tool loop; repeating the original prompt here starts
    # the whole task again and can duplicate side effects. Continuous execution
    # remains available when callers explicitly pass --max-runs.
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
    require_provider_evidence = (
        non_interactive and agent_name.casefold() == "aworld"
    )
    require_explicit_failure_origin = (
        require_provider_evidence
        and os.environ.get("AWORLD_TOOL_SURFACE_PROFILE", "").strip().lower()
        == "one_shot"
    )
    max_provider_attempts = (
        _AWORLD_PRE_PROVIDER_MAX_ATTEMPTS if require_provider_evidence else 1
    )
    summary = None
    try:
        for provider_attempt in range(1, max_provider_attempts + 1):
            evidence_cursor = _initial_live_provider_evidence_cursor(agent_executor)
            summary = await run_with_first_provider_start_watchdog(
                continuous_executor.run_continuous(
                    prompt=multimodal_prompt,
                    agent_name=agent_name,
                    requested_skill_names=requested_skill_names,
                    non_interactive=non_interactive,
                    max_runs=max_runs,
                    max_cost=max_cost,
                    max_duration=max_duration,
                    completion_signal=completion_signal,
                    completion_threshold=completion_threshold,
                    show_start_banner=show_start_banner,
                    show_iteration_header=show_iteration_header,
                    echo_prompt_as_turn=echo_prompt_as_turn,
                ),
                evidence_observed=lambda: evidence_cursor is None
                or _new_live_provider_evidence(
                    agent_executor,
                    cursor=evidence_cursor,
                ),
            )
            if _direct_run_cancelled(summary):
                break
            if _direct_run_infrastructure_failure(summary) is not None:
                break
            if not require_provider_evidence or _direct_run_has_provider_evidence(summary):
                break
            if provider_attempt < max_provider_attempts:
                print(
                    "⚠️ Aworld produced no provider-call evidence; retrying task "
                    f"startup ({provider_attempt}/{max_provider_attempts})",
                    file=sys.stderr,
                )
                await asyncio.sleep(0)
        else:
            return _direct_run_failure_outcome(
                stage=DirectRunStage.PROVIDER_START,
                error_code=DirectRunErrorCode.PROVIDER_CALL_NOT_CAPTURED,
                agent_name=agent_name,
                details={
                    "attempts": max_provider_attempts,
                    "trajectory_capture_mode": "summary_synthetic",
                },
                summary=summary,
            )
    except DirectRunDeadlineExceeded as exc:
        recovered = _prefer_captured_summary(
            summary,
            agent_executor=agent_executor,
        )
        if recovered is not None:
            exc.summary = recovered
        raise
    except asyncio.CancelledError:
        summary = _prefer_captured_summary(
            summary,
            agent_executor=agent_executor,
        )
        return _direct_run_failure_outcome(
            stage=DirectRunStage.AGENT_EXECUTION,
            error_code=DirectRunErrorCode.DIRECT_RUN_CANCELLED,
            agent_name=agent_name,
            summary=summary,
            status=DirectRunStatus.CANCELLED,
            process_exit_code=130,
        )
    except Exception as exc:
        summary = _prefer_captured_summary(
            summary,
            agent_executor=agent_executor,
        )
        return _direct_run_failure_outcome(
            stage=DirectRunStage.AGENT_EXECUTION,
            error_code=DirectRunErrorCode.DIRECT_RUN_EXCEPTION,
            agent_name=agent_name,
            details={"error_type": type(exc).__name__},
            summary=summary,
        )
    if _direct_run_cancelled(summary):
        return _direct_run_failure_outcome(
            stage=DirectRunStage.AGENT_EXECUTION,
            error_code=DirectRunErrorCode.DIRECT_RUN_CANCELLED,
            agent_name=agent_name,
            summary=summary,
            status=DirectRunStatus.CANCELLED,
            process_exit_code=130,
        )
    infrastructure_failure = _direct_run_infrastructure_failure(summary)
    if infrastructure_failure is not None:
        return _direct_run_failure_outcome(
            stage=DirectRunStage.AGENT_EXECUTION,
            error_code=DirectRunErrorCode.AGENT_EXECUTION_INFRASTRUCTURE_FAILED,
            agent_name=agent_name,
            details=infrastructure_failure,
            summary=summary,
        )
    incomplete_results = [
        result for result in (summary or {}).get("results", [])
        if isinstance(result, dict) and result.get("semantic_status", result.get("task_status"))
        in {"incomplete", "budget_exhausted"}
    ]
    if incomplete_results:
        unfinished = incomplete_results[-1]
        semantic = unfinished.get("semantic_status", unfinished.get("task_status"))
        reason = unfinished.get("completion_reason")
        if not isinstance(reason, str) or not reason:
            reason = str(semantic or "agent_incomplete")
        _emit_direct_run_agent_termination(
            agent_name=agent_name,
            summary=summary,
            reason=reason,
        )
        return DirectRunOutcome.from_summary(
            summary,
            status=DirectRunStatus.SUCCEEDED,
        )
    if not _direct_run_succeeded(summary):
        if require_explicit_failure_origin and not _direct_run_has_explicit_task_failure(
            summary
        ):
            return _direct_run_failure_outcome(
                stage=DirectRunStage.AGENT_EXECUTION,
                error_code=DirectRunErrorCode.AGENT_EXECUTION_UNTYPED_FAILURE,
                agent_name=agent_name,
                details={"provider_evidence": _direct_run_has_provider_evidence(summary)},
                summary=summary,
            )
        failed_results = [
            result
            for result in (summary or {}).get("results", [])
            if isinstance(result, dict) and not bool(result.get("success"))
        ]
        reason = next(
            (
                str(result.get("failure_code") or result.get("completion_reason"))
                for result in reversed(failed_results)
                if result.get("failure_code") or result.get("completion_reason")
            ),
            "agent_task_unsolved",
        )
        _emit_direct_run_agent_termination(
            agent_name=agent_name,
            summary=summary,
            reason=reason,
        )
        return DirectRunOutcome.from_summary(
            summary,
            status=DirectRunStatus.SUCCEEDED,
        )
    activation_evidence = getattr(
        agent_executor,
        "last_skill_activation_evidence",
        (),
    )
    if activation_evidence:
        # Preserve a task-bound copy at the direct-run boundary.  The sidecar
        # builder also reads per-iteration evidence, but a runtime wrapper must
        # not be able to drop an otherwise valid resolver attestation.
        summary["skill_activation_evidence"] = [
            dict(item)
            for item in activation_evidence
            if isinstance(item, dict)
        ]
    drain_pending_self_evolve_jobs = getattr(
        runtime,
        "_drain_pending_self_evolve_jobs",
        None,
    )
    if callable(drain_pending_self_evolve_jobs):
        try:
            await drain_pending_self_evolve_jobs()
        except Exception as exc:
            return _direct_run_failure_outcome(
                stage=DirectRunStage.ORCHESTRATION,
                error_code=DirectRunErrorCode.DIRECT_RUN_EXCEPTION,
                agent_name=agent_name,
                details={"error_type": type(exc).__name__},
                summary=summary,
            )
    return DirectRunOutcome.from_summary(
        summary,
        status=DirectRunStatus.SUCCEEDED,
    )


if __name__ == "__main__":
    main()
