from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SUPPORTED_APPLY_POLICIES = {"proposal", "auto_verified"}


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
    candidate_id = _read_report_value(report, "best_candidate_id") or _read_report_value(
        report,
        "selected_candidate_id",
    )

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
    if candidate_id:
        lines.append(f"Best candidate: {candidate_id}")
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
) -> Mapping[str, Any]:
    import aworld.self_evolve as self_evolve

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
        replay_enabled=apply == "auto_verified",
    )


def drain_pending_self_evolve_jobs(*, workspace_root: str) -> int:
    import aworld.self_evolve as self_evolve

    return self_evolve.drain_pending_self_evolve_jobs(workspace_root=workspace_root)


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
