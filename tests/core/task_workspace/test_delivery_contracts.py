from pathlib import Path

import pytest

from aworld.core.task_workspace.contracts import derive_delivery_contract


@pytest.mark.parametrize("source_text,outputs,inputs", [
    ("Read input.csv and save the report to output.json.", ["output.json"], ["input.csv"]),
    ("Please save input.csv to output.csv.", ["output.csv"], ["input.csv"]),
    ("Convert source.csv to converted.json.", ["converted.json"], ["source.csv"]),
    ("Create `result.json`.", ["result.json"], []),
    ('Save the report to "final report.csv".', ["final report.csv"], []),
    ("The output file must be written to output.json.", ["output.json"], []),
    ("Create first.json and second.csv.", ["first.json", "second.csv"], []),
    ("请读取 source.csv，并将最终结果保存到 output.json。", ["output.json"], ["source.csv"]),
])
def test_literal_public_requirements_bind_roles_and_source_spans(tmp_path, source_text, outputs, inputs):
    contract = derive_delivery_contract(source_text, workspace_path=tmp_path)
    assert [Path(item["path"]).name for item in contract["outputs"]] == outputs
    assert [Path(item["path"]).name for item in contract["inputs"]] == inputs
    for item in contract["outputs"] + contract["inputs"]:
        source = item["source"]
        assert source["quote"] == source_text[source["start"]:source["end"]]
        assert item["public_path"] in source["quote"]
    assert contract["source_hash"].startswith("sha256:")


@pytest.mark.parametrize("source_text", [
    "Read input.csv.",
    "Do not write scratch.csv.",
    "For example, write sample.csv.",
    "If needed, save optional.json.",
    "Save optional.json only if requested.",
    'Please explain the phrase "save output.csv".',
    "Should we save output.csv?",
    "Can you tell me how to write output.csv?",
    "```sh\nwrite output.json\n```",
    "Inspect https://example.test/output.json.",
])
def test_non_delivery_mentions_never_become_required_outputs(tmp_path, source_text):
    assert derive_delivery_contract(source_text, workspace_path=tmp_path)["outputs"] == []


@pytest.mark.parametrize("source_text,reason", [
    ("Save either output.csv or output.json.", "alternative_output_paths"),
    ("Create output.json. Do not create output.json.", "conflicting_output_obligations"),
    ("Keep source.csv unchanged. Write source.csv.", "immutable_input_is_output"),
    ("Create output.json with at least 100 bytes and at most 10 bytes.", "conflicting_size_constraints"),
])
def test_conflicting_or_ambiguous_requirements_are_reported_not_guessed(tmp_path, source_text, reason):
    contract = derive_delivery_contract(source_text, workspace_path=tmp_path)
    assert contract["outputs"] == []
    assert reason in {item["reason"] for item in contract["unresolved"]}
    assert contract["coverage_status"] == "partial"


@pytest.mark.parametrize("source_text,immutable", [
    ("Read source.csv.", False),
    ("Do not modify source.csv.", True),
    ("Keep source.csv unchanged.", True),
    ("Read source.csv and then clear the source directory.", False),
    ("请勿修改 source.csv。", True),
])
def test_only_explicit_input_preservation_is_immutable(tmp_path, source_text, immutable):
    contract = derive_delivery_contract(source_text, workspace_path=tmp_path)
    assert len(contract["inputs"]) == 1
    assert contract["inputs"][0]["immutable"] is immutable


def test_checks_use_literal_formats_and_size_units_without_inventing_semantics(tmp_path):
    contract = derive_delivery_contract("Create a nonempty result.json of at most 2 KiB.", workspace_path=tmp_path)
    checks = contract["outputs"][0]["checks"]
    assert {c["kind"] for c in checks} == {"exists", "regular_file", "nonempty", "json", "file_size"}
    assert next(c for c in checks if c["kind"] == "file_size")["max_bytes"] == 2048
    assert all(c["path"] == str(tmp_path / "result.json") for c in checks)
    assert not any("required_keys" in c for c in checks)
    strict = derive_delivery_contract("Create result.bin under 2 KB.", workspace_path=tmp_path)
    assert next(c for c in strict["outputs"][0]["checks"] if c["kind"] == "file_size")["max_bytes"] == 1999


def test_explicit_contract_has_priority_over_derived_or_ambiguous_requirements(tmp_path):
    contract = derive_delivery_contract("Save either guessed.json or other.csv.", workspace_path=tmp_path,
        explicit={"outputs": [{"path": "chosen.bin", "checks": [{"id": "caller-check", "kind": "file_size", "max_bytes": 10}]}]})
    assert [Path(item["path"]).name for item in contract["outputs"]] == ["chosen.bin"]
    assert contract["outputs"][0]["checks"][0]["id"] == "caller-check"
    assert contract["outputs"][0]["source"]["kind"] == "caller_contract"
    assert contract["unresolved"] == []


@pytest.mark.parametrize("source_text", [
    'Create output.json. For example, do not create output.json.',
    'Create output.json. Explain the phrase "do not create output.json".',
])
def test_quoted_or_example_negation_cannot_cancel_a_real_deliverable(tmp_path, source_text):
    contract = derive_delivery_contract(source_text, workspace_path=tmp_path)
    assert [Path(item['path']).name for item in contract['outputs']] == ['output.json']


def test_directory_request_is_uncovered_instead_of_a_guessed_regular_file(tmp_path):
    contract = derive_delivery_contract('Create the directory results/.', workspace_path=tmp_path)
    assert contract['outputs'] == []


def test_explicit_multioutput_checks_and_selection_policy_survive_normalization(tmp_path):
    explicit = {'outputs': ['a.json', 'b.json'],
                'checks': [{'id': 'cross-output', 'kind': 'command', 'argv': ['python', 'check.py']}],
                'policy': {'mandatory_checks': ['cross-output'], 'hard_constraints': [],
                           'objective': {'metric': 'score', 'direction': 'max'}}}
    contract = derive_delivery_contract('Produce the outputs.', workspace_path=tmp_path, explicit=explicit)
    assert contract['checks'][0]['id'] == 'cross-output'
    assert contract['checks'][0]['source']['kind'] == 'caller_contract'
    assert contract['policy'] == explicit['policy']


def test_unspecified_file_target_is_reported_without_guessing(tmp_path):
    contract = derive_delivery_contract('Save the result as a file.', workspace_path=tmp_path)
    assert contract['outputs'] == []
    assert contract['coverage_status'] == 'partial'
    assert contract['unresolved'][0]['reason'] == 'delivery_target_not_literal'


def test_explicit_check_and_selection_paths_are_canonical_without_changing_caller_targets(tmp_path):
    explicit = {
        'outputs': [{'path': 'a.json', 'checks': [{'id': 'caller-check', 'kind': 'csv',
                     'path': './b.csv', 'input': './source.csv', 'same_rows_as': './reference.csv'}]}, 'b.csv'],
        'inputs': ['./source.csv'],
        'checks': [{'id': 'cross-check', 'kind': 'csv', 'path': './b.csv', 'input': './source.csv'}],
        'policy': {'hard_constraints': [{'artifact': './a.json', 'max_bytes': 100}]},
    }
    contract = derive_delivery_contract('', workspace_path=tmp_path, explicit=explicit)
    check = contract['outputs'][0]['checks'][0]
    assert check['path'] == str(tmp_path / 'b.csv')
    assert check['input'] == str(tmp_path / 'source.csv')
    assert check['same_rows_as'] == str(tmp_path / 'reference.csv')
    assert contract['checks'][0]['path'] == str(tmp_path / 'b.csv')
    assert contract['checks'][0]['input'] == str(tmp_path / 'source.csv')
    assert contract['policy']['hard_constraints'][0]['artifact'] == str(tmp_path / 'a.json')
    equivalent = derive_delivery_contract('', workspace_path=tmp_path, explicit={'inputs': ['source.csv']})
    assert contract['inputs'][0]['id'] == equivalent['inputs'][0]['id']
    assert explicit['policy']['hard_constraints'][0]['artifact'] == './a.json'


@pytest.mark.parametrize('checks', [
    [{'id': 'same-id', 'kind': 'exists'}, {'id': 'same-id', 'kind': 'json'}],
    [{'id': 'workbench.delivery', 'kind': 'exists'}],
])
def test_explicit_checks_cannot_overwrite_other_evidence(tmp_path, checks):
    with pytest.raises(ValueError):
        derive_delivery_contract('', workspace_path=tmp_path,
                                 explicit={'outputs': [{'path': 'a.json', 'checks': checks}]})


def test_explicit_legacy_immutable_input_ids_preserve_caller_identity(tmp_path):
    path = str(tmp_path / 'source.csv')
    contract = derive_delivery_contract('', workspace_path=tmp_path,
        explicit={'inputs': [{'id': path, 'path': path, 'immutable': True}]})
    assert contract['inputs'][0]['id'] == path
