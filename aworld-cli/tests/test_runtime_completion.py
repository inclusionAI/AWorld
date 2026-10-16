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
    infer_public_obligation_hints,
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


@pytest.mark.parametrize(
    "request_text",
    (
        "Do not create a file forbidden.txt.",
        "Never write a file secret.json.",
        "If you create a file debug.log while investigating, delete it.",
        "Unless explicitly requested, create a file optional.txt only locally.",
        "The tests create a file /tmp/test-output.bin while they run.",
        "The renderer must write a file /tmp/frame.bmp for each frame.",
        "Example: create a file sample.json to illustrate the format.",
        "Only if debugging is enabled, create a file debug.log.",
        "Should you create a file scratch.txt, remove it before finishing.",
        "Provided that tracing is requested, write a file trace.json.",
        "In case diagnostics fail, create a file diagnostics.txt.",
        "The installation process must create a file /tmp/install.state while running.",
        "The workflow must write a file trace.json during execution.",
    ),
)
def test_negated_conditional_or_component_outputs_are_not_deliverables(
    request_text: str,
    tmp_path: Path,
) -> None:
    assert infer_public_deliverable_hints(
        request_text,
        workspace_path=tmp_path,
    ) == ()


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


@pytest.mark.parametrize(
    "request_text",
    (
        "Create a directory at `/app/ssl/` to store all certificate files.",
        "Create a Flask API that responds to POST requests at /sentiment.",
        "Run the development server and expose the endpoint at /health.",
    ),
)
def test_directories_and_http_routes_are_not_misclassified_as_files(
    request_text: str,
    tmp_path: Path,
) -> None:
    assert infer_public_deliverable_hints(
        request_text,
        workspace_path=tmp_path,
    ) == ()


def test_explicit_extensionless_executable_remains_a_deliverable(
    tmp_path: Path,
) -> None:
    hints = infer_public_deliverable_hints(
        "Create an executable named /app/release and verify it runs.",
        workspace_path=tmp_path,
    )

    assert [item.display_path for item in hints] == ["/app/release"]


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
    ledger = context.context_info["public_task_contract"]
    assert ledger["schema_version"] == "aworld.public-task-contract/v1"
    assert ledger["authority"] == "public_task_advisory"
    assert ledger["source"] == "public_task_text"
    assert ledger["request_fingerprint"].startswith("sha256:")
    assert ledger["obligations"][0]["kind"] == "deliverable_file"


def test_public_contract_extracts_only_explicit_task_requirements(
    tmp_path: Path,
) -> None:
    obligations = infer_public_obligation_hints(
        """Create `/app/server.py` with this exact public interface:

```proto
message SetValRequest {
  string key = 1;
  int32 value = 2;
}
```

Implement `async def run_tasks(tasks: list, max_concurrent: int) -> None`.
Use port 5328 and leave the service running in the background.
The model file must be less than 150MB and reach at least 0.62 accuracy.
Do not invoke `/app/private_binary`.
When finished I should be able to run `python /app/server.py`.
""",
        workspace_path=tmp_path,
    )

    by_kind: dict[str, list] = {}
    for item in obligations:
        by_kind.setdefault(item.kind, []).append(item)

    fields = {item.subject: item.expectation for item in by_kind["structured_field"]}
    assert fields["key"]["declared_type"] == "string"
    assert fields["value"]["declared_type"] == "int32"
    assert by_kind["function_signature"][0].subject == "run_tasks"
    assert by_kind["service_port"][0].expectation == {
        "port": 5328,
        "required": True,
    }
    assert by_kind["process_liveness"]
    assert len(by_kind["numeric_threshold"]) == 1
    assert by_kind["negative_constraint"]
    assert by_kind["public_command"][0].expectation["command"] == (
        "python /app/server.py"
    )
    assert all(item.authority == "public_task_advisory" for item in obligations)
    assert all(item.source == "public_task_text" for item in obligations)
    assert all(item.source_fingerprint.startswith("sha256:") for item in obligations)


def test_public_contract_extracts_named_fields_size_and_precision_requirements(
    tmp_path: Path,
) -> None:
    obligations = infer_public_obligation_hints(
        """Create `report.json` with required field `value`.
The workbook must contain a sheet named `Summary`.
The result must be within 0.01 of the published input total.
The CSV must contain exactly 20 rows and stay under 2 MB.
""",
        workspace_path=tmp_path,
    )

    fields = {
        item.subject for item in obligations if item.kind == "structured_field"
    }
    thresholds = [
        item for item in obligations if item.kind == "numeric_threshold"
    ]
    assert {"value", "Summary"} <= fields
    assert len(thresholds) == 2


def test_public_contract_does_not_promote_examples_or_component_side_effects(
    tmp_path: Path,
) -> None:
    obligations = infer_public_obligation_hints(
        "The tests will create `/tests/reward.json`. "
        "Example: create file sample.json. "
        "The supplied renderer will write `/tmp/frame.bmp`. "
        "Please analyze the existing input only.",
        workspace_path=tmp_path,
    )

    assert all(item.kind != "deliverable_file" for item in obligations)
    serialized = json.dumps([item.expectation for item in obligations])
    assert "/tests/reward.json" not in serialized
    assert "/tmp/frame.bmp" not in serialized


def test_public_contract_is_bounded_and_deduplicated(tmp_path: Path) -> None:
    request = "\n".join(
        "Use port 5328 and keep the service running in the background."
        for _ in range(100)
    )

    obligations = infer_public_obligation_hints(
        request,
        workspace_path=tmp_path,
    )

    assert len(obligations) <= 32
    assert len([item for item in obligations if item.kind == "service_port"]) == 1
    assert len([item for item in obligations if item.kind == "process_liveness"]) == 1


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
