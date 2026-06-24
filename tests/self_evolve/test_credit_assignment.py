from __future__ import annotations

from pathlib import Path

from aworld.self_evolve.credit_assignment import (
    LLMTargetDiagnosis,
    TargetInventory,
    TrajectoryCreditAssigner,
    build_default_target_inventory,
)
from aworld.self_evolve.trace_pack import build_trace_pack, trace_packs_from_trajectory_log


FIXTURE_LOG = Path(__file__).parent / "fixtures" / "credit_assignment_cases" / "sample_trajectory.log"


def _fixture_packs_by_task() -> dict[str, object]:
    return {
        pack.task_id: pack
        for pack in trace_packs_from_trajectory_log(FIXTURE_LOG, max_steps=8)
    }


def _write_skill(root: Path, name: str, *, description: str = "") -> Path:
    path = root / "aworld-skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n",
        encoding="utf-8",
    )
    return path


def test_default_target_inventory_contains_phase1_target_types_with_provenance(tmp_path) -> None:
    _write_skill(tmp_path, "demo-skill", description="Handles demo tasks.")
    inventory = build_default_target_inventory(workspace_root=tmp_path)

    assert isinstance(inventory, TargetInventory)
    assert {
        entry.target.target_type
        for entry in inventory.entries
    } >= {"skill", "prompt-section", "tool-description", "workspace-artifact"}
    assert "config" not in {entry.target.target_type for entry in inventory.entries}
    assert all(entry.provenance.target == entry.target for entry in inventory.entries)
    assert all(entry.provenance.write_origin for entry in inventory.entries)
    assert all(entry.provenance.trust_level for entry in inventory.entries)
    assert all(isinstance(entry.provenance.protected, bool) for entry in inventory.entries)
    assert inventory.find("skill", "demo-skill") is not None


def test_credit_assigner_selects_skill_prompt_tool_config_and_artifact_targets(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "agent-browser",
        description="Automates browser interactions and Chrome profile inspection.",
    )
    packs = _fixture_packs_by_task()
    assigner = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path)
    )

    expected = {
        "task_20260511112019": ("skill", "agent-browser", "skill"),
        "task_20260513161047": ("prompt-section", "result-validation-anchor-policy", "validation"),
        "1f9149cc482611f196356a5e5d182581": ("tool-description", "SKILL_tool.active_skill", "tool_activation"),
        "task_20260511104323": ("skill", "agent-browser", "skill"),
        "e676a3103f9411f197ecaead30e27f1a": ("workspace-artifact", "btc_monitor.sh", "artifact_failure"),
    }

    for task_id, (target_type, target_id, failure_category) in expected.items():
        report = assigner.assign(packs[task_id])

        assert report.selected_target is not None
        assert report.selected_target.target_type == target_type
        assert report.selected_target.target_id == target_id
        assert report.failure_category == failure_category
        assert report.confidence >= 0.8
        assert report.evidence_step_ids


def test_credit_assigner_declines_success_and_ambiguous_low_signal_runs(tmp_path) -> None:
    packs = _fixture_packs_by_task()
    assigner = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path)
    )

    for task_id in [
        "fa27d89c63e911f18b676a5e5d18257e",
        "task_20260609193335",
        "task_20260609230145",
        "task_20260510202321",
    ]:
        report = assigner.assign(packs[task_id])

        assert report.selected_target is None
        assert report.confidence < 0.8
        assert report.failure_category == "no_target"
        assert report.no_target_reason


def test_credit_assigner_accepts_current_trajectory_trace_pack(tmp_path) -> None:
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {
                    "input": {
                        "content": (
                            "Find the source anchors before writing the note."
                        )
                    },
                    "messages": [],
                },
                "action": {
                    "content": "I will collect source anchors.",
                    "tool_calls": [],
                    "is_agent_finished": False,
                },
                "reward": {"status": "ok"},
            },
            {
                "meta": {"step": 2, "agent_id": "agent", "pre_agent": "agent"},
                "state": {"messages": []},
                "action": {
                    "content": (
                        "Result validation mismatch: required anchors are missing "
                        "from source evidence."
                    ),
                    "tool_calls": [],
                    "is_agent_finished": True,
                },
                "reward": {"status": "failed"},
            },
        ],
        source_kind="current_trajectory",
        task_id="current-task",
    )
    assigner = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path)
    )

    report = assigner.assign(pack)

    assert report.selected_target is not None
    assert report.selected_target.target_type == "prompt-section"
    assert report.selected_target.target_id == "result-validation-anchor-policy"
    assert "current-task:step-2" in report.evidence_step_ids


def test_credit_assigner_reports_tool_failures_repeated_actions_and_task_status(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "agent-browser",
        description="Automates browser interactions and login trace inspection.",
    )
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {
                    "input": {
                        "content": "I am logged in, but browser keeps acting logged out."
                    },
                    "messages": [],
                },
                "action": {
                    "content": "I will inspect the browser session.",
                    "tool_calls": [{"function": {"name": "browser.open", "arguments": "{}"}}],
                    "is_agent_finished": False,
                },
                "reward": {"status": "failed", "tool_outputs": [{"status": "error"}]},
            },
            {
                "meta": {"step": 2, "agent_id": "agent", "pre_agent": "browser.open"},
                "state": {"messages": []},
                "action": {
                    "content": "Retry browser.open because login traces are missing.",
                    "tool_calls": [{"function": {"name": "browser.open", "arguments": "{}"}}],
                    "is_agent_finished": True,
                },
                "reward": {"status": "failed", "score": 0.0},
            },
        ],
        source_kind="current_trajectory",
        task_id="browser-task",
    )
    assigner = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path)
    )

    report = assigner.assign(pack)

    assert report.selected_target is not None
    assert report.selected_target.target_type == "skill"
    assert "tool_call_failure:browser.open" in report.signals
    assert "repeated_action:browser.open" in report.signals
    assert "task_failed" in report.signals
    assert "trajectory_score_failure" in report.signals


def test_credit_assigner_selects_installed_skill_by_generic_trajectory_evidence(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "web-navigator",
        description="Use for web navigation through remote browser sessions.",
    )
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {
                    "input": {
                        "content": "Use web-navigator to open the saved browser session."
                    }
                },
                "action": {
                    "content": "web-navigator opened the wrong saved browser session.",
                    "tool_calls": [],
                    "is_agent_finished": True,
                },
                "reward": {"status": "failed"},
            }
        ],
        source_kind="current_trajectory",
        task_id="web-navigation-task",
    )
    assigner = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path)
    )

    report = assigner.assign(pack)

    assert report.selected_target is not None
    assert report.selected_target.target_type == "skill"
    assert report.selected_target.target_id == "web-navigator"
    assert report.failure_category == "skill"
    assert "skill_alias_match:web-navigator" in report.signals


def test_credit_assigner_reports_llm_usage_and_generated_artifact_references(tmp_path) -> None:
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {
                    "input": {"content": "Run generated BTC price monitor script."},
                    "messages": [],
                    "llm_calls": [
                        {
                            "request": {"model": "gpt-test"},
                            "usage": {"total_tokens": 1234},
                            "cost": 0.02,
                        }
                    ],
                },
                "action": {
                    "content": "I will run btc_monitor.sh.",
                    "tool_calls": [{"function": {"name": "terminal.run", "arguments": "{}"}}],
                    "is_agent_finished": False,
                },
                "reward": {"status": "ok"},
            },
            {
                "meta": {"step": 2, "agent_id": "agent", "pre_agent": "terminal.run"},
                "state": {"messages": []},
                "action": {
                    "content": "API sources timed out in btc_monitor.sh.",
                    "tool_calls": [],
                    "is_agent_finished": True,
                },
                "reward": {
                    "status": "failed",
                    "generated_artifacts": [{"path": "btc_monitor.sh"}],
                },
            },
        ],
        source_kind="current_trajectory",
        task_id="artifact-task",
    )
    assigner = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path)
    )

    report = assigner.assign(pack)

    assert report.selected_target is not None
    assert report.selected_target.target_type == "workspace-artifact"
    assert "llm_usage_metadata" in report.signals
    assert "generated_artifact_reference:btc_monitor.sh" in report.signals


def test_credit_assigner_uses_llm_diagnosis_when_it_cites_trace_evidence(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "agent-browser",
        description="Automates browser interactions and handoff recovery.",
    )
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {"input": {"content": "The browser task failed after handoff."}},
                "action": {"content": "I will inspect the handoff details.", "tool_calls": []},
                "reward": {"status": "ok"},
            },
            {
                "meta": {"step": 2, "agent_id": "agent", "pre_agent": "agent"},
                "state": {"messages": []},
                "action": {"content": "The browser guidance did not cover this handoff."},
                "reward": {"status": "failed"},
            },
        ],
        source_kind="current_trajectory",
        task_id="llm-task",
    )

    def diagnose(trace_pack, inventory):
        assert trace_pack is pack
        assert isinstance(inventory, TargetInventory)
        return LLMTargetDiagnosis(
            target_type="skill",
            target_id="agent-browser",
            confidence=0.92,
            evidence_step_ids=("llm-task:step-2",),
            failure_category="browser_session",
            rationale="Step 2 explicitly points to missing browser guidance.",
        )

    assigner = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path),
        llm_diagnoser=diagnose,
    )

    report = assigner.assign(pack)

    assert report.selected_target is not None
    assert report.selected_target.target_type == "skill"
    assert report.selected_target.target_id == "agent-browser"
    assert report.confidence == 0.92
    assert report.evidence_step_ids == ("llm-task:step-2",)
    assert "llm_assisted_diagnosis" in report.signals
    assert report.diagnostics["llm_rationale"] == (
        "Step 2 explicitly points to missing browser guidance."
    )


def test_credit_assigner_declines_low_confidence_llm_diagnosis(tmp_path) -> None:
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {"input": {"content": "The result was generally confusing."}},
                "action": {"content": "I do not have a concrete target."},
                "reward": {"status": "failed"},
            },
        ],
        source_kind="current_trajectory",
        task_id="low-confidence-task",
    )

    def diagnose(trace_pack, inventory):
        return LLMTargetDiagnosis(
            target_type="prompt-section",
            target_id="result-validation-anchor-policy",
            confidence=0.42,
            evidence_step_ids=("low-confidence-task:step-1",),
            failure_category="validation",
            rationale="Possible prompt issue, but evidence is weak.",
        )

    assigner = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path),
        llm_diagnoser=diagnose,
    )

    report = assigner.assign(pack)

    assert report.selected_target is None
    assert report.failure_category == "no_target"
    assert report.confidence == 0.42
    assert report.evidence_step_ids == ("low-confidence-task:step-1",)
    assert report.no_target_reason == "llm diagnosis confidence is below policy"
    assert "llm_assisted_diagnosis" in report.signals
