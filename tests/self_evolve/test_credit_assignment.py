from __future__ import annotations

from pathlib import Path

import pytest

from aworld.self_evolve.credit_assignment import (
    LLMTargetDiagnosis,
    TargetInventory,
    TargetInventoryEntry,
    TargetSelectionDecision,
    TargetSelectionReport,
    TrajectoryCreditAssigner,
    build_target_selection_decision,
    build_default_target_inventory,
)
from aworld.self_evolve.provenance import TargetProvenance
from aworld.self_evolve.types import SelfEvolveTargetRef
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


def test_target_inventory_can_be_scoped_to_executable_target_types(tmp_path) -> None:
    _write_skill(tmp_path, "demo-skill", description="Handles demo tasks.")
    inventory = build_default_target_inventory(workspace_root=tmp_path)

    scoped = inventory.only_target_types({"skill"})

    assert {entry.target.target_type for entry in scoped.entries} == {"skill"}
    assert scoped.find("skill", "demo-skill") is not None
    assert scoped.find("prompt-section", "result-validation-anchor-policy") is None
    assert scoped.draft_skill_root == inventory.draft_skill_root


@pytest.mark.parametrize("link_kind", ["directory", "file"])
def test_default_target_inventory_excludes_symlinked_skill_paths(
    tmp_path,
    link_kind: str,
) -> None:
    real_skill = _write_skill(tmp_path, "real-capability")
    exposed_root = tmp_path / "skills" / "linked-capability"
    exposed_root.parent.mkdir(parents=True)
    if link_kind == "directory":
        exposed_root.symlink_to(real_skill.parent, target_is_directory=True)
    else:
        exposed_root.mkdir()
        (exposed_root / "SKILL.md").symlink_to(real_skill)

    inventory = build_default_target_inventory(workspace_root=tmp_path)

    assert inventory.find("skill", "linked-capability") is None


def test_duplicate_inventory_identity_is_unresolved() -> None:
    first_target = SelfEvolveTargetRef("skill", "capability", "/workspace/a/SKILL.md")
    second_target = SelfEvolveTargetRef("skill", "capability", "/workspace/b/SKILL.md")
    inventory = TargetInventory(
        entries=tuple(
            TargetInventoryEntry(
                target=target,
                provenance=TargetProvenance(
                    target=target,
                    source_kind="skill",
                    write_origin="installed_skill",
                    trust_level="local",
                    protected=False,
                    reason="inventory record",
                ),
            )
            for target in (first_target, second_target)
        )
    )
    report = TargetSelectionReport(
        selected_target=first_target,
        confidence=1.0,
        evidence_step_ids=(),
        failure_category="explicit_target",
    )

    decision = build_target_selection_decision(
        report,
        inventory=inventory,
        selection_origin="operator_explicit",
        workspace_root="/workspace",
    )

    assert decision.provenance is None
    assert decision.provenance_resolution.status == "unresolved"
    assert decision.provenance_resolution.reason == (
        "inventory contains duplicate target identity"
    )


def test_credit_assignment_decision_preserves_inventory_provenance(tmp_path) -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="generic-capability")
    provenance = TargetProvenance(
        target=target,
        source_kind="skill",
        write_origin="installed_skill",
        trust_level="local",
        protected=False,
        reason="generic local capability",
    )
    inventory = TargetInventory(
        entries=(TargetInventoryEntry(target=target, provenance=provenance),)
    )
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1},
                "state": {"input": {"content": "A generic workflow failed."}},
                "action": {"content": "The capability needs diagnosis."},
                "reward": {"status": "failed"},
            }
        ],
        source_kind="current_trajectory",
        task_id="inventory-target",
    )
    diagnosis = LLMTargetDiagnosis(
        target_type="skill",
        target_id="generic-capability",
        confidence=0.95,
        evidence_step_ids=("inventory-target:step-1",),
        failure_category="skill",
        rationale="generic capability owns the failed behavior",
    )

    decision = TrajectoryCreditAssigner(
        inventory=inventory,
        llm_diagnoser=lambda _pack, _inventory: diagnosis,
    ).assign_decision(pack)

    assert isinstance(decision, TargetSelectionDecision)
    assert decision.report.selected_target is not None
    assert decision.report.selected_target.target_id == "generic-capability"
    assert decision.provenance is provenance
    assert decision.report.provenance_status == "resolved"


def test_credit_assignment_decision_marks_new_draft_as_generated(tmp_path) -> None:
    target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="generated-capability",
        path=str(tmp_path / "drafts" / "generated-capability" / "SKILL.md"),
    )
    report = TargetSelectionReport(
        selected_target=target,
        confidence=0.85,
        evidence_step_ids=("generated-target:step-1",),
        failure_category="skill",
        signals=("new_skill_candidate",),
        capability_fingerprint="sha256:" + "a" * 64,
    )

    decision = build_target_selection_decision(
        report,
        inventory=TargetInventory(entries=()),
        selection_origin="inferred",
    )

    assert decision.report.selected_target is not None
    assert decision.report.selected_target.target_id == "generated-capability"
    assert decision.provenance is not None
    assert decision.provenance.trust_level == "generated"
    assert decision.provenance.write_origin == "target_inference"
    assert decision.report.provenance_status == "resolved"


@pytest.mark.parametrize(
    ("target_id", "evidence_ids", "fingerprint", "origin", "intent"),
    (
        ("self-evolve", ("step-1",), "sha256:" + "a" * 64, "inferred", "inferred_draft_creation"),
        ("valid-capability", (), "sha256:" + "a" * 64, "inferred", "inferred_draft_creation"),
        ("valid-capability", ("step-1",), None, "inferred", "inferred_draft_creation"),
        ("valid-capability", ("step-1",), "sha256:" + "a" * 64, "operator_explicit", "inferred_draft_creation"),
    ),
)
def test_draft_creation_intent_rejects_contradictory_or_incomplete_identity(
    target_id: str,
    evidence_ids: tuple[str, ...],
    fingerprint: str | None,
    origin: str,
    intent: str,
) -> None:
    decision = build_target_selection_decision(
        TargetSelectionReport(
            selected_target=SelfEvolveTargetRef("skill", target_id),
            confidence=0.9,
            evidence_step_ids=evidence_ids,
            failure_category="skill",
            capability_fingerprint=fingerprint,
        ),
        inventory=TargetInventory(entries=()),
        selection_origin=origin,
        target_intent=intent,
    )

    assert decision.provenance is None
    assert decision.provenance_resolution.status == "unresolved"


def test_explicit_selection_cannot_erase_inventory_protection(tmp_path) -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="protected-capability")
    provenance = TargetProvenance(
        target=target,
        source_kind="skill",
        write_origin="installed_skill",
        trust_level="protected",
        protected=True,
        reason="generic protected capability",
    )
    entry = TargetInventoryEntry(target=target, provenance=provenance)
    inventory = TargetInventory(entries=(entry,))
    report = TargetSelectionReport(
        selected_target=entry.target,
        confidence=1.0,
        evidence_step_ids=("evidence-1",),
        failure_category="explicit_target",
        signals=("explicit_target",),
    )

    decision = build_target_selection_decision(
        report,
        inventory=inventory,
        selection_origin="operator_explicit",
    )

    assert decision.provenance is entry.provenance
    assert decision.provenance.protected is True

    alias_report = TargetSelectionReport(
        selected_target=SelfEvolveTargetRef(
            target_type="skill",
            target_id=target.target_id,
            path=str(tmp_path / "alternate" / "SKILL.md"),
        ),
        confidence=1.0,
        evidence_step_ids=("evidence-1",),
        failure_category="explicit_target",
        signals=("explicit_target",),
    )
    alias_decision = build_target_selection_decision(
        alias_report,
        inventory=inventory,
        selection_origin="operator_explicit",
        workspace_root=tmp_path,
    )

    assert alias_decision.provenance is None
    assert alias_decision.provenance_resolution.status == "unresolved"


def test_credit_assigner_falls_back_when_signaled_target_is_not_executable(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "web-navigator",
        description="Use for web navigation through remote browser sessions.",
    )
    inventory = build_default_target_inventory(workspace_root=tmp_path).only_target_types(
        {"skill"}
    )
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {
                    "input": {
                        "content": "Use web-navigator and validate source anchors."
                    }
                },
                "action": {
                    "content": (
                        "web-navigator returned a result validation mismatch because "
                        "the required anchors are missing."
                    ),
                    "tool_calls": [],
                    "is_agent_finished": True,
                },
                "reward": {"status": "failed"},
            }
        ],
        source_kind="current_trajectory",
        task_id="generic-capability-fallback",
    )
    assigner = TrajectoryCreditAssigner(inventory=inventory)

    report = assigner.assign(pack)

    assert report.selected_target is not None
    assert report.selected_target.target_type == "skill"
    assert report.selected_target.target_id == "web-navigator"
    assert "unavailable_signaled_target:prompt-section" in report.signals
    assert report.diagnostics["unavailable_signaled_target"]["target_type"] == (
        "prompt-section"
    )


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


def test_generic_tool_action_token_does_not_select_unrelated_skill_alias(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path,
        "catalog_search",
        description="Search a private product catalog by structured filters.",
    )
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {"input": {"content": "Analyze the supplied paper."}},
                "action": {
                    "content": "The generic search attempt failed.",
                    "tool_calls": [
                        {"function": {"name": "CAST_SEARCH", "arguments": "{}"}}
                    ],
                    "is_agent_finished": False,
                },
                "reward": {"status": "failed"},
            }
        ],
        source_kind="current_trajectory",
        task_id="paper-analysis",
    )

    report = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path)
    ).assign(pack)

    assert "skill_alias_match:catalog_search" not in report.signals
    assert report.selected_target is None or report.selected_target.target_id != (
        "catalog_search"
    )


def test_generic_anchor_noun_does_not_select_validation_policy(tmp_path: Path) -> None:
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {"input": {"content": "Analyze a research paper."}},
                "action": {
                    "content": "I will inspect method section anchors in the PDF.",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "mcp",
                                "arguments": '{"command":"find section anchors"}',
                            }
                        }
                    ],
                    "is_agent_finished": True,
                },
                "reward": {"status": "ok"},
            }
        ],
        source_kind="current_trajectory",
        task_id="paper-analysis",
    )

    report = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path)
    ).assign(pack)

    assert report.selected_target is None
    assert "result_validation_mismatch" not in report.signals


def test_credit_assigner_creates_generic_draft_instead_of_matching_description_tokens(
    tmp_path,
) -> None:
    _write_skill(
        tmp_path,
        "video_script_writting",
        description=(
            "Standard operating procedure for AI video production using diffusion "
            "and script templates."
        ),
    )
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {
                    "input": {
                        "content": (
                            "Collect AI tweets from X via CDP, then write a daily "
                            "digest. The feed may include posts about diffusion models."
                        )
                    },
                    "messages": [],
                },
                "action": {
                    "content": "I will create a scraper script for the X feed.",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "mcp",
                                "arguments": "{\"command\":\"python scrape_x.py\"}",
                            }
                        }
                    ],
                    "is_agent_finished": False,
                },
                "reward": {"status": "failed"},
            }
        ],
        source_kind="current_trajectory",
        task_id="x-digest-task",
    )
    assigner = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path)
    )

    report = assigner.assign(pack)

    assert report.selected_target is not None
    assert report.selected_target.target_type == "skill"
    assert report.selected_target.path is None
    assert report.target_intent == "inferred_draft_creation"
    assert report.capability_fingerprint is not None
    assert "skill_alias_match:video_script_writting" not in report.signals


def test_credit_assigner_compiles_path_free_draft_for_structured_http_gap(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "agent-browser",
        description="Fast browser automation CLI for AI agents.",
    )
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {
                    "input": {
                        "content": (
                            "Extract verified records from "
                            "https://api.example.test/resources/demo."
                        )
                    },
                    "messages": [],
                },
                "action": {
                    "content": "I will open the rendered page with agent-browser.",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "mcp",
                                "arguments": "{\"command\":\"agent-browser open https://example.test\"}",
                            }
                        }
                    ],
                    "is_agent_finished": False,
                },
                "reward": {"status": "ok"},
            },
            {
                "meta": {"step": 2, "agent_id": "agent", "pre_agent": "mcp"},
                "state": {
                    "input": {
                        "content": (
                            "The remote resource returned metadata but no structured records."
                        )
                    },
                    "messages": [],
                },
                "action": {
                    "content": "The final answer needs stronger source evidence grounding.",
                    "tool_calls": [],
                    "is_agent_finished": True,
                },
                "reward": {"status": "failed"},
            },
        ],
        source_kind="current_trajectory",
        task_id="http-resource-task",
    )
    assigner = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path)
    )

    report = assigner.assign(pack)

    assert report.selected_target is not None
    assert report.selected_target.target_type == "skill"
    assert report.selected_target.target_id.startswith("http-recovery-")
    assert report.selected_target.path is None
    assert report.failure_category == "skill"
    assert report.confidence == 0.85
    assert "new_skill_candidate" in report.signals
    assert "skill_alias_match:agent-browser" not in report.signals
    assert report.target_intent == "inferred_draft_creation"
    assert report.capability_fingerprint.startswith("sha256:")


def test_credit_assigner_reuses_skill_with_same_capability_fingerprint_identity(
    tmp_path,
) -> None:
    _write_skill(
        tmp_path,
        "agent-browser",
        description="Fast browser automation CLI for AI agents.",
    )
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {
                    "input": {
                        "content": (
                            "Extract verified records from "
                            "https://api.example.test/resources/demo."
                        )
                    },
                    "messages": [],
                },
                "action": {
                    "content": "I will open the rendered page with agent-browser.",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "mcp",
                                "arguments": "{\"command\":\"agent-browser open https://example.test\"}",
                            }
                        }
                    ],
                    "is_agent_finished": False,
                },
                "reward": {"status": "ok"},
            },
            {
                "meta": {"step": 2, "agent_id": "agent", "pre_agent": "mcp"},
                "state": {
                    "input": {
                        "content": (
                            "The remote resource returned metadata but no structured records."
                        )
                    },
                    "messages": [],
                },
                "action": {
                    "content": "The final answer needs stronger source evidence grounding.",
                    "tool_calls": [],
                    "is_agent_finished": True,
                },
                "reward": {"status": "failed"},
            },
        ],
        source_kind="current_trajectory",
        task_id="http-resource-task",
    )
    initial_report = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path)
    ).assign(pack)
    assert initial_report.selected_target is not None
    inferred_id = initial_report.selected_target.target_id
    _write_skill(
        tmp_path,
        inferred_id,
        description="Verified capability for this structured operation fingerprint.",
    )
    assigner = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path)
    )

    report = assigner.assign(pack)

    assert report.selected_target is not None
    assert report.selected_target.target_type == "skill"
    assert report.selected_target.target_id == inferred_id
    assert report.selected_target.path == str(
        tmp_path / "aworld-skills" / inferred_id / "SKILL.md"
    )
    assert report.confidence >= 0.9
    assert "capability_fingerprint_inventory_match" in report.signals
    assert "new_skill_candidate" not in report.signals
    assert "skill_alias_match:agent-browser" not in report.signals
    assert report.target_intent == "existing_target_mutation"


def test_credit_assigner_ignores_skill_catalog_mentions_in_system_messages(tmp_path) -> None:
    _write_skill(
        tmp_path,
        "media_comprehension",
        description="Use for image, audio, and video media analysis.",
    )
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {
                    "input": {
                        "content": (
                            "Extract verified records from "
                            "https://api.example.test/resources/demo."
                        )
                    },
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Available skills include media_comprehension. "
                                "Use for image, audio, and video media analysis."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                "Extract verified records from "
                                "https://api.example.test/resources/demo."
                            ),
                        },
                    ],
                },
                "action": {
                    "content": "I will fetch the page HTML with curl.",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "mcp",
                                "arguments": "{\"command\":\"curl -s https://example.test\"}",
                            }
                        }
                    ],
                    "is_agent_finished": False,
                },
                "reward": {"status": "ok"},
            },
            {
                "meta": {"step": 2, "agent_id": "agent", "pre_agent": "mcp"},
                "state": {
                    "input": {
                        "content": (
                            '<meta property="og:audio" '
                            'content="https://media.xyzcdn.net/demo/audio.m4a"/> '
                            '<meta property="twitter:image" '
                            'content="https://image.xyzcdn.net/demo.png"/> '
                            "/Users/me/Documents/workspace/aworld/episode.html"
                        )
                    },
                    "messages": [],
                },
                "action": {
                    "content": "The page has metadata but no structured records.",
                    "tool_calls": [],
                    "is_agent_finished": True,
                },
                "reward": {"status": "failed"},
            }
        ],
        source_kind="current_trajectory",
        task_id="http-resource-task",
    )
    assigner = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(workspace_root=tmp_path)
    )

    report = assigner.assign(pack)

    assert report.selected_target is not None
    assert report.selected_target.target_id.startswith("http-recovery-")
    assert report.failure_category == "skill"
    assert "new_skill_candidate" in report.signals
    assert "skill_alias_match:media_comprehension" not in report.signals


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


def test_credit_assigner_accepts_explicit_llm_new_skill_intent_without_a_path(
    tmp_path: Path,
) -> None:
    pack = build_trace_pack(
        [
            {
                "meta": {"step": 1},
                "state": {"input": {"content": "Run the remote operation."}},
                "action": {
                    "content": "The remote operation needs reusable recovery guidance.",
                    "tool_calls": [
                        {"function": {"name": "remote.execute", "arguments": "{}"}}
                    ],
                },
                "reward": {"status": "failed", "code": "remote_unavailable"},
            }
        ],
        source_kind="current_trajectory",
        task_id="new-capability-task",
    )
    diagnosis = LLMTargetDiagnosis(
        target_type="skill",
        target_id="remote-operation-recovery",
        confidence=0.93,
        evidence_step_ids=("new-capability-task:step-1",),
        failure_category="skill",
        rationale="The trace demonstrates a reusable remote-operation recovery gap.",
        selection_kind="new_skill",
    )

    decision = TrajectoryCreditAssigner(
        inventory=build_default_target_inventory(tmp_path),
        llm_diagnoser=lambda _pack, _inventory: diagnosis,
    ).assign_decision(pack)

    assert decision.report.selected_target == SelfEvolveTargetRef(
        "skill", "remote-operation-recovery"
    )
    assert decision.target_intent == "inferred_draft_creation"
    assert decision.report.capability_fingerprint.startswith("sha256:")
    assert decision.provenance is not None
    assert decision.provenance.trust_level == "generated"
