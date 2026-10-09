"""Thin CLI host: configure capabilities, submit runs, render results, clean up."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import fields, is_dataclass
from enum import Enum
import json
import os
from pathlib import Path
import sys
import stat
from typing import Mapping
from uuid import uuid4

from aworld import __version__
from aworld.core.agent import Agent
from aworld.core.agent.messages import AssistantMessage, ToolCall, ToolResultMessage
from aworld.core.context import BudgetPolicy, Context, ContextBudget
from aworld.core.sandbox import LocalSandbox
from aworld.core.session import InMemorySessionStore, RunOptions, RunStatus, create_session, load_session
from aworld.core.tool import ToolRegistry, default_tools, session_tools


def _serialize(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=_serialize, allow_nan=False)


@asynccontextmanager
async def _stdin_reader():
    """Keep the loop alive while stdin waits, without an unkillable input thread."""
    descriptor = sys.stdin.fileno()
    if stat.S_ISREG(os.fstat(descriptor).st_mode):
        async def read():
            return sys.stdin.readline()
        yield read
        return
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    blocking = os.get_blocking(descriptor)
    stream = os.fdopen(os.dup(descriptor), "rb", buffering=0)
    transport = None
    try:
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, stream)
        async def read():
            return (await reader.readline()).decode(sys.stdin.encoding or "utf-8")
        yield read
    finally:
        if transport is not None:
            transport.close()
        else:
            stream.close()
        os.set_blocking(descriptor, blocking)


class DemoModel:
    """Deterministic offline smoke model, explicitly selected by --demo."""
    async def complete(self, request):
        last = request.messages[-1]
        if isinstance(last, ToolResultMessage):
            return AssistantMessage("demo result: " + _json(last.content))
        if last.content.startswith("read "):
            return AssistantMessage(tool_calls=(ToolCall(uuid4().hex, "read", {"path": last.content[5:]}),))
        return AssistantMessage("demo: " + last.content)


def parser():
    value = argparse.ArgumentParser(prog="aworld", description="AWorld 1.0: Agent + Context + Tool (default LocalSandbox).")
    value.add_argument("prompt", nargs="?", help="Task text; omit for an interactive session")
    value.add_argument("--task", help="Task text (alternative to positional prompt)")
    value.add_argument("--follow-up", action="append", default=[], help="Another run in the same session")
    value.add_argument("--demo", action="store_true", help="Offline deterministic smoke model")
    value.add_argument("--model", help="Model name; overrides environment, .env and model profiles")
    value.add_argument("--model-profile", help="Named model in project/user .aworld/aworld.json")
    value.add_argument("--base-url")
    value.add_argument("--cwd", type=Path, default=Path.cwd(), help="LocalSandbox working directory")
    value.add_argument("--tools", help="Comma-separated capability names; replaces defaults")
    value.add_argument("--no-tools", action="store_true")
    skill_args = value.add_mutually_exclusive_group()
    skill_args.add_argument("--skill-path", action="append", default=[], help="Explicit Skill roots, replacing auto-discovery (requires aworld[skills])")
    skill_args.add_argument("--no-skills", action="store_true", help="Disable Skill discovery")
    value.add_argument("--work-root", type=Path, help="Session notes root (default: cwd/.aworld/sessions)")
    value.add_argument("--network-policy", choices=["allowed", "disabled", "unknown"], default=os.getenv("AWORLD_NETWORK_POLICY", "unknown"))
    value.add_argument("--system-prompt", default=None, help="Replace the default base prompt; workspace, Skill and runtime context still apply")
    value.add_argument("--max-turns", type=int, default=20)
    value.add_argument("--timeout", type=float, help="Total execution budget per run in seconds")
    value.add_argument("--request-timeout", type=float, default=300,
                       help="Per-attempt model request timeout in seconds (default: 300)")
    value.add_argument("--response-retries", type=int, default=2, help="Recovery retries for empty model replies (0-5; count toward max-turns)")
    value.add_argument("--response-retry-delay", type=float, default=0.5, help="Initial response recovery delay in seconds (0-30; exponential backoff)")
    value.add_argument("--max-retries", type=int, default=3, help="Retries per unresolved model request (0-10)")
    value.add_argument("--reasoning-effort", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"])
    value.add_argument("--context-window", type=int, help="Override profile/environment/model-registry context limit")
    value.add_argument("--max-output-tokens", type=int, help="Override profile output reserve and per-request cap")
    value.add_argument("--compaction-trigger-ratio", type=float, default=0.85,
                       help="Compact above this fraction of the remaining input budget")
    value.add_argument("--keep-recent-tokens", type=int, help="Estimated recent history retained during compaction")
    value.add_argument("--summary-max-tokens", type=int, help="Summary output limit (default: 32768, bounded by model output and context budget)")
    value.add_argument("--compaction-timeout", type=float, default=300, help="Summary request timeout in seconds (default: 300; bounded by the remaining run deadline)")
    value.add_argument("--no-compaction", action="store_true", help="Use the full history without automatic budgeting")
    value.add_argument("--json", action="store_true", help="Write one terminal RunResult JSON per line")
    value.add_argument("--events", action="store_true", help="Write observed Run events as JSON lines to stderr")
    value.add_argument("--trajectory-output", type=Path, help="Atomically export canonical session history as ATIF-v1.7 after each run")
    value.add_argument("--result-output", type=Path, help="Atomically write the terminal RunResult after each run")
    value.add_argument("--version", action="version", version=f"aworld {__version__}")
    return value


async def _execute(session, input, args, agent):
    run = await session.submit(input, options=RunOptions(timeout_seconds=args.timeout))
    try:
        if args.events:
            async for event in run.events():
                print(_json(event), file=sys.stderr, flush=True)
        result = await run.result()
    except asyncio.CancelledError:
        await run.cancel()
        result = await run.result()
        raise
    finally:
        result = await run.result()
        if args.trajectory_output or args.result_output:
            from aworld.cli.trajectory import build_trajectory, write_json
            if args.result_output:
                write_json(args.result_output, result, default=_serialize)
            if args.trajectory_output:
                history = await session.history()
                events = []
                for run_id in dict.fromkeys([entry.run_id for entry in history] + [result.run_id]):
                    handle = await session.get_run(run_id)
                    events.extend([event async for event in handle.events()])
                write_json(args.trajectory_output, build_trajectory(history, events=events,
                    result=result, agent=agent, model_name="demo" if args.demo else args.model))
    if args.json:
        print(_json(result), flush=True)
    elif result.status == RunStatus.COMPLETED:
        print(result.output, flush=True)
    else:
        print(f"{result.status.value}: {result.error.message if result.error else result.stop_reason.value}", file=sys.stderr)
    return 0 if result.status == RunStatus.COMPLETED else 1


async def _host(args, command):
    registry = ToolRegistry(default_tools(sandbox=LocalSandbox(args.cwd)))
    if args.no_tools:
        registry = ToolRegistry()
    elif args.tools is not None:
        registry = registry.select(*(name.strip() for name in args.tools.split(",") if name.strip()))
    if command == "tools":
        print(_json(registry.schemas()) if args.json else "\n".join(f"{tool.name}: {tool.description}" for tool in registry))
        return 0
    from aworld.cli.model_config import resolve_model_settings
    settings = resolve_model_settings(args)
    args.model, args.base_url = settings.model, settings.base_url
    args.context_window, args.max_output_tokens = settings.context_window, settings.max_output_tokens
    safety_margin = min(2048, max(64, settings.context_window // 64))
    input_budget = settings.context_window - settings.max_output_tokens - safety_margin
    policy = None
    if not args.no_compaction:
        keep_recent_tokens = args.keep_recent_tokens if args.keep_recent_tokens is not None else min(16000, max(1, input_budget // 4))
        summary_room = int(input_budget * args.compaction_trigger_ratio) - keep_recent_tokens - 1
        policy = BudgetPolicy(ContextBudget(context_window=settings.context_window, output_reserve=settings.max_output_tokens,
            safety_margin=safety_margin, trigger_ratio=args.compaction_trigger_ratio,
            keep_recent_tokens=keep_recent_tokens,
            summary_max_tokens=args.summary_max_tokens if args.summary_max_tokens is not None else min(32768, settings.max_output_tokens, max(1, summary_room)),
            summary_timeout=args.compaction_timeout))
    from aworld.cli.prompt import BASE_PROMPT, PROMPT_VERSION, discover_skills, workspace_prompt, runtime_prompt
    skills = discover_skills(
        args.cwd, paths=args.skill_path, disabled=args.no_skills or args.no_tools
    )
    workspace, sources = workspace_prompt(args.cwd)
    system_prompt = "\n\n".join(part for part in (BASE_PROMPT if args.system_prompt is None else args.system_prompt, workspace) if part)
    sources = [{"kind": "base", "version": PROMPT_VERSION, "custom": args.system_prompt is not None}, *sources,
               {"kind": "skills", "paths": [skill.location for skill in skills]}, {"kind": "runtime"}]
    context_metadata = {"enabled": policy is not None, "context_window": settings.context_window,
                        "max_output_tokens": settings.max_output_tokens, "window_source": settings.window_source,
                        "output_source": settings.output_source, "model_profile": settings.profile}
    if os.getenv("AWORLD_CONTEXT_BUDGET_SOURCE"):
        context_metadata["host_source"] = os.environ["AWORLD_CONTEXT_BUDGET_SOURCE"]
    if policy is not None:
        context_metadata["budget"] = {item.name: getattr(policy.budget, item.name) for item in fields(policy.budget)}
    if args.demo:
        model = DemoModel()
    else:
        from aworld.models.chat_completions import ChatCompletionsModel
        model = ChatCompletionsModel(model=args.model, base_url=args.base_url,
            api_key=settings.api_key, default_parameters={**settings.parameters, "max_tokens": settings.max_output_tokens},
            timeout=args.request_timeout, max_retries=args.max_retries)
    sessions = InMemorySessionStore()
    try:
        agent = Agent(model=model, tools=registry, skills=skills, system_prompt=system_prompt, max_turns=args.max_turns,
                      response_retries=args.response_retries, response_retry_delay=args.response_retry_delay,
                      runtime_prompt=runtime_prompt(args), prompt_metadata={"version": PROMPT_VERSION, "sources": sources,
                                                                          "context": context_metadata})
        session = await create_session(agent=agent, context=Context(policy=policy), store=sessions, metadata={"cwd": str(args.cwd.resolve())})
        initial = args.task if args.task is not None else args.prompt
        if initial is not None:
            code = await _execute(session, initial, args, agent)
            for follow_up in args.follow_up:
                if code:
                    break
                code = await _execute(session, follow_up, args, agent)
            return code
        print(f"Session {session.id}; /new, /sessions, /session ID, /query JSON, /read ID, /quit", file=sys.stderr)
        async with _stdin_reader() as read:
            while True:
                if sys.stdin.isatty():
                    print("aworld> ", end="", flush=True)
                raw = await read()
                if not raw:
                    break
                line = raw.strip()
                if line in ("/quit", "/exit"):
                    break
                try:
                    if line == "/new":
                        session = await create_session(agent=agent, context=Context(policy=policy), store=sessions, metadata={"cwd": str(args.cwd.resolve())})
                        print(f"Session {session.id}", file=sys.stderr)
                    elif line == "/sessions":
                        print(_json([await item.snapshot() for item in await sessions.list_sessions()]))
                    elif line.startswith("/session "):
                        session = await load_session(line[9:].strip(), store=sessions)
                        print(f"Session {session.id}", file=sys.stderr)
                    elif line.startswith("/query ") or line.startswith("/read "):
                        name = "session_query" if line.startswith("/query ") else "read_session"
                        arguments = {"query": json.loads(line[7:])} if name == "session_query" else {"session_id": line[6:].strip()}
                        print(_json(await ToolRegistry(session_tools(sessions)).execute(name, arguments, None)))
                    elif line:
                        await _execute(session, line, args, agent)
                except (ValueError, LookupError) as exc:
                    print(f"error: {exc}", file=sys.stderr)
        return 0
    finally:
        for session in await sessions.list_sessions():
            await session.close()
        close = getattr(model, "aclose", None)
        if close:
            await close()


def main(argv=None):
    args_list = list(sys.argv[1:] if argv is None else argv)
    command = args_list.pop(0) if args_list and args_list[0] in ("run", "chat", "tools") else None
    cli = parser()
    args = cli.parse_args(args_list)
    if args.task is not None and args.prompt is not None:
        cli.error("Use either positional prompt or --task")
    if args.no_tools and args.tools is not None:
        cli.error("Use either --tools or --no-tools")
    if command == "run" and args.task is None and args.prompt is None:
        cli.error("run requires a task")
    try:
        from aworld.cli.model_config import load_project_dotenv
        load_project_dotenv(args.cwd)
        return asyncio.run(_host(args, command))
    except KeyboardInterrupt:
        return 130
    except (ValueError, LookupError, RuntimeError, OSError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
