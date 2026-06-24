from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping


TASK_ID_DEFAULT = "task_20260609193335"
TRAJECTORY_LOG_DEFAULT = "~/Documents/logs/trajectory.log"
EVALUATOR_AGENT_DEFAULT: str | None = None


RunCli = Callable[[list[str], Mapping[str, str], Path], subprocess.CompletedProcess[str]]


def run_test_case(
    *,
    trajectory_log: str | Path,
    task_id: str,
    out_dir: str | Path,
    workspace_root: str | Path,
    evaluator_agent_md: str | Path | None,
    apply_policy: str = "proposal",
    run_cli: RunCli | None = None,
) -> dict[str, Any]:
    if apply_policy not in {"proposal", "auto_verified"}:
        raise ValueError("apply_policy must be one of: proposal, auto_verified")
    workspace = Path(workspace_root).resolve()
    output_dir = Path(out_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    record = _find_record(Path(trajectory_log).expanduser(), task_id=task_id)
    extracted = _extract_record(record)
    filtered_log = output_dir / f"trajectory_{task_id}.log"
    extracted_path = output_dir / f"extracted_{task_id}.json"
    filtered_log.write_text(repr(record) + "\n", encoding="utf-8")
    _write_json(extracted_path, extracted)

    command = [
        sys.executable,
        "-m",
        "aworld_cli.main",
        "optimize",
        "--from-trajectory",
        str(filtered_log),
        "--apply",
        apply_policy,
    ]
    evaluator_agent_path = (
        Path(evaluator_agent_md).expanduser()
        if evaluator_agent_md is not None
        else None
    )
    if (
        apply_policy == "auto_verified"
        and evaluator_agent_path is not None
        and evaluator_agent_path.exists()
    ):
        command.extend(["--judge-agent", str(evaluator_agent_path)])
    env = dict(os.environ)
    pythonpath_parts = [
        str(workspace / "aworld-cli" / "src"),
        str(workspace),
    ]
    if env.get("PYTHONPATH"):
        pythonpath_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    runner = run_cli or _run_cli
    completed = runner(command, env, workspace)
    cli_stdout_path = output_dir / f"aworld_cli_optimize_{task_id}.stdout.txt"
    cli_stderr_path = output_dir / f"aworld_cli_optimize_{task_id}.stderr.txt"
    cli_stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    cli_stderr_path.write_text(completed.stderr or "", encoding="utf-8")

    if completed.returncode != 0:
        report = None
        target_selection = None
        report_path = None
        target_selection_path = None
    else:
        report_path = _path_from_cli_stdout(completed.stdout or "", "Report")
        target_selection_path = _path_from_cli_stdout(completed.stdout or "", "Target selection")
        report = _load_json_if_exists(report_path)
        target_selection = _load_json_if_exists(target_selection_path)

    evaluator = _evaluator_agent_summary(
        evaluator_agent_md,
        usage=(
            "aworld_trajectory_evaluator"
            if apply_policy == "auto_verified"
            and evaluator_agent_path is not None
            and evaluator_agent_path.exists()
            else "rubric_reference"
        ),
    )
    self_evolve_summary = _self_evolve_summary(
        completed=completed,
        command=command,
        report=report,
        report_path=report_path,
        target_selection=target_selection,
        target_selection_path=target_selection_path,
    )
    evaluation = _evaluate_design_goal(
        self_evolve=self_evolve_summary,
        report=report,
        target_selection=target_selection,
        apply_policy=apply_policy,
    )

    result: dict[str, Any] = {
        "task_id": task_id,
        "question": extracted.get("question"),
        "baseline": {
            "num_steps": extracted.get("num_steps"),
            "evidence_blocks": len(extracted.get("evidence", [])),
            "final_answer_len": len(extracted.get("final_answer") or ""),
            "source_trajectory_log": str(Path(trajectory_log).expanduser()),
        },
        "evaluator_agent": evaluator,
        "self_evolve": self_evolve_summary,
        "evaluation": evaluation,
        "artifacts": {
            "filtered_trajectory_log": str(filtered_log),
            "extracted_trajectory": str(extracted_path),
            "cli_stdout": str(cli_stdout_path),
            "cli_stderr": str(cli_stderr_path),
            "json_report": str(output_dir / f"self_evolve_test_report_{task_id}.json"),
            "markdown_report": str(output_dir / f"self_evolve_test_report_{task_id}.md"),
        },
    }
    _write_json(Path(result["artifacts"]["json_report"]), result)
    Path(result["artifacts"]["markdown_report"]).write_text(
        _render_markdown_report(result),
        encoding="utf-8",
    )
    return result


def _run_cli(
    command: list[str],
    env: Mapping[str, str],
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=dict(env),
        text=True,
        capture_output=True,
        timeout=120,
    )


def _find_record(log_path: Path, *, task_id: str) -> dict[str, Any]:
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if task_id not in line:
                continue
            record = _parse_record_line(line)
            if record is not None and record.get("task_id") == task_id:
                return record
    raise ValueError(f"task_id {task_id!r} not found in {log_path}")


def _parse_record_line(line: str) -> dict[str, Any] | None:
    clean = _strip_ansi(line).strip()
    start = clean.find("{")
    if start < 0:
        return None
    try:
        value = ast.literal_eval(clean[start:])
    except (SyntaxError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    if "task_id" not in value or "trajectory" not in value:
        return None
    return value


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _extract_record(record: Mapping[str, Any]) -> dict[str, Any]:
    task_id = str(record["task_id"])
    trajectory = json.loads(str(record["trajectory"]))
    if not isinstance(trajectory, list):
        trajectory = []
    question = None
    steps: list[dict[str, Any]] = []
    final_answer = None
    evidence: list[dict[str, Any]] = []

    if trajectory:
        state_input = trajectory[0].get("state", {}).get("input", {})
        if isinstance(state_input, Mapping):
            question = state_input.get("content")

    for item in trajectory:
        if not isinstance(item, Mapping):
            continue
        meta = item.get("meta") if isinstance(item.get("meta"), Mapping) else {}
        action = item.get("action") if isinstance(item.get("action"), Mapping) else {}
        tool_calls = []
        for call in action.get("tool_calls") or []:
            if not isinstance(call, Mapping):
                continue
            function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
            tool_calls.append(
                {
                    "name": function.get("name"),
                    "arguments": _bounded_text(function.get("arguments"), limit=2000),
                }
            )
        finished = str(action.get("is_agent_finished")).strip().lower() in {"true", "1"}
        content = str(action.get("content") or "")
        if finished and content:
            final_answer = content
        steps.append(
            {
                "step": meta.get("step"),
                "agent_id": meta.get("agent_id"),
                "pre_agent": meta.get("pre_agent"),
                "tool_calls": tool_calls,
                "assistant_content": _bounded_text(content, limit=4000),
                "is_agent_finished": finished,
            }
        )

    if trajectory:
        final_state = trajectory[-1].get("state") if isinstance(trajectory[-1], Mapping) else {}
        messages = final_state.get("messages") if isinstance(final_state, Mapping) else []
        for index, message in enumerate(messages or []):
            if isinstance(message, Mapping) and message.get("role") == "tool":
                evidence.append(
                    {
                        "msg_index": index,
                        "content": _bounded_text(message.get("content"), limit=12000),
                    }
                )

    return {
        "task_id": task_id,
        "is_sub_task": record.get("is_sub_task"),
        "num_steps": len(trajectory),
        "question": question,
        "steps": steps,
        "final_answer": final_answer,
        "evidence": evidence,
    }


def _self_evolve_summary(
    *,
    completed: subprocess.CompletedProcess[str],
    command: list[str],
    report: Mapping[str, Any] | None,
    report_path: Path | None,
    target_selection: Mapping[str, Any] | None,
    target_selection_path: Path | None,
) -> dict[str, Any]:
    target = None
    if isinstance(report, Mapping):
        target = report.get("target")
    if not isinstance(target, Mapping) and isinstance(target_selection, Mapping):
        target = target_selection.get("selected_target")
    target_ref = _target_ref(target)
    candidate_ids = report.get("candidate_ids", []) if isinstance(report, Mapping) else []
    selected_candidate_id = (
        report.get("selected_candidate_id") if isinstance(report, Mapping) else None
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "status": report.get("status") if isinstance(report, Mapping) else None,
        "run_id": report.get("run_id") if isinstance(report, Mapping) else None,
        "target_ref": target_ref,
        "target": target,
        "target_selection": target_selection,
        "candidate_ids": candidate_ids,
        "selected_candidate_id": selected_candidate_id,
        "unsupported_target": (
            report.get("unsupported_target") if isinstance(report, Mapping) else None
        ),
        "gate_results": report.get("gate_results", []) if isinstance(report, Mapping) else [],
        "candidate_metrics": report.get("candidate_metrics") if isinstance(report, Mapping) else None,
        "baseline_metrics": report.get("baseline_metrics") if isinstance(report, Mapping) else None,
        "report_path": str(report_path) if report_path is not None else None,
        "target_selection_path": (
            str(target_selection_path) if target_selection_path is not None else None
        ),
    }


def _evaluate_design_goal(
    *,
    self_evolve: Mapping[str, Any],
    report: Mapping[str, Any] | None,
    target_selection: Mapping[str, Any] | None,
    apply_policy: str,
) -> dict[str, Any]:
    reasons: list[str] = []
    target = self_evolve.get("target") if isinstance(self_evolve.get("target"), Mapping) else {}
    target_type = target.get("target_type")
    candidate_ids = self_evolve.get("candidate_ids") or []

    if self_evolve.get("returncode") != 0:
        reasons.append("aworld-cli optimize failed")
    if self_evolve.get("status") == "rejected":
        reasons.append("self-evolve run was rejected")
    if self_evolve.get("unsupported_target"):
        reasons.append("unsupported target selected by framework target inference")
    if target_type in {"config", "prompt-section", "tool-description", "workspace-artifact"}:
        reasons.append(f"selected target type {target_type!r} is not an available phase-1 CLI mutation path")
    if not candidate_ids:
        reasons.append("no candidate was generated")
    if not self_evolve.get("selected_candidate_id"):
        reasons.append("no candidate was selected")
    if not self_evolve.get("candidate_metrics"):
        reasons.append("no candidate metrics were produced")

    selected_target = (
        target_selection.get("selected_target")
        if isinstance(target_selection, Mapping)
        else None
    )
    if selected_target is None:
        reasons.append("target inference did not select a target")

    design_goal_satisfied = not reasons
    return {
        "design_goal": (
            "Using current AWorld self-evolve on the baseline trajectory should produce "
            "an actionable improvement path for aworld-cli Aworld main on the same task."
        ),
        "design_goal_satisfied": design_goal_satisfied,
        "verdict": "Pass" if design_goal_satisfied else "Fail",
        "reasons": reasons,
        "notes": [
            "This test intentionally uses aworld-cli optimize without --target so the framework owns target inference.",
            (
                "Proposal-only mode is used; the script does not mutate runtime behavior."
                if apply_policy == "proposal"
                else "auto_verified mode is used; framework gates decide whether to mutate allowlisted targets."
            ),
        ],
    }


def _evaluator_agent_summary(
    path_value: str | Path | None,
    *,
    usage: str = "rubric_reference",
) -> dict[str, Any]:
    if path_value is None:
        return {"path": None, "exists": False}
    path = Path(path_value).expanduser()
    if not path.exists():
        return {"path": str(path), "exists": False}
    content = path.read_text(encoding="utf-8", errors="replace")
    frontmatter = _frontmatter(content)
    return {
        "path": str(path),
        "exists": True,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "name": frontmatter.get("name"),
        "description": frontmatter.get("description"),
        "usage": usage,
    }


def _frontmatter(content: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---", content, flags=re.DOTALL)
    if match is None:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def _path_from_cli_stdout(stdout: str, label: str) -> Path | None:
    prefix = f"{label}:"
    for line in stdout.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix) :].strip()
            if value:
                return Path(value)
    return None


def _load_json_if_exists(path: Path | None) -> Mapping[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _target_ref(target: Any) -> str | None:
    if not isinstance(target, Mapping):
        return None
    target_type = target.get("target_type")
    target_id = target.get("target_id")
    if not target_type or not target_id:
        return None
    return f"{target_type}:{target_id}"


def _bounded_text(value: Any, *, limit: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _render_markdown_report(result: Mapping[str, Any]) -> str:
    self_evolve = result["self_evolve"]
    evaluation = result["evaluation"]
    reasons = "\n".join(f"- {reason}" for reason in evaluation["reasons"]) or "- none"
    return (
        f"# Self-Evolve CLI Trajectory Test: {result['task_id']}\n\n"
        f"## Question\n\n{result.get('question')}\n\n"
        "## Baseline\n\n"
        f"- Steps: {result['baseline']['num_steps']}\n"
        f"- Evidence blocks: {result['baseline']['evidence_blocks']}\n"
        f"- Final answer length: {result['baseline']['final_answer_len']}\n\n"
        "## Self-Evolve Run\n\n"
        f"- Run id: {self_evolve.get('run_id')}\n"
        f"- Status: {self_evolve.get('status')}\n"
        f"- Target: {self_evolve.get('target_ref')}\n"
        f"- Candidate ids: {self_evolve.get('candidate_ids')}\n"
        f"- Selected candidate: {self_evolve.get('selected_candidate_id')}\n"
        f"- Report path: {self_evolve.get('report_path')}\n"
        f"- Target selection path: {self_evolve.get('target_selection_path')}\n\n"
        "## Evaluation\n\n"
        f"- Verdict: {evaluation['verdict']}\n"
        f"- Design goal satisfied: {evaluation['design_goal_satisfied']}\n\n"
        "### Reasons\n\n"
        f"{reasons}\n\n"
        "## Evaluator Agent\n\n"
        f"- Path: {result['evaluator_agent'].get('path')}\n"
        f"- Exists: {result['evaluator_agent'].get('exists')}\n"
        f"- Name: {result['evaluator_agent'].get('name')}\n"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the AWorld CLI self-evolve trajectory test case through automatic "
            "target inference and write an auditable report."
        )
    )
    parser.add_argument("--trajectory-log", default=TRAJECTORY_LOG_DEFAULT)
    parser.add_argument("--task-id", default=TASK_ID_DEFAULT)
    parser.add_argument(
        "--out-dir",
        default=f".aworld/self_evolve/testcases/{TASK_ID_DEFAULT}",
    )
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--evaluator-agent-md", default=EVALUATOR_AGENT_DEFAULT)
    parser.add_argument("--apply", choices=("proposal", "auto_verified"), default="proposal")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when the design goal is not satisfied.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run_test_case(
        trajectory_log=args.trajectory_log,
        task_id=args.task_id,
        out_dir=args.out_dir,
        workspace_root=args.workspace_root,
        evaluator_agent_md=args.evaluator_agent_md,
        apply_policy=args.apply,
    )
    print(json.dumps(result["evaluation"], ensure_ascii=False, indent=2))
    print(f"JSON report: {result['artifacts']['json_report']}")
    print(f"Markdown report: {result['artifacts']['markdown_report']}")
    if args.strict and not result["evaluation"]["design_goal_satisfied"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
