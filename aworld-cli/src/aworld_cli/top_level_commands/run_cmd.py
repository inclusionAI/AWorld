from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from aworld_cli.async_runtime import DirectRunDeadlineExceeded, run_direct_async
from aworld_cli.runtime_bootstrap import RuntimeBootstrapError, bootstrap_runtime


def _aworld_agent_version() -> str:
    """Resolve the installed AWorld package version for ATIF metadata."""

    try:
        import aworld

        public_version = getattr(aworld, "__version__", None)
        if isinstance(public_version, str) and public_version.strip():
            return public_version.strip()
        from aworld.version_gen import __version__ as generated_version

        if isinstance(generated_version, str) and generated_version.strip():
            return generated_version.strip()
    except Exception:
        pass
    return "unknown"


def _write_final_markers(lines: list[str]) -> None:
    """Flush prior output, then append grouped diagnostic marker lines."""

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    sys.stderr.write("\n" + "\n".join(lines) + "\n")
    sys.stderr.flush()


def _write_outcome_sidecar(path: str, payload: dict) -> None:
    """Atomically persist the content-free direct-run control record."""

    # Do not resolve the leaf: os.replace must replace a pre-existing symlink,
    # never follow it and overwrite its target.
    destination = Path(path).expanduser().absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
        try:
            directory_descriptor = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            # The file itself is already fsynced and atomically installed.
            # Some filesystems do not permit opening/fsyncing a directory.
            pass
    finally:
        temporary_path.unlink(missing_ok=True)


_SELF_EVOLVE_TASK_RESPONSE_SCHEMA = "aworld.self_evolve.task_response.v1"
_TASK_RESPONSE_CAPABILITY_FD_ENV = (
    "AWORLD_SELF_EVOLVE_TASK_RESPONSE_CAPABILITY_FD"
)
_TASK_RESPONSE_CAPABILITY_MAX_BYTES_ENV = (
    "AWORLD_SELF_EVOLVE_TASK_RESPONSE_CAPABILITY_MAX_BYTES"
)
_DEFAULT_TASK_RESPONSE_CAPABILITY_MAX_BYTES = 8_000_000
_LIVE_ATIF_CHECKPOINT_INTERVAL_ENV = "AWORLD_LIVE_ATIF_CHECKPOINT_INTERVAL_SECONDS"


def _live_atif_checkpoint_interval_seconds() -> float:
    raw = os.environ.get(_LIVE_ATIF_CHECKPOINT_INTERVAL_ENV, "30")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 30.0
    return value if 1.0 <= value <= 300.0 else 30.0


def _bounded_text(value: object, *, max_chars: int) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= max_chars:
        return text
    head = max(max_chars * 3 // 4, 1)
    tail = max(max_chars - head, 0)
    return text[:head] + ("\n…<bounded>…\n" if tail else "") + text[-tail:]


def _complete_llm_usage_projection(value: object) -> dict | None:
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
    projected = {
        "schema_version": "aworld.llm_usage_summary.v1",
        "call_count": call_count,
        "usage_call_count": usage_call_count,
        "total_tokens": total_tokens,
        "coverage_complete": True,
        "ledger_consistent": True,
    }
    iteration_count = value.get("iteration_count")
    if (
        not isinstance(iteration_count, bool)
        and isinstance(iteration_count, int)
        and iteration_count > 0
    ):
        projected["iteration_count"] = iteration_count
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
        projected["input_tokens"] = input_tokens
        projected["output_tokens"] = output_tokens
    return projected


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
        if isinstance(tool_calls, (list, tuple)):
            projected_action["tool_call_count"] = len(tool_calls)
            # Preserve pending intent without retaining tool identity or arguments.
            # Completion checks distinguish a nonempty call list from a final answer.
            projected_action["tool_calls"] = [{}] if tool_calls else []
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
    activation_evidence = sidecar.get("skill_activation_evidence")
    if isinstance(activation_evidence, list):
        compact["skill_activation_evidence"] = [
            item for item in activation_evidence if isinstance(item, dict)
        ][:32]
    llm_usage = _complete_llm_usage_projection(sidecar.get("llm_usage"))
    if llm_usage is not None:
        compact["llm_usage"] = llm_usage
    compact_encoded = json.dumps(
        compact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(compact_encoded) > max_bytes:
        projected_action = compact["trajectory"][0].get("action", {})
        compact["trajectory"][0] = {
            "action": {
                **projected_action,
                "content": _bounded_text(
                    terminal.get("action", {}).get("content", "")
                    if isinstance(terminal.get("action"), dict)
                    else "",
                    max_chars=max(min(max_bytes // 8, 16_000), 256),
                ),
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
    parser.add_argument(
        "--trajectory-output",
        type=str,
        help="Write the direct-run trajectory to this file.",
    )
    parser.add_argument(
        "--trajectory-format",
        choices=("atif",),
        default="atif",
        help="Trajectory output format (default: atif).",
    )
    parser.add_argument(
        "--outcome-output",
        type=str,
        help="Atomically write the content-free direct-run outcome to this file.",
    )


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
            DirectRunLiveSummary,
            _direct_run_failure_outcome,
            _emit_direct_run_agent_termination,
            _resolve_agent_dirs,
            _run_direct_mode,
            _self_evolve_config_from_cli_mode,
            _show_banner,
            init_middlewares,
        )
        from aworld_cli.run_outcome import (
            DirectRunOutcome,
            DirectRunErrorCode,
            DirectRunStage,
            DirectRunStatus,
            coerce_direct_run_outcome,
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
        except RuntimeBootstrapError as exc:
            outcome = _direct_run_failure_outcome(
                stage=DirectRunStage.ORCHESTRATION,
                error_code=DirectRunErrorCode.DIRECT_RUN_EXCEPTION,
                agent_name=getattr(args, "agent", None) or "Aworld",
                details={"error_type": type(exc).__name__},
            )
            return self._finalize_outcome(
                args=args,
                agent_name=getattr(args, "agent", None) or "Aworld",
                outcome=outcome,
                task_response_capability=task_response_capability,
            )

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
            outcome = _direct_run_failure_outcome(
                stage=DirectRunStage.AGENT_LOAD,
                error_code=DirectRunErrorCode.AGENT_LOAD_FAILED,
                agent_name=getattr(args, "agent", None) or "unknown",
                details={"error_type": "AgentResolutionError"},
            )
            return self._finalize_outcome(
                args=args,
                agent_name=getattr(args, "agent", None) or "unknown",
                outcome=outcome,
                task_response_capability=task_response_capability,
            )

        checkpoint_receipt = self._write_initial_atif_checkpoint(
            args=args,
            agent_name=agent_name,
        )
        if checkpoint_receipt is not None and checkpoint_receipt.status.value == "failed":
            print(
                "AWORLD_INITIAL_ATIF_CHECKPOINT="
                + json.dumps(checkpoint_receipt.to_dict(), ensure_ascii=False, sort_keys=True),
                file=sys.stderr,
            )

        live_summary = DirectRunLiveSummary(
            checkpoint_writer=(
                lambda summary: self._write_live_atif_checkpoint(
                    args=args,
                    agent_name=agent_name,
                    summary=summary,
                )
            ) if getattr(args, "trajectory_output", None) else None,
            checkpoint_interval_seconds=_live_atif_checkpoint_interval_seconds(),
        )
        try:
            direct_run_result = run_direct_async(
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
                    live_summary=live_summary,
                ),
                deadline_summary=live_summary.snapshot,
                one_shot=bool(args.non_interactive),
            )
            outcome = coerce_direct_run_outcome(direct_run_result)
        except KeyboardInterrupt:
            outcome = _direct_run_failure_outcome(
                stage=DirectRunStage.AGENT_EXECUTION,
                error_code=DirectRunErrorCode.DIRECT_RUN_INTERRUPTED,
                agent_name=agent_name,
                status=DirectRunStatus.CANCELLED,
                process_exit_code=130,
            )
        except asyncio.CancelledError:
            outcome = _direct_run_failure_outcome(
                stage=DirectRunStage.AGENT_EXECUTION,
                error_code=DirectRunErrorCode.DIRECT_RUN_CANCELLED,
                agent_name=agent_name,
                status=DirectRunStatus.CANCELLED,
                process_exit_code=130,
            )
        except DirectRunDeadlineExceeded as exc:
            try:
                deadline_stage = DirectRunStage(exc.stage)
            except (AttributeError, ValueError):
                deadline_stage = DirectRunStage.AGENT_EXECUTION
            startup_timeout = deadline_stage is DirectRunStage.PROVIDER_START
            if startup_timeout:
                outcome = _direct_run_failure_outcome(
                    stage=deadline_stage,
                    error_code=DirectRunErrorCode.PROVIDER_START_TIMEOUT,
                    agent_name=agent_name,
                    details={
                        "error_type": type(exc).__name__,
                        "phase": getattr(exc, "phase", "task_deadline"),
                    },
                    status=DirectRunStatus.INFRASTRUCTURE_FAILED,
                    summary=getattr(exc, "summary", None),
                )
            else:
                summary = getattr(exc, "summary", None)
                _emit_direct_run_agent_termination(
                    agent_name=agent_name,
                    summary=summary,
                    reason="task_deadline_exhausted",
                )
                outcome = DirectRunOutcome.from_summary(
                    summary,
                    status=DirectRunStatus.SUCCEEDED,
                )
        except Exception as exc:
            outcome = _direct_run_failure_outcome(
                stage=DirectRunStage.ORCHESTRATION,
                error_code=DirectRunErrorCode.DIRECT_RUN_EXCEPTION,
                agent_name=agent_name,
                details={"error_type": type(exc).__name__},
            )

        return self._finalize_outcome(
            args=args,
            agent_name=agent_name,
            outcome=outcome,
            task_response_capability=task_response_capability,
        )

    @staticmethod
    def _write_initial_atif_checkpoint(*, args, agent_name: str):
        """Atomically seed a valid incomplete ATIF before provider execution.

        A final outcome replaces this checkpoint.  If the enclosing container
        is killed before Python can run ``finally`` logic, Harbor still has a
        schema-valid record showing that completion was not established.
        """

        trajectory_output = getattr(args, "trajectory_output", None)
        if not trajectory_output:
            return None
        from aworld_cli.atif import build_atif_trajectory, try_write_atif_trajectory

        agent_version = _aworld_agent_version()
        try:
            trajectory = build_atif_trajectory(
                {
                    "trajectory": [],
                    "trajectory_capture_mode": "pre_execution_checkpoint",
                    "trajectory_fidelity": "partial",
                    "llm_call_count": 0,
                    "tool_call_count": 0,
                    "action_count": 0,
                },
                prompt=args.task,
                agent_name=agent_name,
                agent_version=agent_version,
                model_name=os.environ.get("LLM_MODEL_NAME"),
                run_outcome={
                    "semantic_status": "in_progress",
                    "process_exit_code": 1,
                    "trajectory_fidelity": "partial",
                    "llm_call_count": 0,
                    "tool_call_count": 0,
                    "action_count": 0,
                },
            )
        except Exception as exc:
            from aworld_cli.atif import AtifExportReceipt, AtifExportStatus

            return AtifExportReceipt(
                status=AtifExportStatus.FAILED,
                trajectory_fidelity="partial",
                error_code="atif_checkpoint_build_failed",
                error_type=type(exc).__name__,
            )
        return try_write_atif_trajectory(
            trajectory_output,
            trajectory,
            trajectory_fidelity="partial",
        )

    @staticmethod
    def _write_live_atif_checkpoint(*, args, agent_name: str, summary: dict):
        """Atomically persist live execution evidence without affecting the task."""

        trajectory_output = getattr(args, "trajectory_output", None)
        if not trajectory_output:
            return None
        from aworld_cli.atif import build_atif_trajectory, try_write_atif_trajectory
        from aworld_cli.main import _trajectory_payload_from_direct_run_summary
        from aworld_cli.run_outcome import DirectRunOutcome, DirectRunStatus

        payload = _trajectory_payload_from_direct_run_summary(
            summary,
            prompt=args.task,
            agent_name=agent_name,
        )
        metrics = DirectRunOutcome.from_summary(
            summary,
            status=DirectRunStatus.SUCCEEDED,
        )
        payload.update(
            {
                "trajectory_capture_mode": "live_context",
                "trajectory_fidelity": "partial",
                "llm_call_count": metrics.llm_call_count,
                "tool_call_count": metrics.tool_call_count,
                "action_count": metrics.action_count,
            }
        )
        trajectory = build_atif_trajectory(
            payload,
            prompt=args.task,
            agent_name=agent_name,
            agent_version=_aworld_agent_version(),
            model_name=os.environ.get("LLM_MODEL_NAME"),
            run_outcome={
                "semantic_status": "in_progress",
                "process_exit_code": 1,
                "trajectory_fidelity": "partial",
                "llm_call_count": metrics.llm_call_count,
                "tool_call_count": metrics.tool_call_count,
                "action_count": metrics.action_count,
                "last_successful_checkpoint": metrics.last_successful_checkpoint,
            },
        )
        return try_write_atif_trajectory(
            trajectory_output,
            trajectory,
            trajectory_fidelity="partial",
        )

    @staticmethod
    def _finalize_outcome(
        *,
        args,
        agent_name: str,
        outcome,
        task_response_capability: int | None = None,
    ) -> int:
        from aworld_cli.atif import (
            AtifExportReceipt,
            AtifExportStatus,
            build_atif_trajectory,
            try_write_atif_trajectory,
        )
        from aworld_cli.main import _trajectory_payload_from_direct_run_summary
        from aworld_cli.run_outcome import (
            DirectRunErrorCode,
            DirectRunStage,
            DirectRunStatus,
        )

        summary = outcome.summary
        trajectory_payload = _trajectory_payload_from_direct_run_summary(
            summary,
            prompt=args.task,
            agent_name=agent_name,
        )
        trajectory_payload.update(
            {
                "trajectory_fidelity": outcome.trajectory_fidelity,
                "llm_call_count": outcome.llm_call_count,
                "tool_call_count": outcome.tool_call_count,
                "action_count": outcome.action_count,
            }
        )

        task_response_path = os.environ.get(
            "AWORLD_SELF_EVOLVE_TASK_RESPONSE_PATH"
        )
        if task_response_path:
            _write_self_evolve_task_response(
                trajectory_payload,
                capability_fd=task_response_capability,
            )
            task_response_capability = None
        elif task_response_capability is not None:
            os.close(task_response_capability)
            task_response_capability = None

        if getattr(args, "emit_trajectory", False):
            print(
                json.dumps(
                    trajectory_payload,
                    ensure_ascii=False,
                )
            )

        trajectory_output = getattr(args, "trajectory_output", None)
        export_receipt = AtifExportReceipt(
            status=AtifExportStatus.NOT_REQUESTED,
            trajectory_fidelity=outcome.trajectory_fidelity,
        )
        if trajectory_output:
            try:
                agent_version = _aworld_agent_version()
                run_outcome_payload = outcome.to_dict()
                trajectory = build_atif_trajectory(
                    trajectory_payload,
                    prompt=args.task,
                    agent_name=agent_name,
                    agent_version=agent_version,
                    model_name=os.environ.get("LLM_MODEL_NAME"),
                    run_outcome=run_outcome_payload,
                )
                export_receipt = try_write_atif_trajectory(
                    trajectory_output,
                    trajectory,
                    trajectory_fidelity=outcome.trajectory_fidelity,
                )
            except Exception as exc:
                export_receipt = AtifExportReceipt(
                    status=AtifExportStatus.FAILED,
                    trajectory_fidelity=outcome.trajectory_fidelity,
                    error_code="atif_build_failed",
                    error_type=type(exc).__name__,
                )
            atif_marker = (
                "AWORLD_ATIF_EXPORT="
                + json.dumps(
                    export_receipt.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        else:
            atif_marker = None

        # ATIF is an observability artifact. Export failure is recorded in the
        # receipt but must never rewrite the independent task outcome.
        final_outcome = outcome

        outcome_marker = (
            "AWORLD_RUN_OUTCOME="
            + json.dumps(
                final_outcome.to_dict(atif_export=export_receipt.to_dict()),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        outcome_output = getattr(args, "outcome_output", None)
        if outcome_output:
            try:
                _write_outcome_sidecar(
                    outcome_output,
                    final_outcome.to_dict(atif_export=export_receipt.to_dict()),
                )
            except Exception as exc:
                final_outcome = replace(
                    final_outcome,
                    status=DirectRunStatus.INFRASTRUCTURE_FAILED,
                    process_exit_code=1,
                    failure_record={
                        "stage": DirectRunStage.ORCHESTRATION.value,
                        "error_code": DirectRunErrorCode.DIRECT_RUN_EXCEPTION.value,
                    },
                )
                outcome_marker = (
                    "AWORLD_RUN_OUTCOME="
                    + json.dumps(
                        final_outcome.to_dict(atif_export=export_receipt.to_dict()),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                print(
                    "Direct-run outcome sidecar write failed; "
                    f"error_type={type(exc).__name__}",
                    file=sys.stderr,
                )
        markers = [marker for marker in (atif_marker, outcome_marker) if marker]
        _write_final_markers(markers)
        return final_outcome.process_exit_code

    def _resolve_agent_name(self, args) -> str | None:
        agent_name = args.agent
        if not agent_name and args.agent_file:
            if len(args.agent_file) == 1:
                from aworld_cli.core.loader import init_agent_file

                try:
                    agent_name = init_agent_file(args.agent_file[0])
                    if not agent_name:
                        print(
                            "❌ Error: Could not extract an agent name from the file"
                        )
                        return None
                    print(f"ℹ️  Auto-detected agent name: {agent_name}")
                except Exception as exc:
                    print(
                        "❌ Error: Failed to load the agent file "
                        f"({type(exc).__name__}); path and exception text were omitted"
                    )
                    return None
            else:
                print("❌ Error: --agent is required when using multiple --agent-file")
                return None
        elif not agent_name:
            agent_name = "Aworld"
            print(f"ℹ️  Using default agent: {agent_name}")

        return agent_name
