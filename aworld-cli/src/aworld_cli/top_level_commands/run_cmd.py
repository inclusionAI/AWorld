from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from aworld_cli.async_runtime import run_direct_async
from aworld_cli.runtime_bootstrap import RuntimeBootstrapError, bootstrap_runtime


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
            _direct_run_failure_outcome,
            _resolve_agent_dirs,
            _run_direct_mode,
            _self_evolve_config_from_cli_mode,
            _show_banner,
            init_middlewares,
        )
        from aworld_cli.run_outcome import (
            DirectRunErrorCode,
            DirectRunStage,
            DirectRunStatus,
            coerce_direct_run_outcome,
        )

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
            )

        checkpoint_receipt = self._write_initial_atif_checkpoint(
            args=args,
            agent_name=agent_name,
        )
        if checkpoint_receipt is not None and checkpoint_receipt.status.value == "failed":
            outcome = _direct_run_failure_outcome(
                stage=DirectRunStage.ORCHESTRATION,
                error_code=DirectRunErrorCode.ATIF_EXPORT_FAILED,
                agent_name=agent_name,
            )
            return self._finalize_outcome(
                args=args,
                agent_name=agent_name,
                outcome=outcome,
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
                )
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

        try:
            import aworld

            agent_version = getattr(aworld, "__version__", "unknown")
        except Exception:
            agent_version = "unknown"
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
    def _finalize_outcome(*, args, agent_name: str, outcome) -> int:
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
                try:
                    import aworld

                    agent_version = getattr(aworld, "__version__", "unknown")
                except Exception:
                    agent_version = "unknown"
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

        final_outcome = outcome
        if (
            trajectory_output
            and export_receipt.status is AtifExportStatus.FAILED
        ):
            final_outcome = replace(
                outcome,
                status=DirectRunStatus.INFRASTRUCTURE_FAILED,
                process_exit_code=outcome.process_exit_code or 1,
                failure_record={
                    "stage": DirectRunStage.ORCHESTRATION.value,
                    "error_code": DirectRunErrorCode.ATIF_EXPORT_FAILED.value,
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
                    process_exit_code=final_outcome.process_exit_code or 1,
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
