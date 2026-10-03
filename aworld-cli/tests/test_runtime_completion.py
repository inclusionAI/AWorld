from pathlib import Path
import json
import sys

import pytest

from aworld.core.context.base import Context
from aworld.core.context.compiler import CompletionMode, CompletionStatus
from aworld_cli.core.runtime_completion import (
    build_runtime_completion_contract,
    configure_goal_completion,
    configure_runtime_completion,
    resolve_completion_max_repairs,
    resolve_completion_mode,
)


def test_completion_checks_are_off_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_COMPLETION_MODE", raising=False)
    assert resolve_completion_mode() is CompletionMode.OFF


def test_contract_resolves_explicit_relative_paths_against_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AWORLD_COMPLETION_MAX_REPAIRS", raising=False)
    contract = build_runtime_completion_contract(
        "ignored model request",
        workspace_path=tmp_path,
        explicit_paths=("./answer.json",),
    )
    assert contract is not None
    assert tuple(item.path for item in contract.required_artifacts) == (
        str((tmp_path / "answer.json").resolve()),
    )
    assert contract.max_repairs is None


def test_completion_max_repairs_env_applies_to_runtime_contracts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AWORLD_COMPLETION_MODE", "enforce")
    monkeypatch.setenv("AWORLD_COMPLETION_MAX_REPAIRS", "3")
    artifact_contract = build_runtime_completion_contract(
        "",
        workspace_path=tmp_path,
        explicit_paths=("./answer.json",),
    )
    assert artifact_contract is not None
    assert artifact_contract.max_repairs == 3

    fallback_context = Context(task_id="completion-max-repairs-fallback")
    monkeypatch.setenv("AWORLD_REQUIRED_ARTIFACTS_JSON", "[]")
    fallback_contract = configure_runtime_completion(
        fallback_context,
        request="Do the requested work",
        workspace_path=tmp_path,
    )
    assert fallback_contract is not None
    assert fallback_contract.max_repairs == 3

    goal_context = Context(task_id="completion-max-repairs-goal")
    goal_contract = configure_goal_completion(
        goal_context,
        verification_commands=("true",),
        workspace_path=tmp_path,
    )
    assert goal_contract is not None
    assert goal_contract.max_repairs == 3


def test_completion_max_repairs_unset_preserves_unbounded_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_COMPLETION_MAX_REPAIRS", raising=False)
    assert resolve_completion_max_repairs() is None


@pytest.mark.parametrize("value", ("-1", "+1", "1.5", "three"))
def test_completion_max_repairs_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("AWORLD_COMPLETION_MAX_REPAIRS", value)
    with pytest.raises(
        ValueError,
        match="AWORLD_COMPLETION_MAX_REPAIRS must be a non-negative integer",
    ):
        resolve_completion_max_repairs()


def test_completion_max_repairs_accepts_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_COMPLETION_MAX_REPAIRS", "0")
    assert resolve_completion_max_repairs() == 0


def test_non_off_mode_without_explicit_contract_remains_model_owned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AWORLD_COMPLETION_MODE", "enforce")
    context = Context(task_id="no-explicit-contract")
    contract = configure_runtime_completion(
        context,
        request="Write answer.json.",
        workspace_path=tmp_path,
    )
    assert contract is None
    assert context.completion_contract is None
    assert context.context_info["runtime_completion_contract"] == {
        "mode": "off",
        "requested_mode": "enforce",
        "source": "no_explicit_contract",
        "required_artifacts": [],
        "max_repairs": None,
    }


def test_legacy_inference_env_does_not_create_a_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AWORLD_COMPLETION_MODE", "enforce")
    monkeypatch.setenv("AWORLD_INFER_REQUIRED_ARTIFACTS", "true")
    context = Context(task_id="no-inferred-contract")
    assert (
        configure_runtime_completion(
            context,
            request="Save it to ./answer.json",
            workspace_path=tmp_path,
        )
        is None
    )
    assert context.completion_contract is None


def test_explicit_artifact_configuration_does_not_require_inference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AWORLD_COMPLETION_MODE", "observe")
    monkeypatch.setenv("AWORLD_REQUIRED_ARTIFACTS_JSON", '["./declared.bin"]')
    context = Context(task_id="completion-explicit")
    contract = configure_runtime_completion(
        context,
        request="Do the requested work",
        workspace_path=tmp_path,
    )
    assert contract is not None
    assert context.completion_mode is CompletionMode.OBSERVE
    assert contract.required_artifacts[0].path == str(
        (tmp_path / "declared.bin").resolve()
    )


@pytest.mark.asyncio
async def test_explicit_structured_artifact_can_enforce_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AWORLD_COMPLETION_MODE", "enforce")
    monkeypatch.setenv("AWORLD_REQUIRED_ARTIFACTS_JSON", '["./declared.bin"]')
    context = Context(task_id="completion-explicit-enforce")
    contract = configure_runtime_completion(
        context,
        request="Please discuss whether to save another.bin.",
        workspace_path=tmp_path,
    )
    assert contract is not None
    assert [item.path for item in contract.required_artifacts] == [
        str((tmp_path / "declared.bin").resolve())
    ]
    context.record_completion_final_evidence("agent_final_response")
    await context.resolve_completion_evidence()
    assessment = context.assess_completion_contract(agent_claimed_finished=True)
    assert assessment is not None
    assert assessment.status is CompletionStatus.REPAIR_REQUIRED
    assert context.context_info["runtime_completion_contract"]["source"] == (
        "explicit_structured"
    )


@pytest.mark.asyncio
async def test_explicit_validation_command_records_real_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AWORLD_COMPLETION_MODE", "enforce")
    monkeypatch.setenv(
        "AWORLD_VALIDATION_COMMANDS_JSON",
        json.dumps(
            [
                {
                    "command_id": "caller-check",
                    "argv": [sys.executable, "-c", "raise SystemExit(7)"],
                }
            ]
        ),
    )
    context = Context(task_id="explicit-command")
    contract = configure_runtime_completion(
        context,
        request="Do the work.",
        workspace_path=tmp_path,
    )
    assert contract is not None
    context.record_completion_final_evidence("agent_final_response")
    await context.resolve_completion_evidence()
    evidence = {
        item.command_id: item for item in context._completion_self_checks
    }
    assert evidence["caller-check"].exit_code == 7


def test_existing_caller_contract_identity_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AWORLD_COMPLETION_MODE", "enforce")
    context = Context(task_id="caller-contract")
    contract = build_runtime_completion_contract(
        "",
        workspace_path=tmp_path,
        explicit_paths=("caller.txt",),
    )
    assert contract is not None
    context.configure_completion_contract(contract, mode=CompletionMode.OBSERVE)
    configured = configure_runtime_completion(
        context,
        request="Write unrelated.txt.",
        workspace_path=tmp_path,
    )
    assert configured is contract
    assert context.completion_contract is contract
    assert context.completion_mode is CompletionMode.OBSERVE
