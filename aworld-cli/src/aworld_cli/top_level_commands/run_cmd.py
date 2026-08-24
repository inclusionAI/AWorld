from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from aworld_cli.runtime_bootstrap import RuntimeBootstrapError, bootstrap_runtime


_SELF_EVOLVE_TASK_RESPONSE_SCHEMA = "aworld.self_evolve.task_response.v1"
_TASK_RESPONSE_CAPABILITY_FD_ENV = (
    "AWORLD_SELF_EVOLVE_TASK_RESPONSE_CAPABILITY_FD"
)
_TASK_RESPONSE_CAPABILITY_MAX_BYTES_ENV = (
    "AWORLD_SELF_EVOLVE_TASK_RESPONSE_CAPABILITY_MAX_BYTES"
)
_DEFAULT_TASK_RESPONSE_CAPABILITY_MAX_BYTES = 8_000_000


def _bounded_text(value: object, *, max_chars: int) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= max_chars:
        return text
    head = max(max_chars * 3 // 4, 1)
    tail = max(max_chars - head, 0)
    return text[:head] + ("\n…<bounded>…\n" if tail else "") + text[-tail:]


def _terminal_trajectory_projection(
    item: dict,
    *,
    text_budget: int,
) -> dict:
    projected: dict[str, object] = {}
    meta = item.get("meta")
    if isinstance(meta, dict):
        projected["meta"] = {
            str(key): value
            for key, value in meta.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
    state = item.get("state")
    if isinstance(state, dict) and "input" in state:
        projected["state"] = {
            "input": _bounded_text(
                state.get("input"), max_chars=max(text_budget // 4, 256)
            )
        }
    action = item.get("action")
    if isinstance(action, dict):
        projected_action: dict[str, object] = {
            "content": _bounded_text(
                action.get("content", ""), max_chars=max(text_budget, 1_024)
            )
        }
        if "is_agent_finished" in action:
            projected_action["is_agent_finished"] = action["is_agent_finished"]
        tool_calls = action.get("tool_calls")
        if isinstance(tool_calls, list):
            projected_action["tool_call_count"] = len(tool_calls)
            projected_action["tool_calls"] = []
        projected["action"] = projected_action
    reward = item.get("reward")
    if isinstance(reward, dict):
        projected["reward"] = {
            str(key): value
            for key, value in reward.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
    return projected or {"action": {"content": "task response completed"}}


def _bounded_task_response_capability_payload(
    sidecar: dict,
    *,
    max_bytes: int,
) -> dict:
    encoded = json.dumps(
        sidecar,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) <= max_bytes:
        return sidecar
    trajectory = sidecar.get("trajectory")
    terminal = next(
        (
            item
            for item in reversed(trajectory)
            if isinstance(item, dict)
        ),
        {"action": {"content": "task response completed"}},
    ) if isinstance(trajectory, list) else {
        "action": {"content": "task response completed"}
    }
    # The capability transports a completion projection, not the full replay
    # transcript. The supervisor already captures stdout and persists evidence;
    # keeping the pipe payload bounded prevents a large trajectory from closing
    # the parent reader and turning a successful baseline into BrokenPipeError.
    text_budget = min(max(max_bytes // 4, 1_024), 256_000)
    compact = {
        "schema_version": sidecar.get("schema_version"),
        "trajectory_capture_mode": "task_response",
        "trajectory": [
            _terminal_trajectory_projection(terminal, text_budget=text_budget)
        ],
        "trajectory_compacted": True,
        "trajectory_original_count": (
            len(trajectory) if isinstance(trajectory, list) else 0
        ),
        "trajectory_digest": "sha256:" + hashlib.sha256(encoded).hexdigest(),
    }
    compact_encoded = json.dumps(
        compact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(compact_encoded) > max_bytes:
        compact["trajectory"][0] = {
            "action": {
                "content": _bounded_text(
                    terminal.get("action", {}).get("content", "")
                    if isinstance(terminal.get("action"), dict)
                    else "",
                    max_chars=max(min(max_bytes // 8, 16_000), 256),
                ),
                "is_agent_finished": "True",
            }
        }
    final_encoded = json.dumps(
        compact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(final_encoded) > max_bytes:
        raise RuntimeError("task-response capability projection exceeds its byte limit")
    return compact


def _consume_task_response_capability() -> int | None:
    raw_fd = os.environ.pop(_TASK_RESPONSE_CAPABILITY_FD_ENV, None)
    if raw_fd is None:
        return None
    try:
        descriptor = int(raw_fd)
        if descriptor < 0:
            raise ValueError
    except ValueError as exc:
        raise RuntimeError("invalid task-response capability fd") from exc
    os.set_inheritable(descriptor, False)
    return descriptor


def _write_self_evolve_task_response(
    payload: dict,
    *,
    capability_fd: int | None = None,
) -> None:
    """Publish final task output atomically for the replay supervisor."""

    raw_path = os.environ.get("AWORLD_SELF_EVOLVE_TASK_RESPONSE_PATH")
    if not raw_path:
        return
    destination = Path(raw_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.tmp"
    )
    sidecar = {
        "schema_version": _SELF_EVOLVE_TASK_RESPONSE_SCHEMA,
        **payload,
    }
    if capability_fd is not None:
        raw_limit = os.environ.get(_TASK_RESPONSE_CAPABILITY_MAX_BYTES_ENV)
        try:
            max_bytes = int(raw_limit) if raw_limit else (
                _DEFAULT_TASK_RESPONSE_CAPABILITY_MAX_BYTES
            )
        except ValueError as exc:
            raise RuntimeError("invalid task-response capability byte limit") from exc
        if max_bytes < 1_024:
            raise RuntimeError("task-response capability byte limit is too small")
        sidecar = _bounded_task_response_capability_payload(
            sidecar,
            max_bytes=max_bytes,
        )
        encoded = json.dumps(
            sidecar,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            offset = 0
            while offset < len(encoded):
                written = os.write(capability_fd, encoded[offset:])
                if written <= 0:
                    raise OSError("task-response capability write stalled")
                offset += written
        finally:
            os.close(capability_fd)
        return
    temporary.write_text(
        json.dumps(sidecar, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def _register_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=str, required=True)
    parser.add_argument("--agent", type=str)
    parser.add_argument("--skill", dest="skill", action="append")
    parser.add_argument("--max-runs", type=int)
    parser.add_argument("--max-cost", type=float)
    parser.add_argument("--max-duration", type=str)
    parser.add_argument("--completion-signal", type=str)
    parser.add_argument("--completion-threshold", type=int, default=3)
    parser.add_argument("--session_id", "--session-id", type=str, dest="session_id")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--env-file", type=str, default=".env")
    parser.add_argument("--remote-backend", type=str, action="append")
    parser.add_argument("--agent-dir", type=str, action="append")
    parser.add_argument("--agent-file", type=str, action="append")
    parser.add_argument("--skill-path", type=str, action="append")
    parser.add_argument(
        "--evolve",
        nargs="?",
        const="shadow",
        choices=("off", "offline", "shadow", "online"),
        default=None,
    )
    parser.add_argument("--judge-agent", type=str)
    parser.add_argument("--judge-agent-name", type=str)
    parser.add_argument("--judge-backend-ref", type=str)
    parser.add_argument("--judge-model-profile", type=str)
    parser.add_argument("--emit-trajectory", action="store_true")


def _parse_global_evolve_options(argv) -> argparse.Namespace:
    modes = {"off", "offline", "shadow", "online"}
    tokens = list(argv)[1:]
    result = argparse.Namespace(
        evolve=None,
        judge_agent=None,
        judge_agent_name=None,
        judge_backend_ref=None,
        judge_model_profile=None,
    )
    for index, token in enumerate(tokens):
        if token.startswith("--evolve="):
            value = token.split("=", 1)[1].strip().lower()
            result.evolve = value if value in modes else None
            continue
        if token == "--evolve":
            next_token = tokens[index + 1].strip().lower() if index + 1 < len(tokens) else ""
            result.evolve = next_token if next_token in modes else "shadow"
            continue
        if token in {"--judge-agent", "--judge-agent-name", "--judge-backend-ref", "--judge-model-profile"}:
            value = tokens[index + 1] if index + 1 < len(tokens) else None
            if token == "--judge-agent":
                result.judge_agent = value
            elif token == "--judge-agent-name":
                result.judge_agent_name = value
            elif token == "--judge-backend-ref":
                result.judge_backend_ref = value
            else:
                result.judge_model_profile = value
            continue
        if token.startswith("--judge-agent="):
            result.judge_agent = token.split("=", 1)[1]
        elif token.startswith("--judge-agent-name="):
            result.judge_agent_name = token.split("=", 1)[1]
        elif token.startswith("--judge-backend-ref="):
            result.judge_backend_ref = token.split("=", 1)[1]
        elif token.startswith("--judge-model-profile="):
            result.judge_model_profile = token.split("=", 1)[1]
    return result


class RunTopLevelCommand:
    @property
    def name(self) -> str:
        return "run"

    @property
    def description(self) -> str:
        return "Run a task in direct mode."

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    def register_parser(self, subparsers) -> None:
        parser = subparsers.add_parser(
            "run",
            help=self.description,
            description=self.description,
            prog="aworld-cli run",
        )
        _register_run_options(parser)

    def run(self, args, context) -> int | None:
        from aworld_cli.main import (
            _resolve_agent_dirs,
            _run_direct_mode,
            _self_evolve_config_from_cli_mode,
            _show_banner,
            init_middlewares,
        )

        task_response_capability = _consume_task_response_capability()
        try:
            bootstrap_runtime(
                env_file=args.env_file,
                skill_paths=args.skill_path,
                show_banner="--no-banner" not in context.argv,
                init_middlewares_fn=init_middlewares,
                show_banner_fn=_show_banner,
            )
        except RuntimeBootstrapError:
            return 1

        local_dirs = _resolve_agent_dirs(args.agent_dir)
        args_evolve = getattr(args, "evolve", None)
        global_evolve = _parse_global_evolve_options(context.argv)
        evolve_mode = args_evolve if args_evolve is not None else global_evolve.evolve
        judge_agent = getattr(args, "judge_agent", None) or global_evolve.judge_agent
        judge_agent_name = getattr(args, "judge_agent_name", None) or global_evolve.judge_agent_name
        judge_backend_ref = getattr(args, "judge_backend_ref", None) or global_evolve.judge_backend_ref
        judge_model_profile = getattr(args, "judge_model_profile", None) or global_evolve.judge_model_profile
        agent_name = self._resolve_agent_name(args)
        if agent_name is None:
            return 0

        summary = asyncio.run(
            _run_direct_mode(
                prompt=args.task,
                agent_name=agent_name,
                requested_skill_names=args.skill,
                skill_paths=args.skill_path,
                max_runs=args.max_runs,
                max_cost=args.max_cost,
                max_duration=args.max_duration,
                completion_signal=args.completion_signal,
                completion_threshold=args.completion_threshold,
                non_interactive=args.non_interactive,
                session_id=args.session_id,
                remote_backends=args.remote_backend,
                local_dirs=local_dirs,
                agent_files=args.agent_file,
                self_evolve_config=_self_evolve_config_from_cli_mode(
                    evolve_mode,
                    judge_agent=judge_agent,
                    judge_agent_name=judge_agent_name,
                    judge_backend_ref=judge_backend_ref,
                    judge_model_profile=judge_model_profile,
                ),
            )
        )
        emit_trajectory = getattr(args, "emit_trajectory", False)
        task_response_path = os.environ.get(
            "AWORLD_SELF_EVOLVE_TASK_RESPONSE_PATH"
        )
        if emit_trajectory or task_response_path:
            from aworld_cli.main import _trajectory_payload_from_direct_run_summary

            trajectory_payload = _trajectory_payload_from_direct_run_summary(
                summary,
                prompt=args.task,
                agent_name=agent_name,
            )
            if task_response_path:
                _write_self_evolve_task_response(
                    trajectory_payload,
                    capability_fd=task_response_capability,
                )
        if emit_trajectory:
            print(
                json.dumps(
                    trajectory_payload,
                    ensure_ascii=False,
                )
            )
        return 0

    def _resolve_agent_name(self, args) -> str | None:
        agent_name = args.agent
        if not agent_name and args.agent_file:
            if len(args.agent_file) == 1:
                from aworld_cli.core.loader import init_agent_file

                try:
                    agent_name = init_agent_file(args.agent_file[0])
                    if not agent_name:
                        print(
                            f"❌ Error: Could not extract agent name from {args.agent_file[0]}"
                        )
                        return None
                    print(f"ℹ️  Auto-detected agent name: {agent_name}")
                except Exception as exc:
                    print(
                        f"❌ Error: Failed to load agent file {args.agent_file[0]}: {exc}"
                    )
                    return None
            else:
                print("❌ Error: --agent is required when using multiple --agent-file")
                return None
        elif not agent_name:
            agent_name = "Aworld"
            print(f"ℹ️  Using default agent: {agent_name}")

        return agent_name
