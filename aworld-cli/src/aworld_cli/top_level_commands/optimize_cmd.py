from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable


SUPPORTED_APPLY_POLICIES = {"proposal", "auto_verified"}
AUTO_VERIFIED_JUDGE_REPETITIONS = 3
AUTO_VERIFIED_BASELINE_REPLAY_REPETITIONS = 2
AUTO_VERIFIED_CANDIDATE_REPLAY_REPETITIONS = 3


class OptimizeTopLevelCommand:
    @property
    def name(self) -> str:
        return "optimize"

    @property
    def description(self) -> str:
        return "Run a self-evolve optimization through framework APIs."

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    def register_parser(self, subparsers) -> None:
        parser = subparsers.add_parser(
            "optimize",
            help=self.description,
            description=self.description,
            prog="aworld-cli optimize",
        )
        parser.add_argument("--agent", type=str)
        parser.add_argument("--task", type=str)
        parser.add_argument("--target", type=str)
        parser.add_argument("--dataset", type=str)
        parser.add_argument("--from-session", type=str, dest="from_session")
        parser.add_argument("--from-trajectory", type=str, dest="from_trajectory")
        parser.add_argument("--batch-config", type=str, dest="batch_config")
        parser.add_argument("--iterations", type=int)
        parser.add_argument("--apply", type=str, default="proposal")
        parser.add_argument("--judge-agent", type=str, dest="judge_agent")
        parser.add_argument("--judge-agent-name", type=str, dest="judge_agent_name")
        parser.add_argument("--judge-backend-ref", type=str, dest="judge_backend_ref")
        parser.add_argument(
            "--replay-timeout",
            type=int,
            dest="replay_timeout_seconds",
            help="Timeout in seconds for each self-evolve replay rollout.",
        )
        parser.add_argument(
            "--replay-max-runs",
            type=int,
            dest="replay_max_steps",
            help="Maximum aworld-cli run iterations for each self-evolve replay rollout.",
        )
        parser.add_argument(
            "--judge-repetitions",
            type=int,
            dest="judge_repetitions",
            help="Number of successful judge samples to aggregate per evaluator call.",
        )
        parser.add_argument(
            "--baseline-replay-repetitions",
            type=int,
            dest="baseline_replay_repetitions",
            help="Number of baseline replay rollouts to aggregate.",
        )
        parser.add_argument(
            "--candidate-replay-repetitions",
            type=int,
            dest="candidate_replay_repetitions",
            help="Number of candidate replay rollouts to aggregate.",
        )
        parser.add_argument(
            "--drain-pending",
            action="store_true",
            dest="drain_pending",
            help="Drain pending framework-owned post-run self-evolve jobs.",
        )

    def run(self, args, context) -> int:
        if getattr(args, "drain_pending", False):
            drained = drain_pending_self_evolve_jobs(workspace_root=context.cwd)
            print(f"Drained pending self-evolve jobs: {drained}")
            return 0

        apply_policy = getattr(args, "apply", "proposal") or "proposal"
        if apply_policy not in SUPPORTED_APPLY_POLICIES:
            print("Optimize error: --apply must be one of proposal, auto_verified")
            return 0
        judge_selectors = [
            getattr(args, "judge_agent", None),
            getattr(args, "judge_agent_name", None),
            getattr(args, "judge_backend_ref", None),
        ]
        if sum(1 for value in judge_selectors if value) > 1:
            print("Optimize error: use only one of --judge-agent, --judge-agent-name, or --judge-backend-ref")
            return 1

        target = getattr(args, "target", None)
        try:
            report = run_optimize_cli(
                agent=getattr(args, "agent", None),
                task=getattr(args, "task", None),
                target=target,
                dataset=getattr(args, "dataset", None),
                from_session=getattr(args, "from_session", None),
                from_trajectory=getattr(args, "from_trajectory", None),
                batch_config=getattr(args, "batch_config", None),
                iterations=getattr(args, "iterations", None),
                apply=apply_policy,
                infer_target=target is None,
                workspace_root=context.cwd,
                judge_agent=getattr(args, "judge_agent", None),
                judge_agent_name=getattr(args, "judge_agent_name", None),
                judge_backend_ref=getattr(args, "judge_backend_ref", None),
                judge_repetitions=getattr(args, "judge_repetitions", None),
                replay_timeout_seconds=getattr(args, "replay_timeout_seconds", None),
                replay_max_steps=getattr(args, "replay_max_steps", None),
                baseline_replay_repetitions=getattr(args, "baseline_replay_repetitions", None),
                candidate_replay_repetitions=getattr(args, "candidate_replay_repetitions", None),
            )
        except (FileNotFoundError, ValueError, KeyError, NotImplementedError) as exc:
            print(f"Optimize error: {exc}")
            return 1

        print(render_optimize_summary(report))
        return 0


def render_optimize_summary(report: Any) -> str:
    report_path = _read_report_value(report, "report_path")
    status = _read_report_value(report, "status")
    target_selection_path = _read_report_value(report, "target_selection_path")
    replay_path = _read_report_value(report, "replay_path")
    evaluator_report_paths = _read_report_value(report, "evaluator_report_paths") or []
    best_candidate_id = _read_report_value(report, "best_candidate_id")
    selected_candidate_id = _read_report_value(report, "selected_candidate_id")
    failed_gate_names = _failed_gate_names(_read_report_value(report, "gate_results"))

    lines = ["Optimize run submitted."]
    if status:
        lines.append(f"Status: {status}")
    if report_path:
        lines.append(f"Report: {report_path}")
    if target_selection_path:
        lines.append(f"Target selection: {target_selection_path}")
    if replay_path:
        lines.append(f"Replay: {replay_path}")
    if isinstance(evaluator_report_paths, (list, tuple)):
        for report_path_item in evaluator_report_paths:
            if report_path_item:
                lines.append(f"Evaluator report: {report_path_item}")
    if best_candidate_id:
        lines.append(f"Best candidate: {best_candidate_id}")
    elif selected_candidate_id:
        lines.append(f"Selected candidate: {selected_candidate_id}")
    if status == "rejected" and failed_gate_names:
        lines.append(f"Rejected gates: {', '.join(failed_gate_names)}")
    return "\n".join(lines)


def run_optimize_cli(
    *,
    agent: str | None,
    task: str | None,
    target: str | None,
    dataset: str | None,
    from_session: str | None,
    from_trajectory: str | None,
    batch_config: str | None,
    iterations: int | None,
    apply: str,
    infer_target: bool,
    workspace_root: str,
    judge_agent: str | None = None,
    judge_agent_name: str | None = None,
    judge_backend_ref: str | None = None,
    judge_repetitions: int | None = None,
    replay_timeout_seconds: int | None = None,
    replay_max_steps: int | None = None,
    baseline_replay_repetitions: int | None = None,
    candidate_replay_repetitions: int | None = None,
    runtime_registry_refresher: Callable[[Any], Any] | None = None,
) -> Mapping[str, Any]:
    import aworld.self_evolve as self_evolve

    judge_repetitions = _auto_verified_default(
        apply,
        judge_repetitions,
        AUTO_VERIFIED_JUDGE_REPETITIONS,
    )
    baseline_replay_repetitions = _auto_verified_default(
        apply,
        baseline_replay_repetitions,
        AUTO_VERIFIED_BASELINE_REPLAY_REPETITIONS,
    )
    candidate_replay_repetitions = _auto_verified_default(
        apply,
        candidate_replay_repetitions,
        AUTO_VERIFIED_CANDIDATE_REPLAY_REPETITIONS,
    )
    judge_config = _judge_config_from_cli(
        judge_agent=judge_agent,
        judge_agent_name=judge_agent_name,
        judge_backend_ref=judge_backend_ref,
    )
    return self_evolve.optimize_from_cli_request(
        agent=agent,
        task=task,
        target=target,
        dataset=dataset,
        from_session=from_session,
        from_trajectory=from_trajectory,
        batch_config=batch_config,
        iterations=iterations,
        apply_policy=apply,
        infer_target=infer_target,
        workspace_root=workspace_root,
        judge_config=judge_config,
        **_judge_options(judge_repetitions=judge_repetitions),
        replay_enabled=apply == "auto_verified",
        runtime_registry_refresher=runtime_registry_refresher,
        **_replay_options(
            replay_timeout_seconds=replay_timeout_seconds,
            replay_max_steps=replay_max_steps,
            baseline_replay_repetitions=baseline_replay_repetitions,
            candidate_replay_repetitions=candidate_replay_repetitions,
        ),
    )


def _auto_verified_default(
    apply_policy: str,
    value: int | None,
    default: int,
) -> int | None:
    if value is not None or apply_policy != "auto_verified":
        return value
    return default


def _judge_options(*, judge_repetitions: int | None) -> dict[str, int]:
    options: dict[str, int] = {}
    if judge_repetitions is not None:
        options["judge_repetitions"] = judge_repetitions
    return options


def _replay_options(
    *,
    replay_timeout_seconds: int | None,
    replay_max_steps: int | None,
    baseline_replay_repetitions: int | None,
    candidate_replay_repetitions: int | None,
) -> dict[str, int]:
    options: dict[str, int] = {}
    if replay_timeout_seconds is not None:
        options["replay_timeout_seconds"] = replay_timeout_seconds
    if replay_max_steps is not None:
        options["replay_max_steps"] = replay_max_steps
    if baseline_replay_repetitions is not None:
        options["baseline_replay_repetitions"] = baseline_replay_repetitions
    if candidate_replay_repetitions is not None:
        options["candidate_replay_repetitions"] = candidate_replay_repetitions
    return options


def drain_pending_self_evolve_jobs(
    *,
    workspace_root: str,
    runtime_registry_refresher: Callable[[Any], Any] | None = None,
) -> int:
    import aworld.self_evolve as self_evolve

    return self_evolve.drain_pending_self_evolve_jobs(
        workspace_root=workspace_root,
        runtime_registry_refresher=runtime_registry_refresher,
    )


def _judge_config_from_cli(
    *,
    judge_agent: str | None,
    judge_agent_name: str | None,
    judge_backend_ref: str | None,
) -> Any:
    selector_count = sum(bool(value) for value in (judge_agent, judge_agent_name, judge_backend_ref))
    if selector_count > 1:
        raise ValueError("use only one of --judge-agent, --judge-agent-name, or --judge-backend-ref")
    if judge_agent:
        from aworld.config.conf import SelfEvolveJudgeConfig

        return SelfEvolveJudgeConfig(mode="agent_md", agent_path=judge_agent)
    if judge_agent_name:
        from aworld.config.conf import SelfEvolveJudgeConfig

        return SelfEvolveJudgeConfig(mode="custom_agent", agent_id=judge_agent_name)
    if judge_backend_ref:
        from aworld.config.conf import SelfEvolveJudgeConfig

        return SelfEvolveJudgeConfig(mode="backend_ref", backend_ref=judge_backend_ref)
    return None


def _read_report_value(report: Any, key: str) -> Any:
    if isinstance(report, Mapping):
        return report.get(key)
    return getattr(report, key, None)


def _failed_gate_names(gate_results: Any) -> list[str]:
    if not isinstance(gate_results, (list, tuple)):
        return []
    names: list[str] = []
    for item in gate_results:
        if not isinstance(item, Mapping):
            continue
        if item.get("passed") is not False:
            continue
        gate_name = item.get("gate_name")
        if isinstance(gate_name, str):
            names.append(gate_name)
    return names
