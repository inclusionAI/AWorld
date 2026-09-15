from pathlib import Path

import pytest

from aworld.core.context.base import Context
from aworld.core.context.compiler import CompletionMode, CompletionStatus
from aworld_cli.core.runtime_completion import (
    build_runtime_completion_contract,
    configure_runtime_completion,
    infer_declared_output_paths,
)


def test_inference_selects_output_path_but_not_input_path() -> None:
    request = "请读取 /app/source.csv，并将最终报表保存到 /app/out/report.xlsx"

    assert infer_declared_output_paths(request) == ("/app/out/report.xlsx",)


def test_inference_handles_english_output_and_ignores_url_and_glob() -> None:
    request = (
        "Read /app/input.json and write the final report to ./result.md.\n"
        "Export references to https://example.test/report\n"
        "Save temporary shards to /tmp/chunks/*.json"
    )

    assert infer_declared_output_paths(request) == ("./result.md",)


def test_inference_accepts_explicit_bare_output_filename() -> None:
    request = (
        "I have a decompressor in /app/decomp.c and input data in /app/data.txt. "
        "Write me data.comp that's compressed for that decompressor."
    )

    assert infer_declared_output_paths(request) == ("data.comp",)


def test_inference_accepts_direct_absolute_and_nested_relative_targets() -> None:
    request = "Create /app/result.json. Then export the report to out/final.xlsx."

    assert infer_declared_output_paths(request) == (
        "/app/result.json",
        "out/final.xlsx",
    )


def test_inference_rejects_negated_writes_and_bare_input_filenames() -> None:
    request = (
        "Read source.csv, but do not write scratch.csv. "
        "Inspect https://example.test/result.json."
    )

    assert infer_declared_output_paths(request) == ()


def test_inference_ignores_paths_in_code_blocks_and_read_only_requests() -> None:
    request = "Open /app/input.pdf and inspect it.\n```sh\nwrite output to /app/fake.txt\n```"

    assert infer_declared_output_paths(request) == ()


def test_contract_resolves_relative_paths_against_task_workspace(tmp_path: Path) -> None:
    contract = build_runtime_completion_contract(
        "Save it to ./answer.json",
        workspace_path=tmp_path,
        infer_paths=True,
    )

    assert contract is not None
    assert tuple(item.path for item in contract.required_artifacts) == (
        str((tmp_path / "answer.json").resolve()),
    )
    assert contract.max_repairs == 1


@pytest.mark.asyncio
async def test_configured_contract_observes_missing_then_created_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "answer.json"
    monkeypatch.setenv("AWORLD_COMPLETION_MODE", "enforce")
    monkeypatch.setenv("AWORLD_INFER_REQUIRED_ARTIFACTS", "true")
    context = Context(task_id="completion-test")

    contract = configure_runtime_completion(
        context,
        request=f"Write the answer to {output_path}",
        workspace_path=tmp_path,
    )
    assert contract is not None

    context.record_completion_final_evidence("agent_final_response")
    await context.resolve_completion_evidence()
    missing = context.assess_completion_contract(agent_claimed_finished=True)
    assert missing is not None
    assert missing.mode is CompletionMode.ENFORCE
    assert missing.status is CompletionStatus.REPAIR_REQUIRED
    assert missing.reason_codes == ("required_artifact_missing",)

    output_path.write_text("{}", encoding="utf-8")
    await context.resolve_completion_evidence()
    satisfied = context.assess_completion_contract(agent_claimed_finished=True)
    assert satisfied is not None
    assert satisfied.status is CompletionStatus.SATISFIED


def test_completion_contract_is_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AWORLD_COMPLETION_MODE", raising=False)
    monkeypatch.setenv("AWORLD_INFER_REQUIRED_ARTIFACTS", "true")
    context = Context(task_id="completion-off")

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
    monkeypatch.delenv("AWORLD_INFER_REQUIRED_ARTIFACTS", raising=False)
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
