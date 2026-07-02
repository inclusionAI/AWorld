from __future__ import annotations

import argparse
import asyncio
import sys

from aworld_cli.core.session_restore import resolve_session_record
from aworld_cli.core.session_store import CliSessionStore
from aworld_cli.runtime_bootstrap import RuntimeBootstrapError, bootstrap_runtime
from aworld_cli.top_level_commands.invocation import find_command_index


def _register_resume_options(parser: argparse.ArgumentParser, *, include_prompt_remainder: bool = True) -> None:
    parser.add_argument("session_id", nargs="?")
    parser.add_argument("--last", action="store_true", help="Resume the latest session.")
    parser.add_argument("--all", action="store_true", help="Search sessions from all working directories.")
    parser.add_argument(
        "--include-non-interactive",
        action="store_true",
        help="Include direct/non-interactive sessions when selecting the latest session.",
    )
    parser.add_argument("--agent", type=str, help="Override the agent used for the resumed session.")
    parser.add_argument("--skill", dest="skill", action="append")
    parser.add_argument("--env-file", type=str, default=".env")
    parser.add_argument("--remote-backend", type=str, action="append")
    parser.add_argument("--agent-dir", type=str, action="append")
    parser.add_argument("--agent-file", type=str, action="append")
    parser.add_argument("--skill-path", type=str, action="append")
    if include_prompt_remainder:
        parser.add_argument("prompt_parts", nargs=argparse.REMAINDER)


def _build_resume_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aworld-cli resume",
        description="Resume a previous interactive CLI session.",
        add_help=add_help,
    )
    _register_resume_options(parser, include_prompt_remainder=False)
    return parser


def _normalize_prompt(args: argparse.Namespace) -> argparse.Namespace:
    prompt_parts = list(getattr(args, "prompt_parts", None) or [])
    if getattr(args, "last", False) and getattr(args, "session_id", None):
        prompt_parts.insert(0, args.session_id)
        args.session_id = None
    args.prompt = " ".join(item for item in prompt_parts if item).strip() or None
    return args


def _parse_resume_invocation_args(argv) -> argparse.Namespace:
    from aworld_cli.main import _GLOBAL_OPTIONS_WITH_VALUES

    command_index = find_command_index(
        argv,
        command_name="resume",
        options_with_values=_GLOBAL_OPTIONS_WITH_VALUES,
    )
    parser = _build_resume_parser(add_help=False)
    if command_index is None:
        args, prompt_parts = parser.parse_known_args([])
    else:
        parse_argv = list(argv[1:command_index]) + list(argv[command_index + 1:])
        args, prompt_parts = parser.parse_known_args(parse_argv)
    args.prompt_parts = prompt_parts
    return _normalize_prompt(args)


def _choose_resume_record(records, *, input_fn=None, output_fn=print):
    if input_fn is None:
        import builtins

        input_fn = builtins.input
    if not records:
        return None
    output_fn("Resumable sessions:")
    for index, record in enumerate(records, start=1):
        prompt = record.last_prompt or "(no prompt recorded)"
        output_fn(f"{index}. {record.session_id} | {record.agent_name} | {record.updated_at} | {prompt}")
    raw = input_fn("Select session: ").strip()
    try:
        selected = int(raw)
    except ValueError:
        return None
    if selected < 1 or selected > len(records):
        return None
    return records[selected - 1]


async def _run_resume_mode(
    *,
    session_id: str,
    agent_name: str,
    requested_skill_names: list[str] | None,
    initial_prompt: str | None,
    remote_backends: list[str] | None,
    local_dirs: list[str] | None,
    agent_files: list[str] | None,
    resume_record,
    session_store: CliSessionStore,
    require_same_resume_agent: bool,
    resume_cwd: str,
    fail_on_missing_agent: bool,
) -> None:
    from aworld_cli.main import _run_direct_mode, _run_interactive_mode

    if initial_prompt:
        await _run_direct_mode(
            prompt=initial_prompt,
            agent_name=agent_name,
            requested_skill_names=requested_skill_names,
            max_runs=1,
            non_interactive=False,
            session_id=session_id,
            remote_backends=remote_backends,
            local_dirs=local_dirs,
            agent_files=agent_files,
            session_mode="interactive",
            resume_record=resume_record,
            session_store=session_store,
            require_same_resume_agent=require_same_resume_agent,
            resume_cwd=resume_cwd,
            fail_on_missing_agent=fail_on_missing_agent,
            show_start_banner=False,
            show_iteration_header=False,
            echo_prompt_as_turn=True,
        )

    await _run_interactive_mode(
        agent_name=agent_name,
        requested_skill_names=requested_skill_names,
        remote_backends=remote_backends,
        local_dirs=local_dirs,
        agent_files=agent_files,
        session_id=session_id,
        resume_record=resume_record,
        session_store=session_store,
        require_same_resume_agent=require_same_resume_agent,
        resume_cwd=resume_cwd,
        fail_on_missing_agent=fail_on_missing_agent,
    )


class ResumeTopLevelCommand:
    @property
    def name(self) -> str:
        return "resume"

    @property
    def description(self) -> str:
        return "Resume a previous interactive CLI session."

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    def register_parser(self, subparsers) -> None:
        parser = subparsers.add_parser(
            "resume",
            help=self.description,
            description=self.description,
            prog="aworld-cli resume",
        )
        _register_resume_options(parser)

    def run(self, args, context) -> int | None:
        from aworld_cli.main import (
            _resolve_agent_dirs,
            _show_banner,
            init_middlewares,
        )

        invocation_args = _parse_resume_invocation_args(context.argv)
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

        session_store = CliSessionStore()
        record = resolve_session_record(
            session_store=session_store,
            session_id=invocation_args.session_id,
            cwd=context.cwd,
            use_latest=invocation_args.last,
            include_all_cwds=invocation_args.all,
            include_non_interactive=invocation_args.include_non_interactive,
        )
        if record is None and not invocation_args.session_id and not invocation_args.last and sys.stdin.isatty():
            record = _choose_resume_record(
                session_store.list(
                    cwd=context.cwd,
                    include_all_cwds=invocation_args.all,
                    include_non_interactive=invocation_args.include_non_interactive,
                )
            )
        if record is None:
            if invocation_args.session_id:
                print(f"Session not found: {invocation_args.session_id}")
                print("Use `aworld-cli resume --all` to include sessions from other workspaces, or `/sessions list`.")
            elif invocation_args.last:
                print(f"No resumable sessions found for {context.cwd}.")
                print("Start a session with `aworld-cli interactive`, or use `--all` to search all workspaces.")
            else:
                print("Error: no session selected. Use 'aworld-cli resume SESSION_ID' or 'aworld-cli resume --last'.")
            return 1

        agent_name = invocation_args.agent or record.agent_name or "Aworld"
        local_dirs = _resolve_agent_dirs(invocation_args.agent_dir)
        asyncio.run(
            _run_resume_mode(
                session_id=record.session_id,
                agent_name=agent_name,
                requested_skill_names=invocation_args.skill,
                initial_prompt=invocation_args.prompt,
                remote_backends=invocation_args.remote_backend,
                local_dirs=local_dirs,
                agent_files=invocation_args.agent_file,
                resume_record=record,
                session_store=session_store,
                require_same_resume_agent=invocation_args.agent is None,
                resume_cwd=context.cwd,
                fail_on_missing_agent=invocation_args.agent is None,
            )
        )
        return 0
