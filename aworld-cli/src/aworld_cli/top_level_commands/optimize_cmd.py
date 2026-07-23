from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable


SUPPORTED_APPLY_POLICIES = {"proposal", "auto_verified"}
SUPPORTED_NEW_SKILL_POLICIES = {"disabled", "draft_only", "auto_verified"}
AUTO_VERIFIED_JUDGE_REPETITIONS = 1
AUTO_VERIFIED_JUDGE_TIMEOUT_SECONDS = 120
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
        parser.add_argument(
            "--from-trajectory",
            type=str,
            dest="from_trajectory",
            help=(
                "AWorld trajectory log. Logs may contain one or more task records; "
                "the framework auto-groups multi-record logs by inferred target."
            ),
        )
        parser.add_argument(
            "--from-trajectory-set",
            type=str,
            dest="from_trajectory_set",
            help=(
                "Advanced explicit trajectory-set JSON for callers that already "
                "know member roles, target, and validation structure."
            ),
        )
        parser.add_argument(
            "--include-prior-runs",
            action="store_true",
            dest="include_prior_runs",
            help="Include prior self-evolve runs for the same target as advisory trajectory-set members.",
        )
        parser.add_argument(
            "--from-run",
            type=str,
            dest="from_run",
            help="Reuse artifacts from a previous self-evolve run.",
        )
        parser.add_argument(
            "--rerun-evaluator",
            action="store_true",
            dest="rerun_evaluator",
            help="Reuse replay artifacts from --from-run and rerun evaluator/gates only.",
        )
        parser.add_argument("--batch-config", type=str, dest="batch_config")
        parser.add_argument("--iterations", type=int)
        parser.add_argument("--apply", type=str)
        parser.add_argument(
            "--max-improvement-cycles",
            type=int,
            default=3,
            dest="max_improvement_cycles",
            help="Maximum bounded cross-run self-improvement cycles for auto_verified.",
        )
        parser.add_argument(
            "--resume-campaign",
            type=str,
            dest="resume_campaign",
            help="Resume a paused or active self-improvement campaign.",
        )
        parser.add_argument(
            "--new-skill-policy",
            type=str,
            choices=sorted(SUPPORTED_NEW_SKILL_POLICIES),
            default="auto_verified",
            dest="new_skill_policy",
            help="Policy for inferred missing capabilities: disabled, draft_only, or auto_verified.",
        )
        parser.add_argument("--judge-agent", type=str, dest="judge_agent")
        parser.add_argument("--judge-agent-name", type=str, dest="judge_agent_name")
        parser.add_argument("--judge-backend-ref", type=str, dest="judge_backend_ref")
        parser.add_argument("--judge-model-profile", type=str, dest="judge_model_profile")
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
            "--judge-timeout",
            type=int,
            dest="judge_timeout_seconds",
            help="Timeout in seconds for each self-evolve judge attempt.",
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

        resume_campaign = getattr(args, "resume_campaign", None)
        if (
            resume_campaign
            and getattr(args, "apply", None) not in {None, "auto_verified"}
        ):
            print("Optimize error: --resume-campaign requires --apply auto_verified")
            return 1
        apply_policy = getattr(args, "apply", None) or (
            "auto_verified" if resume_campaign else "proposal"
        )
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
                from_trajectory_set=getattr(args, "from_trajectory_set", None),
                include_prior_runs=getattr(args, "include_prior_runs", False),
                from_run=getattr(args, "from_run", None),
                rerun_evaluator=getattr(args, "rerun_evaluator", False),
                batch_config=getattr(args, "batch_config", None),
                iterations=getattr(args, "iterations", None),
                max_improvement_cycles=getattr(
                    args, "max_improvement_cycles", 3
                ),
                resume_campaign=resume_campaign,
                apply=apply_policy,
                new_skill_policy=getattr(args, "new_skill_policy", "auto_verified"),
                infer_target=target is None,
                workspace_root=context.cwd,
                judge_agent=getattr(args, "judge_agent", None),
                judge_agent_name=getattr(args, "judge_agent_name", None),
                judge_backend_ref=getattr(args, "judge_backend_ref", None),
                judge_model_profile=getattr(args, "judge_model_profile", None),
                judge_repetitions=getattr(args, "judge_repetitions", None),
                judge_timeout_seconds=getattr(args, "judge_timeout_seconds", None),
                replay_timeout_seconds=getattr(args, "replay_timeout_seconds", None),
                replay_max_steps=getattr(args, "replay_max_steps", None),
                baseline_replay_repetitions=getattr(args, "baseline_replay_repetitions", None),
                candidate_replay_repetitions=getattr(args, "candidate_replay_repetitions", None),
                progress_callback=_print_optimize_progress,
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
    run_id = _read_report_value(report, "run_id")
    grouping_summary = _target_grouping_summary(report)
    replay_failure_summary = _replay_failure_summary(report)
    promotion = _read_report_value(report, "promotion")
    campaign_id = _read_report_value(report, "campaign_id")
    campaign_status = _read_report_value(report, "campaign_status")
    campaign_cycle = _read_report_value(report, "campaign_cycle")
    campaign_max_cycles = _read_report_value(report, "campaign_max_cycles")
    disposition = _read_report_value(report, "self_improvement_disposition")
    goal_handoff_path = _read_report_value(report, "goal_handoff_path")

    lines = ["Optimize run submitted."]
    if status:
        lines.append(f"Status: {status}")
    if campaign_id:
        lines.append(f"Campaign: {campaign_id}")
    if campaign_status:
        lines.append(f"Campaign status: {campaign_status}")
    if campaign_cycle is not None and campaign_max_cycles is not None:
        lines.append(f"Campaign cycle: {campaign_cycle}/{campaign_max_cycles}")
    if isinstance(disposition, Mapping) and disposition.get("reason_code"):
        lines.append(
            "Self-improvement: "
            f"{disposition.get('kind')} ({disposition['reason_code']})"
        )
    if goal_handoff_path:
        lines.append(f"Goal handoff: {goal_handoff_path}")
        if campaign_id:
            lines.append(f"Continue goal: /goal --from-campaign {campaign_id}")
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
    if grouping_summary:
        lines.append(f"Target grouping: {grouping_summary}")
    if isinstance(promotion, Mapping) and promotion.get("status"):
        lines.append(f"New skill: {promotion['status']}")
    if status == "rejected" and failed_gate_names:
        lines.append(f"Rejected gates: {', '.join(failed_gate_names)}")
    if status == "rejected" and replay_failure_summary:
        lines.append(f"Replay failures: {replay_failure_summary}")
    if status == "rejected" and _has_no_candidate(report):
        lines.append(
            "No candidate generated: optimizer produced no non-noop candidate, "
            "so replay/evaluation/apply were skipped."
        )
    if (
        status == "rejected"
        and replay_path
        and run_id
        and _has_judge_timeout(report)
        and not _has_replay_repetition_failure(report)
    ):
        lines.append(
            "Resume evaluator: "
            f"aworld-cli optimize --from-run {run_id} --rerun-evaluator"
        )
    if status == "rejected" and replay_path and _has_replay_repetition_failure(report):
        lines.append(
            "Replay recovery: rerun full optimize with a higher --replay-timeout; "
            "--from-run --rerun-evaluator cannot add missing replay repetitions."
        )
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
    max_improvement_cycles: int = 1,
    resume_campaign: str | None = None,
    apply: str,
    infer_target: bool,
    workspace_root: str,
    new_skill_policy: str = "auto_verified",
    include_prior_runs: bool = False,
    judge_agent: str | None = None,
    judge_agent_name: str | None = None,
    judge_backend_ref: str | None = None,
    judge_model_profile: str | None = None,
    judge_repetitions: int | None = None,
    judge_timeout_seconds: int | None = None,
    replay_timeout_seconds: int | None = None,
    replay_max_steps: int | None = None,
    baseline_replay_repetitions: int | None = None,
    candidate_replay_repetitions: int | None = None,
    runtime_registry_refresher: Callable[[Any], Any] | None = None,
    runtime_skill_activator: Callable[[Any], Any] | None = None,
    progress_callback: Callable[[str, str], Any] | None = None,
    from_run: str | None = None,
    rerun_evaluator: bool = False,
    from_trajectory_set: str | None = None,
) -> Mapping[str, Any]:
    import aworld.self_evolve as self_evolve

    runtime_apply = "auto_verified" if resume_campaign else apply
    judge_repetitions = _auto_verified_default(
        runtime_apply,
        judge_repetitions,
        AUTO_VERIFIED_JUDGE_REPETITIONS,
    )
    judge_timeout_seconds = _auto_verified_default(
        runtime_apply,
        judge_timeout_seconds,
        AUTO_VERIFIED_JUDGE_TIMEOUT_SECONDS,
    )
    baseline_replay_repetitions = _auto_verified_default(
        runtime_apply,
        baseline_replay_repetitions,
        AUTO_VERIFIED_BASELINE_REPLAY_REPETITIONS,
    )
    candidate_replay_repetitions = _auto_verified_default(
        runtime_apply,
        candidate_replay_repetitions,
        AUTO_VERIFIED_CANDIDATE_REPLAY_REPETITIONS,
    )
    judge_config = _judge_config_from_cli(
        judge_agent=judge_agent,
        judge_agent_name=judge_agent_name,
        judge_backend_ref=judge_backend_ref,
        judge_model_profile=judge_model_profile,
    )
    mutation_model_config = (
        None if rerun_evaluator else _default_mutation_model_config()
    )
    if progress_callback is not None:
        progress_callback("prepare", "Preparing self-evolve optimize request")
    request = {
        "agent": agent,
        "task": task,
        "target": target,
        "dataset": dataset,
        "from_session": from_session,
        "from_trajectory": from_trajectory,
        "from_trajectory_set": from_trajectory_set,
        "include_prior_runs": include_prior_runs,
        "from_run": from_run,
        "rerun_evaluator": rerun_evaluator,
        "batch_config": batch_config,
        "iterations": iterations,
        "apply_policy": runtime_apply,
        "inferred_new_skill_policy": new_skill_policy,
        "infer_target": infer_target,
        "workspace_root": workspace_root,
        "judge_config": judge_config,
        "mutation_model_config": mutation_model_config,
        "concurrency_policy": self_evolve.SelfEvolveConcurrencyPolicy(),
        **_judge_options(
            judge_repetitions=judge_repetitions,
            judge_timeout_seconds=judge_timeout_seconds,
        ),
        "replay_enabled": runtime_apply == "auto_verified",
        "runtime_registry_refresher": runtime_registry_refresher,
        "runtime_skill_activator": runtime_skill_activator
        or _default_runtime_skill_activator(),
        "progress_callback": progress_callback,
        **_replay_options(
            replay_timeout_seconds=replay_timeout_seconds,
            replay_max_steps=replay_max_steps,
            baseline_replay_repetitions=baseline_replay_repetitions,
            candidate_replay_repetitions=candidate_replay_repetitions,
        ),
    }
    if not rerun_evaluator and (
        resume_campaign
        or (runtime_apply == "auto_verified" and max_improvement_cycles > 1)
    ):
        return self_evolve.run_self_improvement_campaign(
            workspace_root=workspace_root,
            request=request,
            max_improvement_cycles=max_improvement_cycles,
            resume_campaign=resume_campaign,
        )
    return self_evolve.optimize_from_cli_request(**request)


def _default_mutation_model_config():
    """Resolve mutation independently from all judge-specific CLI options."""

    from aworld_cli.core.model_profiles import resolve_model_profile

    try:
        return resolve_model_profile("default")
    except KeyError:
        return None


def _default_runtime_skill_activator() -> Callable[[Any], Mapping[str, Any]]:
    def activate(candidate: Any) -> Mapping[str, Any]:
        from aworld_cli.core.skill_state_manager import SkillStateManager

        target = getattr(candidate, "target", None)
        skill_name = getattr(target, "target_id", None)
        if not skill_name:
            return {"status": "skipped", "reason": "candidate target has no skill name"}
        manager = SkillStateManager()
        was_enabled = manager.is_enabled(str(skill_name))
        manager.enable_skill(str(skill_name))
        return {
            "status": "enabled",
            "skill_name": str(skill_name),
            "was_enabled": was_enabled,
            "enabled": manager.is_enabled(str(skill_name)),
        }

    return activate


def _auto_verified_default(
    apply_policy: str,
    value: int | None,
    default: int,
) -> int | None:
    if value is not None or apply_policy != "auto_verified":
        return value
    return default


def _print_optimize_progress(stage: str, message: str) -> None:
    print(f"[self-evolve:{stage}] {message}", flush=True)


def _judge_options(
    *,
    judge_repetitions: int | None,
    judge_timeout_seconds: int | None,
) -> dict[str, int]:
    options: dict[str, int] = {}
    if judge_repetitions is not None:
        options["judge_repetitions"] = judge_repetitions
    if judge_timeout_seconds is not None:
        options["judge_timeout_seconds"] = judge_timeout_seconds
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
    judge_model_profile: str | None = None,
) -> Any:
    selector_count = sum(bool(value) for value in (judge_agent, judge_agent_name, judge_backend_ref))
    if selector_count > 1:
        raise ValueError("use only one of --judge-agent, --judge-agent-name, or --judge-backend-ref")
    if judge_agent:
        from aworld.config.conf import SelfEvolveJudgeConfig

        return SelfEvolveJudgeConfig(mode="agent_md", agent_path=judge_agent, model_profile=judge_model_profile)
    if judge_agent_name:
        from aworld.config.conf import SelfEvolveJudgeConfig

        return SelfEvolveJudgeConfig(mode="custom_agent", agent_id=judge_agent_name, model_profile=judge_model_profile)
    if judge_backend_ref:
        from aworld.config.conf import SelfEvolveJudgeConfig

        return SelfEvolveJudgeConfig(mode="backend_ref", backend_ref=judge_backend_ref, model_profile=judge_model_profile)
    return None


def _read_report_value(report: Any, key: str) -> Any:
    if isinstance(report, Mapping):
        return report.get(key)
    return getattr(report, key, None)


def _target_grouping_summary(report: Any) -> str | None:
    trajectory_set = _read_report_value(report, "trajectory_set")
    if not isinstance(trajectory_set, Mapping):
        return None
    grouping = trajectory_set.get("auto_grouping")
    if not isinstance(grouping, Mapping) or not grouping.get("auto_grouped"):
        return None
    selected = grouping.get("selected_group_id")
    selected_count = grouping.get("selected_case_count")
    group_count = grouping.get("group_count")
    largest_count = grouping.get("largest_group_case_count")
    if not selected:
        return None
    summary = f"{selected} ({selected_count or 0} case(s), {group_count or 0} group(s))"
    if grouping.get("low_dataset_support"):
        summary += f"; low dataset support, largest group has {largest_count or 0} case(s)"
    return summary


def _replay_failure_summary(report: Any) -> str | None:
    parts: list[str] = []
    for label, key in (
        ("baseline", "baseline_metrics"),
        ("candidate", "candidate_metrics"),
        ("held_out", "held_out_metrics"),
    ):
        metrics = _read_report_value(report, key)
        summary = _metrics_replay_failure_summary(metrics)
        if summary:
            parts.append(f"{label}: {summary}")
    return "; ".join(parts) if parts else None


def _metrics_replay_failure_summary(metrics: Any) -> str | None:
    if not isinstance(metrics, Mapping):
        return None
    failed_count = _int_or_none(
        metrics.get("failed_repetition_count")
        or metrics.get("replay_failed_repetition_count")
    )
    reasons = _string_list(
        metrics.get("replay_failure_reasons")
        or metrics.get("replay_failure_types")
    )
    if failed_count is None or failed_count <= 0:
        return None
    if reasons:
        return f"{failed_count} failed repetition(s): {', '.join(reasons[:4])}"
    return f"{failed_count} failed repetition(s)"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if item]


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


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


def _has_judge_timeout(report: Any) -> bool:
    for key in ("baseline_metrics", "candidate_metrics", "held_out_metrics"):
        metrics = _read_report_value(report, key)
        if _metrics_have_judge_timeout(metrics):
            return True
    return False


def _has_replay_repetition_failure(report: Any) -> bool:
    for key in ("baseline_metrics", "candidate_metrics", "held_out_metrics"):
        metrics = _read_report_value(report, key)
        if _metrics_have_replay_repetition_failure(metrics):
            return True
    return False


def _has_no_candidate(report: Any) -> bool:
    selected_candidate_id = _read_report_value(report, "selected_candidate_id")
    candidate_ids = _read_report_value(report, "candidate_ids")
    if selected_candidate_id:
        return False
    if isinstance(candidate_ids, list) and candidate_ids:
        return False
    iterations = _read_report_value(report, "iterations")
    if not isinstance(iterations, list):
        return False
    return any(
        isinstance(iteration, Mapping) and iteration.get("status") == "no_candidate"
        for iteration in iterations
    )


def _metrics_have_replay_repetition_failure(metrics: Any) -> bool:
    if not isinstance(metrics, Mapping):
        return False
    repetition_count = metrics.get("repetition_count")
    successful_count = metrics.get("successful_repetition_count")
    failed_count = metrics.get("failed_repetition_count")
    if (
        isinstance(repetition_count, (int, float))
        and isinstance(successful_count, (int, float))
        and int(repetition_count) > int(successful_count)
    ):
        return True
    if isinstance(failed_count, (int, float)) and int(failed_count) > 0:
        return True
    failure_types = metrics.get("replay_failure_types") or metrics.get("replay_repetition_failures")
    return _contains_timeout_failure(failure_types)


def _metrics_have_judge_timeout(metrics: Any) -> bool:
    if not isinstance(metrics, Mapping):
        return False
    failures = metrics.get("judge_failures")
    if not isinstance(failures, (list, tuple)):
        return False
    return _contains_timeout_failure(failures)


def _contains_timeout_failure(value: Any) -> bool:
    if not isinstance(value, (list, tuple)):
        return False
    for failure in value:
        if isinstance(failure, str):
            if failure == "TimeoutExpired" or "timed out" in failure.lower():
                return True
            continue
        if not isinstance(failure, Mapping):
            continue
        failure_type = str(failure.get("type") or "")
        reason = str(failure.get("reason") or "")
        if failure_type in {"TimeoutError", "TimeoutExpired"} or "timed out" in reason.lower():
            return True
    return False
