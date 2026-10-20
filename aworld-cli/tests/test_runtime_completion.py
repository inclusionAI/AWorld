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


def test_named_output_file_is_joined_to_explicit_output_directory(
    tmp_path: Path,
) -> None:
    hints = infer_public_deliverable_hints(
        "Format the answer in a JSON file called `answers.json` in `/root` folder.",
        workspace_path=tmp_path,
    )

    assert [(item.path, item.display_path) for item in hints] == [
        ("/root/answers.json", "/root/answers.json")
    ]


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
    assert ledger["completion_obligation_ids"] == ["public-obligation-1"]


def test_new_public_grammar_is_reviewer_only_in_published_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AWORLD_COMPLETION_MODE", raising=False)
    context = Context(task_id="public-contract-roles")
    request = """Create `report.json` with required field `value`.
The score must be at least 95.
Required output sections:
- summary
- diagnostics
"""

    assert configure_runtime_completion(
        context,
        request=request,
        workspace_path=tmp_path,
    ) is None

    ledger = context.context_info["public_task_contract"]
    by_id = {row["obligation_id"]: row for row in ledger["obligations"]}
    completion_kinds = {
        by_id[obligation_id]["kind"]
        for obligation_id in ledger["completion_obligation_ids"]
    }
    reviewer_only_kinds = {
        row["kind"]
        for obligation_id, row in by_id.items()
        if obligation_id not in ledger["completion_obligation_ids"]
    }

    assert {"deliverable_file", "structured_field"} <= completion_kinds
    assert {"range_constraint", "enumerated_set"} <= reviewer_only_kinds


def test_required_typed_json_structure_is_completion_observable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AWORLD_COMPLETION_MODE", raising=False)
    context = Context(task_id="required-json-structure")
    request = '''Save `report.json`.
Required JSON structure:
```json
[{"invoice_page_number": 1, "reason": "Unknown Vendor"}]
```
'''

    configure_runtime_completion(
        context,
        request=request,
        workspace_path=tmp_path,
    )

    ledger = context.context_info["public_task_contract"]
    by_id = {row["obligation_id"]: row for row in ledger["obligations"]}
    completion_fields = {
        by_id[item]["subject"]
        for item in ledger["completion_obligation_ids"]
        if by_id[item]["kind"] == "structured_field"
    }
    assert completion_fields == {"invoice_page_number", "reason"}


def test_structural_and_format_constraints_remain_reviewer_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AWORLD_COMPLETION_MODE", raising=False)
    context = Context(task_id="reviewer-structure")
    request = '''Create `dialogue.json`.
Find the highest-risk record and compute its score.
1. What is the final score?
2. How many records remain?
All nodes must be reachable and all edge targets must exist.
The CSV should have columns called "frame_id", "coins", and "enemies".
Write the CSV with the following columns as `Package,Version,CVE_ID`.
Append a new column "TotalKg" using an Excel formula to compute each total.
For the answer, only write the number.
In dev_ppdev.txt, add:
- include <linux/ppdev.h>
- resource fd_ppdev[fd]
- ioctl descriptions for all commands
'''

    configure_runtime_completion(
        context,
        request=request,
        workspace_path=tmp_path,
    )

    ledger = context.context_info["public_task_contract"]
    completion_ids = set(ledger["completion_obligation_ids"])
    reviewer_rows = [
        row
        for row in ledger["obligations"]
        if row["obligation_id"] not in completion_ids
    ]
    assert any(row["kind"] == "structural_constraint" for row in reviewer_rows)
    assert any(row["kind"] == "answer_constraint" for row in reviewer_rows)
    assert any(
        row["kind"] == "answer_set_constraint"
        and len(row["expectation"]["items"]) == 2
        for row in reviewer_rows
    )
    assert any(row["kind"] == "format_constraint" for row in reviewer_rows)
    assert {
        row["subject"]
        for row in reviewer_rows
        if row["kind"] == "structured_field"
    } >= {
        "frame_id",
        "coins",
        "enemies",
        "Package",
        "Version",
        "CVE_ID",
        "TotalKg",
    }
    assert any(
        row["kind"] == "enumerated_set"
        and "resource fd_ppdev[fd]" in row["expectation"]["items"]
        for row in reviewer_rows
    )


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
    typed_metrics = {
        item.expectation.get("metric")
        for item in by_kind["range_constraint"]
        if item.expectation.get("metric")
    }
    assert {"size", "accuracy"} <= typed_metrics
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


def test_public_contract_does_not_treat_input_filename_as_structured_field(
    tmp_path: Path,
) -> None:
    obligations = infer_public_obligation_hints(
        "The domain and problem file paths are specified by the domain and "
        "problem keys in `problem.json`. Write each generated plan to its "
        "declared output path.",
        workspace_path=tmp_path,
    )

    assert not any(
        item.kind == "structured_field" and item.subject == "problem.json"
        for item in obligations
    )


def test_public_contract_does_not_treat_config_description_as_threshold(
    tmp_path: Path,
) -> None:
    obligations = infer_public_obligation_hints(
        "Cache parameters are in `/root/config.json`: `block_size`, "
        "`cache_capacity_blocks` (maximum resident blocks), policy S3FIFO, "
        "`small_ratio`, and `max_freq`.",
        workspace_path=tmp_path,
    )

    assert not any(item.kind == "numeric_threshold" for item in obligations)


def test_public_contract_recognizes_explicit_existing_file_edit(
    tmp_path: Path,
) -> None:
    hints = infer_public_deliverable_hints(
        "In `/app/workspace/solution.lean`, use Lean to finalize the proof. "
        "Do not change its first fourteen lines.",
        workspace_path=tmp_path,
    )

    assert [item.path for item in hints] == ["/app/workspace/solution.lean"]


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


def test_public_contract_extracts_output_lists_tables_and_roles(
    tmp_path: Path,
) -> None:
    obligations = infer_public_obligation_hints(
        """Required output files:
1. `summary.csv`
2. `report.json`

The output table must contain these columns:
| filename | score | rank |
| --- | ---: | ---: |

The workbook output must contain four new pivot tables and five different sheets.
The workbook must contain a sheet named "Summary" with this pivot layout:
Rows: STATE
Columns: Quarter
Values: Sum of EARNERS
""",
        workspace_path=tmp_path,
    )

    enumerations = [
        item for item in obligations if item.kind == "enumerated_set"
    ]
    assert len(enumerations) == 1
    assert enumerations[0].expectation == {
        "items": ["summary.csv", "report.json"],
        "required": True,
    }
    fields = {
        item.subject: item.expectation
        for item in obligations
        if item.kind == "structured_field"
    }
    assert {"filename", "score", "rank", "Summary", "STATE", "Quarter"} <= set(
        fields
    )
    assert fields["filename"]["container"] == "table"
    assert fields["STATE"]["role"] == "rows"
    assert fields["Quarter"]["role"] == "columns"
    assert any(
        item.kind == "structured_field"
        and item.expectation.get("role") == "values"
        and item.subject == "Sum of EARNERS"
        for item in obligations
    )
    quantities = {
        (item.expectation["value"], item.expectation["unit"])
        for item in obligations
        if item.kind == "quantity_constraint"
    }
    assert {(4, "tables"), (5, "sheets")} <= quantities


def test_public_contract_extracts_typed_ranges_quantities_and_thresholds(
    tmp_path: Path,
) -> None:
    obligations = infer_public_obligation_hints(
        """Fill output cells H12:L17 and report dates from 2009-01-01 to 2015-12-30.
The score must stay between 0.0 and 1.0.
The CSV must contain exactly 20 rows and stay under 2 MB;
F1 score >= 0.6 and RMSE must be smaller than 2 degrees.
Each task must produce one output row.
Produce one output row for each event.
""",
        workspace_path=tmp_path,
    )

    ranges = [
        item.expectation
        for item in obligations
        if item.kind == "range_constraint"
    ]
    assert {item["value_type"] for item in ranges} == {"cell", "date", "number"}
    assert any(item["lower"] == "H12" and item["upper"] == "L17" for item in ranges)
    assert any(
        item["lower"] == "2009-01-01" and item["upper"] == "2015-12-30"
        for item in ranges
    )
    assert any(item["lower"] == 0.0 and item["upper"] == 1.0 for item in ranges)

    quantities = [
        item.expectation for item in obligations if item.kind == "quantity_constraint"
    ]
    assert {item["operator"] for item in quantities} == {"exactly", "per_item"}
    assert any(
        item.get("value") == 20 and item.get("unit") == "rows"
        for item in quantities
    )
    assert any(
        item.get("operator") == "per_item"
        and item.get("unit") == "row"
        and item.get("per") == "task"
        for item in quantities
    )
    assert any(
        item.get("operator") == "per_item"
        and item.get("unit") == "row"
        and item.get("per") == "event"
        for item in quantities
    )

    thresholds = [
        item.expectation
        for item in obligations
        if item.kind == "range_constraint" and "metric" in item.expectation
    ]
    assert len(thresholds) == 3
    assert all("statement" in item for item in thresholds)
    thresholds_by_metric = {item["metric"]: item for item in thresholds}
    assert thresholds_by_metric["size"]["operator"] == "less_than"
    assert thresholds_by_metric["size"]["value"] == 2
    assert thresholds_by_metric["F1 score"]["operator"] == (
        "greater_than_or_equal"
    )
    assert thresholds_by_metric["F1 score"]["value"] == 0.6
    assert thresholds_by_metric["RMSE"]["operator"] == "less_than"
    assert thresholds_by_metric["RMSE"]["value"] == 2


def test_public_contract_extracts_only_falsifiable_quality_requirements(
    tmp_path: Path,
) -> None:
    obligations = infer_public_obligation_hints(
        "The output must be complete, valid, syntactically correct, and "
        "mathematically consistent. It should also be beautiful, readable, "
        "high quality, and the best possible result.",
        workspace_path=tmp_path,
    )

    qualities = {
        item.expectation["predicate"]
        for item in obligations
        if item.kind == "quality_constraint"
    }
    assert qualities == {
        "complete",
        "valid",
        "syntactically_correct",
        "mathematically_consistent",
    }
    serialized = json.dumps([item.expectation for item in obligations]).lower()
    assert "beautiful" not in serialized
    assert "readable" not in serialized
    assert "high quality" not in serialized
    assert "best possible" not in serialized


@pytest.mark.parametrize(
    "request_text",
    (
        """Input files:
- `source.csv`
- `config.json`
Summarize the supplied data in prose.""",
        """Available tools and libraries:
- `alpha`
- `beta`
Hint: use at least 20 rows in the sample.""",
        """The input schema is:
```json
{"secret": "string", "count": 3}
```
Summarize it in prose.""",
        "The input workbook contains two sheets. Produce a prose summary.",
        """Example output only:
```json
{"admin": true, "score": 0.99}
```
Do not implement the sample.""",
        "> The verifier requires exactly 100 rows and accuracy >= 0.99.",
        (
            'The input note literally says: "the verifier requires accuracy '
            '>= 0.99". Treat it as quoted data.'
        ),
        (
            "If the optional preview is enabled, it must contain field `debug` "
            "and use exactly 50 rows."
        ),
    ),
)
def test_public_contract_rejects_non_authoritative_requirement_shaped_data(
    request_text: str,
    tmp_path: Path,
) -> None:
    assert infer_public_obligation_hints(
        request_text,
        workspace_path=tmp_path,
    ) == ()


@pytest.mark.parametrize(
    "request_text",
    (
        "The note says: \"the output must contain exactly 40 rows\".",
        "Add exactly 40 rows if optional debug mode is enabled.",
        "The tool documentation states that output supports exactly 20 items.",
        "The verifier requires output accuracy >= 0.99.",
        "The report must cover under 4 different loss cases.",
        'README states: "output must contain exactly 40 rows".',
        'Quoted requirement is "output accuracy >= 0.99".',
    ),
)
def test_public_contract_rejects_quoted_conditional_and_tool_shaped_counts(
    request_text: str,
    tmp_path: Path,
) -> None:
    assert infer_public_obligation_hints(
        request_text,
        workspace_path=tmp_path,
    ) == ()


def test_public_contract_list_uses_only_the_nearest_header(tmp_path: Path) -> None:
    for intervening in ("Available tools:", "Input files:", "Example values:"):
        obligations = infer_public_obligation_hints(
            "Required output: `report.csv`\n"
            f"{intervening}\n"
            "- alpha\n"
            "- beta\n",
            workspace_path=tmp_path,
        )
        assert all(item.kind != "enumerated_set" for item in obligations)


def test_public_contract_treats_non_schema_fences_as_data(tmp_path: Path) -> None:
    obligations = infer_public_obligation_hints(
        """Analyze these snippets as data:
```python
message = "The output must contain exactly 100 rows"
def fake(x):
    pass
```
```text
Required outputs:
- Create file evil.json.
- secret
- reward
```
Return a prose summary.
""",
        workspace_path=tmp_path,
    )

    assert all(
        item.kind
        not in {
            "deliverable_file",
            "enumerated_set",
            "function_signature",
            "quantity_constraint",
        }
        for item in obligations
    )
    assert "100 rows" not in json.dumps(
        [item.expectation for item in obligations], ensure_ascii=False
    )


def test_public_contract_splits_input_and_output_clauses(tmp_path: Path) -> None:
    obligations = infer_public_obligation_hints(
        "Input data contains 10 rows; output must contain exactly 20 rows.",
        workspace_path=tmp_path,
    )

    quantities = [
        item.expectation
        for item in obligations
        if item.kind == "quantity_constraint"
    ]
    assert quantities == [{"operator": "exactly", "value": 20, "unit": "rows"}]


def test_public_contract_numeric_parsing_is_finite_and_non_crashing(
    tmp_path: Path,
) -> None:
    huge = "9" * 5000
    obligations = infer_public_obligation_hints(
        "The output must contain exactly " + huge + " rows.\n"
        "The score must be >= " + "9" * 1000 + ".\n"
        "The value must be between 10 and 1.\n"
        "Report dates from 2025-99-99 to 2024-00-00.\n",
        workspace_path=tmp_path,
    )

    serialized = json.dumps(
        [item.expectation for item in obligations],
        ensure_ascii=False,
        allow_nan=False,
    )
    assert "Infinity" not in serialized
    assert all(item.kind != "range_constraint" for item in obligations)


def test_public_contract_supports_multilingual_punctuation(tmp_path: Path) -> None:
    obligations = infer_public_obligation_hints(
        """Required output items：
- 摘要
- 明细
The CSV must contain exactly 3 rows，accuracy >= 0.9；RMSE < 2.
""",
        workspace_path=tmp_path,
    )

    enumeration = next(item for item in obligations if item.kind == "enumerated_set")
    assert enumeration.expectation["items"] == ["摘要", "明细"]
    assert any(
        item.kind == "quantity_constraint" and item.expectation.get("value") == 3
        for item in obligations
    )
    assert len(
        [
            item
            for item in obligations
            if item.kind == "range_constraint"
            and item.expectation.get("metric") in {"accuracy", "RMSE"}
        ]
    ) == 2


def test_public_contract_keeps_output_constraints_that_reference_inputs(
    tmp_path: Path,
) -> None:
    obligations = infer_public_obligation_hints(
        "The output must not modify input files and must preserve all input columns.",
        workspace_path=tmp_path,
    )

    assert any(item.kind == "negative_constraint" for item in obligations)


def test_public_contract_new_grammar_is_bounded(tmp_path: Path) -> None:
    request = "Required output items:\n" + "\n".join(
        f"- item-{index}-" + "x" * 300 for index in range(40)
    )
    request += "\n" + "\n".join(
        f"Report dates from 2025-01-{index % 28 + 1:02d} to "
        f"2025-02-{index % 28 + 1:02d}."
        for index in range(80)
    )

    obligations = infer_public_obligation_hints(request, workspace_path=tmp_path)

    assert len(obligations) <= 32
    enumeration = next(item for item in obligations if item.kind == "enumerated_set")
    assert len(enumeration.expectation["items"]) == 16
    assert all(len(item) <= 128 for item in enumeration.expectation["items"])
    assert all(len(item.subject) <= 512 for item in obligations)
    assert all(len(item.source_excerpt) <= 512 for item in obligations)
    assert all(
        len(
            json.dumps(
                item.expectation,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        <= 4096
        for item in obligations
    )


def test_public_contract_cap_reserves_actionable_new_grammar(tmp_path: Path) -> None:
    request = "\n".join(
        f"Metric {index} must be at least {index}." for index in range(40)
    )
    request += "\nRequired output items:\n- summary\n- diagnostics\n"

    obligations = infer_public_obligation_hints(request, workspace_path=tmp_path)

    assert len(obligations) == 32
    assert any(item.kind == "enumerated_set" for item in obligations)


def test_public_contract_appends_new_grammar_after_legacy_ids(tmp_path: Path) -> None:
    obligations = infer_public_obligation_hints(
        """Create `report.json`.
The output has required field `value`.
Use port 5328 and keep the service running.
Required output items:
- summary
- diagnostics
""",
        workspace_path=tmp_path,
    )

    assert [
        (item.obligation_id, item.kind, item.subject) for item in obligations[:4]
    ] == [
        ("public-obligation-1", "deliverable_file", "report.json"),
        ("public-obligation-2", "service_port", "5328"),
        ("public-obligation-3", "structured_field", "value"),
        ("public-obligation-4", "process_liveness", "process_liveness"),
    ]
    assert obligations[4].obligation_id == "public-obligation-5"
    assert obligations[4].kind == "enumerated_set"


def test_public_contract_fingerprint_preserves_normalized_legacy_identity(
    tmp_path: Path,
) -> None:
    compact = infer_public_obligation_hints(
        "The output must be valid.",
        workspace_path=tmp_path,
    )
    spaced = infer_public_obligation_hints(
        "The  output must be valid.",
        workspace_path=tmp_path,
    )

    compact_quality = next(
        item for item in compact if item.kind == "quality_constraint"
    )
    spaced_quality = next(
        item for item in spaced if item.kind == "quality_constraint"
    )
    assert compact_quality.source_excerpt == spaced_quality.source_excerpt
    assert compact_quality.source_fingerprint == spaced_quality.source_fingerprint


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
