from __future__ import annotations

import importlib.util
import json
from pathlib import Path


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "credit_assignment_cases"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_credit_assignment_spike_fixtures_are_sanitized_and_labeled() -> None:
    cases = _load_jsonl(FIXTURE_DIR / "cases.jsonl")
    labels = _load_json(FIXTURE_DIR / "labels.json")
    recipe = _load_json(FIXTURE_DIR / "dataset_recipe.json")

    assert len(cases) >= 3
    assert recipe["source"]["kind"] == "aworld_trajectory_log"
    assert recipe["source"]["path"]
    assert recipe["source"]["initial_seed_path"] == "~/Documents/logs/trajectory.log"
    assert recipe["source"]["sha256"]
    assert recipe["split_seed"]
    assert set(recipe["splits"]) == {"train", "validation", "held_out"}

    label_by_case = {item["case_id"]: item for item in labels["labels"]}
    assert {case["case_id"] for case in cases} == set(label_by_case)

    for case in cases:
        serialized = json.dumps(case, ensure_ascii=False)
        assert "/Users/" not in serialized
        assert "wuman" not in serialized.lower()
        assert "manwu" not in serialized.lower()
        assert "wuman1.top" not in serialized
        assert case["source_task_id"]
        assert case["trajectory_summary"]["num_steps"] >= 1
        assert case["trajectory_summary"]["evidence_steps"]
        assert case["expected_observable_outcome"]

        label = label_by_case[case["case_id"]]
        assert label["expected_target"]["type"] in {
            "skill",
            "prompt-section",
            "tool-description",
            "config",
            "workspace-artifact",
            "no_target",
        }
        assert label["rationale"]
        assert label["evidence_step_ids"]


def test_credit_assignment_spike_fixtures_cover_required_target_types() -> None:
    labels = _load_json(FIXTURE_DIR / "labels.json")
    report = _load_json(FIXTURE_DIR / "spike_report.json")

    required_target_types = {
        "skill",
        "prompt-section",
        "tool-description",
        "config",
        "workspace-artifact",
        "no_target",
    }
    present_target_types = {
        label["expected_target"]["type"]
        for label in labels["labels"]
    }
    categories = {
        case["category"]
        for case in _load_jsonl(FIXTURE_DIR / "cases.jsonl")
    }

    assert required_target_types <= present_target_types
    assert {"success", "ambiguous"} <= categories
    assert report["coverage"]["missing_target_types"] == []


def test_credit_assignment_spike_report_records_go_when_target_selection_gate_is_met() -> None:
    report = _load_json(FIXTURE_DIR / "spike_report.json")

    assert report["decision"] == "go"
    assert report["thresholds"] == {
        "target_selection_precision": 0.8,
        "target_selection_recall": 0.7,
        "no_target_precision": 0.8,
    }
    for metric_name, threshold in report["thresholds"].items():
        assert report["metrics"][metric_name] >= threshold
    assert report["false_positives"] == []
    assert report["false_negatives"] == []
    assert report["predictions"]


def test_credit_assignment_spike_script_can_regenerate_fixtures(tmp_path: Path) -> None:
    script_path = Path("scripts/self_evolve_credit_assignment_spike.py")
    spec = importlib.util.spec_from_file_location("self_evolve_credit_assignment_spike", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source_log = FIXTURE_DIR / "sample_trajectory.log"
    module.generate_spike_artifacts(source_log=source_log, output_dir=tmp_path)

    assert (tmp_path / "cases.jsonl").exists()
    assert (tmp_path / "labels.json").exists()
    assert (tmp_path / "dataset_recipe.json").exists()
    assert (tmp_path / "spike_report.json").exists()

    generated_report = _load_json(tmp_path / "spike_report.json")
    assert generated_report["decision"] == "go"
