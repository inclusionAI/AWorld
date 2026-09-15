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


@pytest.mark.parametrize(
    "task_text",
    (
        "Explain how to export a report to out/report.xlsx.",
        "Tell me the command to create foo.txt.",
        "请解释如何把数据保存到 output.csv。",
        "What happens if I write the answer to result.json?",
        "Can this tool export reports to result.csv?",
        "Does this application save the report to result.json?",
        "Verify whether the application can write data to result.json.",
        "Can I save the report to output.csv?",
        "Tell me whether to save the report to answer.csv.",
        "Do you recommend I save the report to output.csv?",
        "Should we save it to output.csv?",
        "May I save it to output.csv?",
        "Would it be better to export to result.csv?",
        "Is it possible to save the report to output.csv?",
        "Please tell me if I should save the report to output.csv.",
        "When should we export the report to result.csv?",
        "请确认这个工具是否能把结果导出到 result.csv。",
        "Discuss the autosave-to output.csv feature.",
        "Does autosave-to output.csv work?",
        "The autosave-to output.csv setting should stay disabled.",
        "Must I save the report to output.csv?",
        "Shall we export the report to result.csv?",
        "Would I need to save the report to output.csv?",
        "Is it okay to save the report to output.csv?",
        "Would you advise me to save the report to output.csv?",
        "Should the application save the report to output.csv?",
        "我应该把报告保存到 output.csv 吗？",
        "建议把报告保存到 output.csv 吗？",
        "可以把报告保存到 output.csv 吗？",
    ),
)
def test_inference_rejects_instructional_output_examples(task_text: str) -> None:
    assert infer_declared_output_paths(task_text) == ()


def test_inference_keeps_direct_output_clause_after_explanation() -> None:
    for task_text in (
        "Explain the source schema, then save the converted data to output.csv.",
        "How to transform the input? Then save the result to output.csv.",
        "Explain how to parse the input, then save the result to output.csv.",
        "解释如何转换输入，然后保存到 output.csv。",
    ):
        assert infer_declared_output_paths(task_text) == ("output.csv",)


@pytest.mark.parametrize(
    ("task_text", "expected"),
    (
        ("Can you save the report to output.csv?", ("output.csv",)),
        ("Would you please export the result to result.csv?", ("result.csv",)),
    ),
)
def test_inference_keeps_polite_direct_output_requests(
    task_text: str, expected: tuple[str, ...]
) -> None:
    assert infer_declared_output_paths(task_text) == expected


@pytest.mark.parametrize(
    "task_text",
    (
        "You should not save the report to output.csv.",
        "Do not attempt to save the report to output.csv.",
        "No need to save the report to output.csv.",
        "You may save the report to output.csv.",
        "If needed, save the report to output.csv.",
        "I recommend that you save the report to output.csv.",
        "The application will save the report to output.csv.",
        "For example, save the report to output.csv.",
        "If CSV is requested, save to output.csv; otherwise save to output.json.",
    ),
)
def test_inference_rejects_non_obligatory_output_language(task_text: str) -> None:
    assert infer_declared_output_paths(task_text) == ()


@pytest.mark.parametrize(
    "task_text",
    (
        "Can you analyze the data and save the result to output.csv?",
        "Would you please inspect the input, then export it to output.csv?",
        "Could you carefully save the report to output.csv?",
        "Save the report to output.csv. Is that okay?",
    ),
)
def test_inference_keeps_compound_direct_requests(task_text: str) -> None:
    assert infer_declared_output_paths(task_text) == ("output.csv",)


@pytest.mark.parametrize(
    "task_text",
    (
        "Can you not save the report to output.csv?",
        "Could you please not save the report to output.csv?",
        "Save to output.csv only if it is needed.",
        "Save either output.csv or output.json.",
    ),
)
def test_inference_rejects_negated_or_optional_direct_language(
    task_text: str,
) -> None:
    assert infer_declared_output_paths(task_text) == ()


@pytest.mark.parametrize(
    ("task_text", "expected"),
    (
        ("Please save report.csv to output.csv.", ("output.csv",)),
        ("Save source.docx as result.pdf.", ("result.pdf",)),
        (
            "Please save input/a.csv and input/b.csv into output.csv.",
            ("output.csv",),
        ),
    ),
)
def test_inference_binds_destination_instead_of_source_path(
    task_text: str, expected: tuple[str, ...]
) -> None:
    assert infer_declared_output_paths(task_text) == expected


def test_inference_rejects_chinese_alternative_outputs() -> None:
    request = "请把结果保存到 output.csv 或 output.json。"

    assert infer_declared_output_paths(request) == ()


@pytest.mark.parametrize(
    "task_text",
    (
        'Would you say "save the report to output.csv"?',
        "Can you repeat: save the report to output.csv?",
        "Can you tell me to save the report to output.csv?",
        "Could you describe a command that will save to output.csv?",
        "Can you test whether this command will save to output.csv?",
        "Would you quote the phrase `save to output.csv`?",
        "Can you explain why the application will save to output.csv?",
        "Can you tell me where to save output.csv?",
    ),
)
def test_inference_rejects_nested_output_language_in_direct_questions(
    task_text: str,
) -> None:
    assert infer_declared_output_paths(task_text) == ()


@pytest.mark.parametrize(
    "task_text",
    (
        "Please analyze whether to save the result to output.csv.",
        "Please read: save the result to output.csv.",
        'Please review the proposed instruction "save the report to output.csv".',
        'Please open README.md and confirm it says "save to output.csv".',
        'Please analyze the statement "save to output.csv" for safety.',
        "Please inspect the text: write output.csv.",
        "Please process the request `export to output.csv` as text.",
        'Please convert the sentence "save to output.csv" into French.',
        'Please extract the phrase "save to output.csv" from this paragraph.',
    ),
)
def test_inference_rejects_nested_output_language_after_work_verbs(
    task_text: str,
) -> None:
    assert infer_declared_output_paths(task_text) == ()


@pytest.mark.parametrize(
    "task_text",
    (
        "请分析“保存到 output.csv”这句话。",
        "请读取并解释保存到 output.csv 这句话。",
        "请说明保存到 output.csv 的含义。",
        "请翻译“保存到 output.csv”。",
        "请讨论保存到 output.csv 的利弊。",
        "Save to output.csv: explain what this command does.",
    ),
)
def test_inference_rejects_chinese_and_suffix_meta_mentions(
    task_text: str,
) -> None:
    assert infer_declared_output_paths(task_text) == ()


@pytest.mark.parametrize(
    "task_text",
    (
        "Please analyze whether the application can process the data and save to output.csv.",
        "Please review documentation saying to process data and save to output.csv.",
        "Please analyze why the tool will process input and save to output.csv.",
        "Please analyze this request: read input and save to output.csv.",
        "请分析程序读取数据并保存到 output.csv 的行为。",
        "请分析这个请求：读取数据并保存到 output.csv。",
    ),
)
def test_inference_rejects_meta_coordinated_actions(task_text: str) -> None:
    assert infer_declared_output_paths(task_text) == ()


@pytest.mark.parametrize(
    "task_text",
    (
        "Please analyze the data and save the result to output.csv.",
        "请分析数据并保存到 output.csv。",
    ),
)
def test_inference_keeps_direct_coordinated_actions(task_text: str) -> None:
    assert infer_declared_output_paths(task_text) == ("output.csv",)


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
