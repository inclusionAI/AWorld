import json
import sys
from pathlib import Path

import pytest

from aworld_cli.core.runtime_completion import (
    build_runtime_completion_contract,
    configure_goal_completion,
    configure_runtime_completion,
    infer_public_deliverable_hints,
    infer_public_executable_hints,
    resolve_completion_max_repairs,
    resolve_completion_mode,
)

from aworld.core.context.base import Context
from aworld.core.context.compiler import CompletionMode, CompletionStatus


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


def test_explicit_public_output_filename_becomes_advisory_deliverable(
    tmp_path: Path,
) -> None:
    hints = infer_public_deliverable_hints(
        "The input file sequences.fasta contains templates.\n"
        "The output fasta file should be titled primers.fasta.",
        workspace_path=tmp_path,
    )
    assert [item.path for item in hints] == [
        str((tmp_path / "primers.fasta").resolve())
    ]
    assert hints[0].authority == "public_task_advisory"


@pytest.mark.parametrize(
    ("request_text", "expected_display_path"),
    (
        (
            "Create a python file /app/filter.py that removes scripts.",
            "/app/filter.py",
        ),
        ("Write a file eval.scm that evaluates the language.", "eval.scm"),
        ("Write a c program image.c that produces the requested image.", "image.c"),
        ("Write me data.comp that's compressed for the supplied decoder.", "data.comp"),
        (
            "Implement a MIPS interpreter complete with handling system calls "
            "called vm.js so I can run it.",
            "vm.js",
        ),
        (
            "Call your program /app/gpt2.c; I will compile it with gcc.",
            "/app/gpt2.c",
        ),
        (
            "Create the file /app/pipeline_parallel.py and implement train_step.",
            "/app/pipeline_parallel.py",
        ),
    ),
)
def test_common_imperative_output_forms_become_advisory_deliverables(
    request_text: str,
    expected_display_path: str,
    tmp_path: Path,
) -> None:
    hints = infer_public_deliverable_hints(
        request_text,
        workspace_path=tmp_path,
    )

    assert [item.display_path for item in hints] == [expected_display_path]


def test_described_runtime_side_effect_is_not_a_primary_deliverable(
    tmp_path: Path,
) -> None:
    hints = infer_public_deliverable_hints(
        "I provided doomgeneric_img.c, which will write each drawn frame to "
        "/tmp/frame.bmp. Build the doomgeneric_mips ELF for me.",
        workspace_path=tmp_path,
    )

    assert all(item.display_path != "/tmp/frame.bmp" for item in hints)


def test_side_effect_filter_applies_to_noun_form_but_keeps_user_imperative(
    tmp_path: Path,
) -> None:
    hints = infer_public_deliverable_hints(
        "The supplied renderer will write a file /tmp/frame.bmp while it runs. "
        "You should create a python file /app/driver.py to control it.",
        workspace_path=tmp_path,
    )

    assert [item.display_path for item in hints] == ["/app/driver.py"]


def test_explicit_output_directory_list_becomes_advisory_deliverables(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    hints = infer_public_deliverable_hints(
        "Write these two artifacts under `artifacts/`:\n\n"
        "1. `document.md`\n"
        "2. `layout.json`\n\n"
        "Use the source faithfully.",
        workspace_path=tmp_path,
    )

    assert [(item.path, item.display_path) for item in hints] == [
        (str(artifacts / "document.md"), "document.md"),
        (str(artifacts / "layout.json"), "layout.json"),
    ]


def test_output_directory_list_rejects_nested_or_extensionless_items(
    tmp_path: Path,
) -> None:
    hints = infer_public_deliverable_hints(
        "Create output files in `artifacts/`:\n"
        "- `safe.json`\n"
        "- `../outside.json`\n"
        "- `nested/report.md`\n"
        "- `README`",
        workspace_path=tmp_path,
    )

    assert [(item.path, item.display_path) for item in hints] == [
        (str(tmp_path / "artifacts" / "safe.json"), "safe.json"),
    ]


def test_input_directory_list_is_not_inferred_as_output(tmp_path: Path) -> None:
    assert infer_public_deliverable_hints(
        "Inspect these inputs under `/workspace/input/`:\n\n"
        "1. `document.pdf`\n"
        "2. `notes.txt`",
        workspace_path=tmp_path,
    ) == ()


def test_output_directory_list_stops_before_later_input_list(tmp_path: Path) -> None:
    hints = infer_public_deliverable_hints(
        "Write output artifacts under `outputs/`:\n\n"
        "1. `answer.md`: the result.\n\n"
        "Then inspect these source files:\n\n"
        "1. `input.pdf`\n"
        "2. `notes.txt`",
        workspace_path=tmp_path,
    )

    assert [(item.path, item.display_path) for item in hints] == [
        (str(tmp_path / "outputs" / "answer.md"), "answer.md"),
    ]


@pytest.mark.parametrize(
    "request_text",
    (
        "The file sequences.fasta contains the input sequences.",
        "Read https://example.test/result.json for background.",
        "Compare input.csv with old-output.csv before deciding what to do.",
        "The output file should be titled ../outside.json.",
    ),
)
def test_incidental_or_unsafe_paths_are_not_public_deliverables(
    request_text: str,
    tmp_path: Path,
) -> None:
    assert infer_public_deliverable_hints(request_text, workspace_path=tmp_path) == ()


def test_default_completion_publishes_advisory_deliverable_without_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AWORLD_COMPLETION_MODE", raising=False)
    context = Context(task_id="public-delivery-hint")
    assert configure_runtime_completion(
        context,
        request="Create the report and save it as result.json.",
        workspace_path=tmp_path,
    ) is None
    assert context.completion_contract is None
    assert context.context_info["public_deliverable_contract"] == {
        "schema_version": "aworld.public-deliverables/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "artifacts": [
            {
                "deliverable_id": "public-output-1",
                "path": str((tmp_path / "result.json").resolve()),
                "display_path": "result.json",
                "kind": "file",
                "authority": "public_task_advisory",
            }
        ],
    }


def test_explicit_public_tool_becomes_non_executable_capability_hint() -> None:
    hints = infer_public_executable_hints(
        "The output of primer3's oligotm tool should be considered ground truth."
    )
    assert [item.executable for item in hints] == ["oligotm"]
    assert hints[0].authority == "public_task_advisory"


def test_capability_hint_is_published_but_never_executed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AWORLD_COMPLETION_MODE", raising=False)
    context = Context(task_id="public-capability-hint")
    configure_runtime_completion(
        context,
        request="Use the jq tool to inspect the input.",
        workspace_path=tmp_path,
    )
    assert context.context_info["public_capability_hints"] == {
        "schema_version": "aworld.public-capabilities/v1",
        "authority": "public_task_advisory",
        "source": "public_task_text",
        "executables": [
            {
                "capability_id": "public-executable-1",
                "executable": "jq",
                "authority": "public_task_advisory",
            }
        ],
    }
    assert context.completion_contract is None


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
