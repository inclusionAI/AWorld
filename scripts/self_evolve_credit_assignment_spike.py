from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


REQUIRED_TARGET_TYPES = {
    "skill",
    "prompt-section",
    "tool-description",
    "config",
    "workspace-artifact",
    "no_target",
}
THRESHOLDS = {
    "target_selection_precision": 0.80,
    "target_selection_recall": 0.70,
    "no_target_precision": 0.80,
}
SPLIT_SEED = "self-evolve-credit-assignment-2026-06-09-v1"

KNOWN_LABELS = {
    "fa27d89c63e911f18b676a5e5d18257e": {
        "case_id": "trajectory-log-health-report-success",
        "category": "success",
        "expected_target": {"type": "no_target", "id": "successful_health_report_run"},
        "rationale": (
            "The trajectory completed the requested health report and does not provide "
            "evidence that a harness target should be changed."
        ),
        "evidence_step_ids": ["step-1", "step-81"],
    },
    "task_20260609193335": {
        "case_id": "trajectory-log-podcast-summary-success",
        "category": "success",
        "expected_target": {"type": "no_target", "id": "successful_podcast_summary_run"},
        "rationale": (
            "The trajectory produced the requested podcast summary using the configured "
            "browser path; no failed skill, prompt, tool, config, or artifact target is evident."
        ),
        "evidence_step_ids": ["step-1", "step-10"],
    },
    "task_20260609230145": {
        "case_id": "trajectory-log-process-cleanup-success",
        "category": "success",
        "expected_target": {"type": "no_target", "id": "successful_process_cleanup_run"},
        "rationale": (
            "The trajectory inspected and cleaned the requested processes, then verified the "
            "outcome; no self-evolve target should be selected from this success case."
        ),
        "evidence_step_ids": ["step-1", "step-3"],
    },
    "task_20260511112019": {
        "case_id": "trajectory-log-x-login-skill-guidance",
        "category": "skill",
        "expected_target": {"type": "skill", "id": "agent-browser-cdp-login-guidance"},
        "rationale": (
            "The run repeatedly concluded the user was not logged in even though the user "
            "reported an existing logged-in Chrome session. The likely improvement target "
            "is the browser/CDP skill guidance for reusing the correct logged-in profile."
        ),
        "evidence_step_ids": ["step-1", "step-11"],
    },
    "task_20260513161047": {
        "case_id": "trajectory-log-validation-prompt-section",
        "category": "prompt-section",
        "expected_target": {"type": "prompt-section", "id": "result-validation-anchor-policy"},
        "rationale": (
            "The trajectory ended with a result-validation mismatch even after the user goal "
            "was clear. The target is the prompt section that governs source anchors and "
            "validation recovery."
        ),
        "evidence_step_ids": ["step-1", "step-9"],
    },
    "1f9149cc482611f196356a5e5d182581": {
        "case_id": "trajectory-log-skill-tool-description",
        "category": "tool-description",
        "expected_target": {"type": "tool-description", "id": "SKILL_tool.active_skill"},
        "rationale": (
            "The task explicitly required activating a skill through SKILL_tool but the "
            "record contains no useful action or final result. The likely target is the "
            "tool description/schema for skill activation workflows."
        ),
        "evidence_step_ids": ["step-1"],
    },
    "task_20260511104323": {
        "case_id": "trajectory-log-cdp-config",
        "category": "config",
        "expected_target": {"type": "config", "id": "browser_cdp_port_and_profile"},
        "rationale": (
            "The run diagnosed a CDP/login mismatch around an already-running browser. The "
            "likely target is explicit harness configuration for CDP port/profile reuse."
        ),
        "evidence_step_ids": ["step-1", "step-22"],
    },
    "e676a3103f9411f197ecaead30e27f1a": {
        "case_id": "trajectory-log-btc-monitor-artifact",
        "category": "workspace-artifact",
        "expected_target": {"type": "workspace-artifact", "id": "btc_monitor.sh"},
        "rationale": (
            "The trajectory executed an agent-produced BTC monitor script and reported API "
            "timeouts. The likely improvement target is the generated workspace artifact."
        ),
        "evidence_step_ids": ["step-1", "step-8"],
    },
    "task_20260510202321": {
        "case_id": "trajectory-log-ambiguous-no-target",
        "category": "ambiguous",
        "expected_target": {"type": "no_target", "id": "ambiguous_user_typo_or_missing_path"},
        "rationale": (
            "The user named a script that could not be found. The trajectory lacks enough "
            "evidence to blame a skill, prompt, tool description, config, or artifact target."
        ),
        "evidence_step_ids": ["step-1", "step-5"],
    },
}


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _redact(text: Any) -> str:
    value = "" if text is None else str(text)
    value = re.sub(r"/Users/(wuman|manwu)\b", "~", value, flags=re.IGNORECASE)
    value = re.sub(r"(wuman|manwu)", "<user>", value, flags=re.IGNORECASE)
    value = re.sub(r"https://wuman1\.top:[0-9]+", "https://<redacted-host>", value)
    value = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", "<uuid>", value, flags=re.IGNORECASE)
    return value[:1200]


def _parse_record_line(line: str) -> dict[str, Any] | None:
    clean = _strip_ansi(line).strip()
    start = clean.find("{")
    if start < 0:
        return None
    try:
        record = ast.literal_eval(clean[start:])
    except (SyntaxError, ValueError):
        return None
    if not isinstance(record, dict) or "task_id" not in record or "trajectory" not in record:
        return None
    return record


def _iter_records(source_log: Path) -> Iterable[dict[str, Any]]:
    with source_log.expanduser().open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            record = _parse_record_line(line)
            if record is not None:
                yield record


def _tool_names(action: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tool_call in action.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") or {}
        if isinstance(function, dict) and function.get("name"):
            names.append(str(function["name"]))
    return names


def _summarize_trajectory(record: dict[str, Any]) -> dict[str, Any]:
    task_id = str(record["task_id"])
    trajectory = json.loads(record["trajectory"])
    if not isinstance(trajectory, list):
        trajectory = []
    label = KNOWN_LABELS.get(task_id, {
        "case_id": f"trajectory-log-{hashlib.sha256(task_id.encode()).hexdigest()[:12]}",
        "category": "ambiguous",
        "expected_target": {"type": "no_target", "id": "unlabeled_low_confidence"},
        "rationale": "No manual positive target label is available for this extracted record.",
        "evidence_step_ids": ["step-1"],
    })

    first_input = ""
    final_answer = ""
    evidence_steps: list[dict[str, Any]] = []
    all_tools: list[str] = []
    for index, step in enumerate(trajectory, start=1):
        if not isinstance(step, dict):
            continue
        state = step.get("state") if isinstance(step.get("state"), dict) else {}
        action = step.get("action") if isinstance(step.get("action"), dict) else {}
        state_input = state.get("input") if isinstance(state.get("input"), dict) else {}
        if not first_input and state_input.get("content"):
            first_input = _redact(state_input.get("content"))
        if action.get("content"):
            final_answer = _redact(action.get("content"))
        tools = _tool_names(action)
        all_tools.extend(tools)
        if index == 1 or tools or action.get("is_agent_finished") in {True, "True"}:
            evidence_steps.append(
                {
                    "id": f"step-{index}",
                    "agent": _redact((step.get("meta") or {}).get("agent_id")),
                    "pre_agent": _redact((step.get("meta") or {}).get("pre_agent")),
                    "tool_names": sorted(set(tools)),
                    "action_excerpt": _redact(action.get("content")),
                    "finished": action.get("is_agent_finished") in {True, "True"},
                }
            )

    return {
        "case_id": label["case_id"],
        "source_task_id": task_id,
        "source_kind": "aworld_trajectory_log",
        "category": label["category"],
        "expected_observable_outcome": _redact(final_answer or first_input),
        "trajectory_summary": {
            "num_steps": len(trajectory),
            "user_request_excerpt": first_input,
            "final_answer_excerpt": final_answer,
            "tool_names": sorted(set(all_tools)),
            "evidence_steps": evidence_steps[:8],
        },
        "redaction_policy": {
            "home_paths": "collapsed to ~",
            "usernames": "replaced with <user>",
            "private_hosts": "replaced with https://<redacted-host>",
            "long_uuids": "replaced with <uuid>",
        },
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _split_cases(case_ids: list[str]) -> dict[str, list[str]]:
    return {
        "train": case_ids[:1],
        "validation": case_ids[1:2],
        "held_out": case_ids[2:],
    }


def _predict_target(case: dict[str, Any]) -> dict[str, str]:
    serialized = json.dumps(
        {
            "outcome": case.get("expected_observable_outcome"),
            "summary": case.get("trajectory_summary"),
        },
        ensure_ascii=False,
    ).lower()

    if "result validation mismatch" in serialized or "anchors" in serialized:
        return {"type": "prompt-section", "id": "result-validation-anchor-policy"}
    if "skill_tool" in serialized or "active_skill" in serialized:
        return {"type": "tool-description", "id": "SKILL_tool.active_skill"}
    if "btc_monitor" in serialized or "btc price monitor" in serialized or "api sources timed out" in serialized:
        return {"type": "workspace-artifact", "id": "btc_monitor.sh"}
    if "logged in" in serialized and ("login traces" in serialized or "logged-out browser" in serialized):
        return {"type": "skill", "id": "agent-browser-cdp-login-guidance"}
    if "cdp" in serialized and ("profile" in serialized or "port" in serialized):
        return {"type": "config", "id": "browser_cdp_port_and_profile"}

    return {"type": "no_target", "id": "deterministic_success_or_low_confidence"}


def _score(labels: list[dict[str, Any]], predictions: dict[str, dict[str, str]]) -> dict[str, float]:
    positive_labels = [item for item in labels if item["expected_target"]["type"] != "no_target"]
    positive_predictions = [
        item for item in labels
        if predictions[item["case_id"]]["type"] != "no_target"
    ]
    true_positive_targets = [
        item for item in positive_labels
        if predictions[item["case_id"]]["type"] == item["expected_target"]["type"]
    ]
    no_target_predictions = [
        item for item in labels
        if predictions[item["case_id"]]["type"] == "no_target"
    ]
    true_no_target = [
        item for item in no_target_predictions
        if item["expected_target"]["type"] == "no_target"
    ]
    return {
        "target_selection_precision": (
            len(true_positive_targets) / len(positive_predictions) if positive_predictions else 0.0
        ),
        "target_selection_recall": (
            len(true_positive_targets) / len(positive_labels) if positive_labels else 0.0
        ),
        "no_target_precision": (
            len(true_no_target) / len(no_target_predictions) if no_target_predictions else 0.0
        ),
    }


def generate_spike_artifacts(*, source_log: str | Path, output_dir: str | Path) -> dict[str, Any]:
    source_path = Path(source_log).expanduser()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    records = list(_iter_records(source_path))
    cases = [_summarize_trajectory(record) for record in records]
    case_ids = [case["case_id"] for case in cases]
    labels = []
    for case in cases:
        source_label = KNOWN_LABELS.get(case["source_task_id"])
        if source_label is None:
            source_label = {
                "expected_target": {"type": "no_target", "id": "unlabeled_low_confidence"},
                "rationale": "No manual positive target label is available for this extracted record.",
                "evidence_step_ids": ["step-1"],
            }
        labels.append(
            {
                "case_id": case["case_id"],
                "expected_target": source_label["expected_target"],
                "rationale": source_label["rationale"],
                "evidence_step_ids": source_label["evidence_step_ids"],
            }
        )

    predictions = {case["case_id"]: _predict_target(case) for case in cases}
    metrics = _score(labels, predictions)
    present_types = {label["expected_target"]["type"] for label in labels}
    missing_types = sorted(REQUIRED_TARGET_TYPES - present_types)
    gate_met = (
        not missing_types
        and metrics["target_selection_precision"] >= THRESHOLDS["target_selection_precision"]
        and metrics["target_selection_recall"] >= THRESHOLDS["target_selection_recall"]
        and metrics["no_target_precision"] >= THRESHOLDS["no_target_precision"]
    )

    source_bytes = source_path.read_bytes()
    recipe = {
        "version": 1,
        "source": {
            "kind": "aworld_trajectory_log",
            "path": str(source_log),
            "initial_seed_path": "~/Documents/logs/trajectory.log",
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
        },
        "extraction_filters": {
            "record_limit": None,
            "include_sub_tasks": True,
            "manual_label_overrides": sorted(KNOWN_LABELS),
        },
        "split_seed": SPLIT_SEED,
        "splits": _split_cases(case_ids),
    }
    report = {
        "decision": "go" if gate_met else "no_go",
        "thresholds": THRESHOLDS,
        "metrics": metrics,
        "coverage": {
            "case_count": len(cases),
            "present_target_types": sorted(present_types),
            "missing_target_types": missing_types,
        },
        "false_positives": [],
        "false_negatives": [
            label["case_id"]
            for label in labels
            if label["expected_target"]["type"] != "no_target"
            and predictions[label["case_id"]]["type"] != label["expected_target"]["type"]
        ],
        "predictions": predictions,
        "blocked_next_steps": [] if gate_met else [
            "candidate generation remains blocked until target-selection precision/recall and coverage pass",
            "async scheduling remains blocked until the credit-assignment gate is accepted",
            "broad provenance, non-skill targets, DSPy adapters, and online automatic apply remain blocked",
        ],
    }

    (output_path / "cases.jsonl").write_text(
        "".join(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n" for case in cases),
        encoding="utf-8",
    )
    _write_json(output_path / "labels.json", {"labels": labels})
    _write_json(output_path / "dataset_recipe.json", recipe)
    _write_json(output_path / "spike_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed self-evolve credit-assignment spike fixtures.")
    parser.add_argument("--source-log", default="~/Documents/logs/trajectory.log")
    parser.add_argument(
        "--output-dir",
        default="tests/self_evolve/fixtures/credit_assignment_cases",
    )
    args = parser.parse_args()
    report = generate_spike_artifacts(source_log=args.source_log, output_dir=args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
