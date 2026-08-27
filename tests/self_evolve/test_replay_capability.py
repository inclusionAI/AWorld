from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aworld.self_evolve import replay_capability as replay_capability_module
from aworld.self_evolve.replay_adaptation import ReplayCapabilityRequirement
from aworld.self_evolve.replay_capability import (
    REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION,
    ReplayCapabilityCompileRequest,
    ReplayCapabilityError,
    _build_recorded_response_index,
    _compile_failure_runtime_binding_diagnostics,
    build_replay_resource_limited_command,
    build_replay_sandboxed_command,
    compile_and_freeze_capability,
    discover_replay_capability,
    frozen_replay_fixture_shape_fingerprints,
    materialize_replay_evidence_derivations,
    replay_process_memory_bytes,
    verify_frozen_replay_capability,
)
from aworld.self_evolve.schema_diagnostics import SchemaFieldRepairConstraint


def test_conditional_service_schema_aggregates_parse_counterexamples() -> None:
    services = [
        {
            "transport": "http_fixture",
            "runtime_entrypoint": "replay/runtime.py",
        },
        {
            "transport": "skill_runtime",
        },
    ]

    with pytest.raises(ReplayCapabilityError) as error:
        replay_capability_module._validate_conditional_service_fields(services)

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["code"] == (
        "service_conditional_field_validation_failed"
    )
    constraints = error.value.details["schema_field_constraints"]
    assert {item["field_path"] for item in constraints} == {
        "services[*@transport:http_fixture].runtime_entrypoint",
        "services[*@transport:skill_runtime].runtime_entrypoint",
    }
    counterexamples = error.value.details["counterexample_contracts"]
    assert len(counterexamples) == 2
    assert all(
        item["schema_version"]
        == "aworld.self_evolve.schema_counterexample.v1"
        for item in counterexamples
    )
    assert len({item["counterexample_id"] for item in counterexamples}) == 2


def test_schema_counterexample_identity_is_stable_across_observed_values() -> None:
    first = replay_capability_module._schema_field_counterexample_contract(
        replay_capability_module._schema_field_violation(
            schema_layer="compile_result",
            field_path="services[*].runtime_entrypoint",
            rule="type",
            expected=("null",),
            value="replay/first.py",
        )
    )
    second = replay_capability_module._schema_field_counterexample_contract(
        replay_capability_module._schema_field_violation(
            schema_layer="compile_result",
            field_path="services[*].runtime_entrypoint",
            rule="type",
            expected=("null",),
            value="replay/second.py",
        )
    )
    distinct = replay_capability_module._schema_field_counterexample_contract(
        replay_capability_module._schema_field_violation(
            schema_layer="compile_result",
            field_path="services[*].response_fixture",
            rule="type",
            expected=("null",),
            value="replay/second.py",
        )
    )

    assert first["counterexample_id"] == second["counterexample_id"]
    assert first["actual_fingerprint"] != second["actual_fingerprint"]
    assert first["counterexample_id"] != distinct["counterexample_id"]


def test_identical_schema_instances_emit_one_counterexample_contract() -> None:
    violations = tuple(
        replay_capability_module._schema_field_violation(
            schema_layer="compile_result",
            field_path="services[*].readiness.kind",
            rule="required",
            expected=(),
            value=None,
        )
        for _ in range(5)
    )

    with pytest.raises(ReplayCapabilityError) as error:
        replay_capability_module._raise_schema_field_error(
            "replay services require a readiness kind",
            violations,
        )

    assert error.value.details["schema_field_violation_count"] == 5
    assert len(error.value.details["schema_field_violations"]) == 1
    assert error.value.details["schema_field_violations"][0][
        "occurrence_count"
    ] == 5
    assert len(error.value.details["counterexample_contracts"]) == 1


def test_missing_service_readiness_reports_known_allowed_values() -> None:
    with pytest.raises(ReplayCapabilityError) as error:
        replay_capability_module._validate_compile_result_service_schema(
            [
                {
                    "service_id": "service-1",
                    "requirement_id": "requirement-1",
                    "transport": "http_fixture",
                    "response_fixture": "fixtures/one.json",
                }
            ]
        )

    readiness = next(
        item
        for item in error.value.details["schema_field_constraints"]
        if item["field_path"] == "services[*].readiness.kind"
    )
    assert readiness == {
        "schema_layer": "compile_result",
        "field_path": "services[*].readiness.kind",
        "rule": "required",
        "expected": ["http", "tcp"],
    }


def test_empty_forbidden_conditional_field_is_a_schema_violation() -> None:
    with pytest.raises(ReplayCapabilityError) as error:
        replay_capability_module._validate_conditional_service_fields(
            [{"transport": "http_fixture", "runtime_entrypoint": ""}]
        )

    assert error.value.code == "schema_field_validation_failed"
    constraint = error.value.details["schema_field_constraints"][0]
    assert constraint["field_path"] == (
        "services[*@transport:http_fixture].runtime_entrypoint"
    )
    assert constraint["expected"] == ["null"]


def test_compile_failure_collects_independent_runtime_binding_constraint(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime.py"
    runtime.write_text("def respond():\n    return {}\n", encoding="utf-8")
    output_root = tmp_path / "output"
    output_root.mkdir()
    (output_root / "fixture.json").write_text(
        json.dumps(
            {
                "wrapper": [
                    {
                        "action_result": [
                            {
                                "action_name": "records.query",
                                "content": json.dumps(
                                    {"records": [{"value": "recorded"}]}
                                ),
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    capability = SimpleNamespace(runtime_files=(runtime,))
    request = SimpleNamespace(
        requirements=(SimpleNamespace(status="runtime_required"),)
    )

    details = _compile_failure_runtime_binding_diagnostics(
        capability,
        request=request,
        output_roots=(output_root,),
    )

    assert details is not None
    assert details["schema_field_constraints"] == [
        {
            "schema_layer": "runtime",
            "field_path": (
                "environment.AWORLD_REPLAY_RESPONSE_INDEX.consumer"
            ),
            "rule": "enum",
            "expected": ["json_sidecar_record_value_projector"],
            "value_domain": "source_behavior",
            "required_operations": [
                "read_environment_binding_as_path",
                "bind_environment_path_to_json_file_reader",
                "access_records_array",
                "project_record_value_field_directly",
            ],
            "forbidden_operations": [
                "coerce_environment_binding_to_numeric_index",
                "hide_environment_read_behind_zero_arg_return_helper",
                "substitute_raw_fixture_recursive_scan",
            ],
        }
    ]


def test_frozen_fixture_shape_fingerprint_ignores_recorded_scalar_values(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    fixture_path = fixture_root / "recorded.json"
    service = SimpleNamespace(response_fixture="recorded.json")
    capability = SimpleNamespace(
        frozen_root=str(tmp_path),
        services=(service,),
    )
    fixture_path.write_text(
        json.dumps({"records": [{"token": "private-alpha", "count": 1}]}),
        encoding="utf-8",
    )
    first = frozen_replay_fixture_shape_fingerprints(capability)
    fixture_path.write_text(
        json.dumps({"records": [{"token": "private-beta", "count": 99}]}),
        encoding="utf-8",
    )
    second = frozen_replay_fixture_shape_fingerprints(capability)
    fixture_path.write_text(
        json.dumps({"records": [{"token": ["private-beta"], "count": 99}]}),
        encoding="utf-8",
    )
    distinct = frozen_replay_fixture_shape_fingerprints(capability)

    assert first == second
    assert distinct != second
    assert "private-alpha" not in json.dumps(first)
    assert "private-beta" not in json.dumps(distinct)


def test_frozen_fixture_shape_fingerprint_includes_structure_after_128th_node(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    fixture_path = fixture_root / "recorded.json"
    capability = SimpleNamespace(
        frozen_root=str(tmp_path),
        services=(SimpleNamespace(response_fixture="recorded.json"),),
    )
    prefix = [{"value": index} for index in range(128)]
    fixture_path.write_text(
        json.dumps([*prefix, {"tail": "private"}]),
        encoding="utf-8",
    )
    object_tail = frozen_replay_fixture_shape_fingerprints(capability)
    fixture_path.write_text(
        json.dumps([*prefix, ["private"]]),
        encoding="utf-8",
    )
    array_tail = frozen_replay_fixture_shape_fingerprints(capability)

    assert object_tail != array_tail
    assert "private" not in json.dumps(object_tail)
    assert "private" not in json.dumps(array_tail)


def test_frozen_fixture_shape_fingerprint_covers_oversized_json_structure(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    fixture_path = fixture_root / "recorded.json"
    capability = SimpleNamespace(
        frozen_root=str(tmp_path),
        services=(SimpleNamespace(response_fixture="recorded.json"),),
    )
    padding = "private-" + ("x" * (2 * 1024 * 1024))
    fixture_path.write_text(
        json.dumps({"padding": padding, "tail": []}, separators=(",", ":")),
        encoding="utf-8",
    )
    array_tail = frozen_replay_fixture_shape_fingerprints(capability)
    fixture_path.write_text(
        json.dumps({"padding": padding, "tail": {}}, separators=(",", ":")),
        encoding="utf-8",
    )
    object_tail = frozen_replay_fixture_shape_fingerprints(capability)

    assert array_tail != object_tail
    assert "private" not in json.dumps(array_tail)
    assert "private" not in json.dumps(object_tail)


def test_frozen_fixture_shape_fingerprint_covers_structure_beyond_depth_64(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    fixture_path = fixture_root / "recorded.json"
    capability = SimpleNamespace(
        frozen_root=str(tmp_path),
        services=(SimpleNamespace(response_fixture="recorded.json"),),
    )

    def nested(leaf: object) -> object:
        value = leaf
        for _ in range(80):
            value = {"next": value}
        return value

    fixture_path.write_text(json.dumps(nested([])), encoding="utf-8")
    array_leaf = frozen_replay_fixture_shape_fingerprints(capability)
    fixture_path.write_text(json.dumps(nested({})), encoding="utf-8")
    object_leaf = frozen_replay_fixture_shape_fingerprints(capability)

    assert array_leaf != object_leaf


def _nested_recorded_fixture_value() -> str:
    return json.dumps(
        {
            "wrapper": [
                {
                    "action_result": [
                        {
                            "action_name": "records.query",
                            "content": json.dumps(
                                {"records": [{"id": 1, "value": "recorded"}]},
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }
            ]
        },
        ensure_ascii=False,
    )


def test_replay_process_memory_bytes_prefers_native_darwin_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(replay_capability_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        replay_capability_module,
        "_darwin_process_memory_bytes",
        lambda process_id: 12_345,
    )

    def unexpected_subprocess(*args, **kwargs):
        raise AssertionError("/bin/ps fallback should not run")

    monkeypatch.setattr(replay_capability_module.subprocess, "run", unexpected_subprocess)

    assert replay_process_memory_bytes(99) == 12_345


def test_replay_process_memory_bytes_ignores_transient_ps_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(replay_capability_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        replay_capability_module,
        "_darwin_process_memory_bytes",
        lambda process_id: None,
    )

    def timed_out_subprocess(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=1.0)

    monkeypatch.setattr(replay_capability_module.subprocess, "run", timed_out_subprocess)

    assert replay_process_memory_bytes(99) == 0


def _request(
    skill_root: Path,
    *,
    requirement_status: str = "adapter_bound",
    recorded_value: object = "recorded fixture",
) -> ReplayCapabilityCompileRequest:
    context_path = skill_root.parent / "trajectory_context" / "case-1.json"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_payload = {
        "case_id": "case-1",
        "steps": [
            {"observation": recorded_value},
            {"observation": "recorded fixture a"},
            {"observation": "recorded fixture b"},
        ],
    }
    context_fingerprint = "sha256:" + hashlib.sha256(
        json.dumps(
            context_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    context_path.write_text(
        json.dumps({**context_payload, "fingerprint": context_fingerprint}),
        encoding="utf-8",
    )
    requirement = ReplayCapabilityRequirement(
        requirement_id="requirement-local",
        kind="local_endpoint",
        identifier="http://127.0.0.1:9222",
        case_ids=("case-1",),
        evidence_refs=(f"context:case-1:{context_fingerprint}",),
        status=requirement_status,
    )
    return ReplayCapabilityCompileRequest.create(
        requirements=(requirement,),
        context_snapshots={"case-1": str(context_path)},
        task_inputs={"case-1": {"content": "Connect to the recorded endpoint."}},
        capability_root=skill_root,
        context_fingerprint="sha256:context-set",
    )


def _write_capability_skill(
    root: Path,
    *,
    entrypoint: str = "replay/compiler.py",
    nondeterministic: bool = False,
    invalid_evidence: bool = False,
    mutate_runtime: bool = False,
    undeclared_output: bool = False,
    nested_fixture: bool = False,
) -> Path:
    skill = root / "fixture-skill"
    replay = skill / "replay"
    replay.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: fixture-skill\n---\n# Fixture\n",
        encoding="utf-8",
    )
    (replay / "capability.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld.skill.replay_capability.v1",
                "capability_id": "fixture-service",
                "protocol": "aworld.replay.subprocess.v1",
                "entrypoint": entrypoint,
                "handles": ["local_endpoint"],
                "runtime_files": ["replay/runtime.py"],
            }
        ),
        encoding="utf-8",
    )
    (replay / "runtime.py").write_text(
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "index_path = os.environ.get('AWORLD_REPLAY_RESPONSE_INDEX')\n"
        "index = json.loads(Path(index_path).read_text()) if index_path else {}\n"
        "for record in index.get('records', []):\n"
        "    if record.get('non_empty') is True:\n"
        "        response = record.get('value')\n"
        "        break\n"
        "print('runtime')\n",
        encoding="utf-8",
    )
    if entrypoint == "replay/compiler.py":
        fixture_expression = (
            "('recorded fixture a' if 'compile-a' in args.output "
            "else 'recorded fixture b')"
            if nondeterministic
            else (
                "json.dumps({'wrapper': [{'action_result': "
                "[{'action_name': 'records.query', 'content': "
                "json.dumps({'records': [{'id': 1, 'value': 'recorded'}]})}]}]}, "
                "ensure_ascii=False)"
                if nested_fixture
                else "'recorded fixture'"
            )
        )
        evidence_expression = (
            "['context:case-1:sha256:wrong']"
            if invalid_evidence
            else "request['requirements'][0]['evidence_refs']"
        )
        mutation_statement = (
            "Path(request['capability_root'], 'replay/runtime.py').write_text("
            "\"print('mutated')\\n\", encoding='utf-8')"
            if mutate_runtime
            else ""
        )
        undeclared_statement = (
            "(output / 'undeclared.txt').write_text('extra', encoding='utf-8')"
            if undeclared_output
            else ""
        )
        (replay / "compiler.py").write_text(
            """
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--request', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
request = json.loads(Path(args.request).read_text(encoding='utf-8'))
MUTATION_STATEMENT
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
UNDECLARED_STATEMENT
(output / 'fixture.txt').write_text(FIXTURE_EXPRESSION, encoding='utf-8')
requirement = request['requirements'][0]
result = {
    'schema_version': RESULT_SCHEMA,
    'capability_id': 'fixture-service',
    'deterministic': True,
    'handled_requirements': [requirement['requirement_id']],
    'unhandled_requirements': [],
    'evidence_refs': {
        requirement['requirement_id']: EVIDENCE_EXPRESSION,
    },
    'fixture_evidence_refs': {
        'fixture.txt': request['requirements'][0]['evidence_refs'],
    },
    'fixtures': ['fixture.txt'],
    'endpoint_replacements': {
        requirement['identifier']: 'fixture-http',
    },
    'services': [{
        'service_id': 'fixture-http',
        'requirement_id': requirement['requirement_id'],
        'transport': 'http_fixture',
        'response_fixture': 'fixture.txt',
        'readiness': {'kind': 'tcp', 'timeout_seconds': 2.0},
    }],
}
(output / 'result.json').write_text(
    json.dumps(result, sort_keys=True),
    encoding='utf-8',
)
""".replace("FIXTURE_EXPRESSION", fixture_expression)
            .replace("EVIDENCE_EXPRESSION", evidence_expression)
            .replace("MUTATION_STATEMENT", mutation_statement)
            .replace("UNDECLARED_STATEMENT", undeclared_statement)
            .replace("RESULT_SCHEMA", repr(REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION)),
            encoding="utf-8",
        )
    return skill


def test_discover_capability_inside_skill_root(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path)

    discovered = discover_replay_capability(skill)

    assert discovered is not None
    assert discovered.manifest.capability_id == "fixture-service"
    assert discovered.entrypoint == skill / "replay/compiler.py"
    assert discovered.package_fingerprint.startswith("sha256:")


def test_compile_request_binds_manifest_capability_identity(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path)

    request = _request(skill)

    assert request.capability_id == "fixture-service"
    assert request.to_dict()["capability_id"] == "fixture-service"


def test_discovered_capability_fingerprint_excludes_behavior_guidance(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    initial = discover_replay_capability(skill)

    assert initial is not None
    (skill / "SKILL.md").write_text(
        "---\nname: fixture-skill\n---\n# Updated behavior guidance\n",
        encoding="utf-8",
    )
    (skill / "examples.md").write_text("release-only example\n", encoding="utf-8")
    (skill / "replay" / "diagnostic_notes.md").write_text(
        "non-executable replay notes\n",
        encoding="utf-8",
    )
    (skill / "replay" / "undeclared_proposal.py").write_text(
        "PROPOSAL_ONLY = True\n",
        encoding="utf-8",
    )
    behavior_only = discover_replay_capability(skill)

    assert behavior_only is not None
    assert behavior_only.package_fingerprint == initial.package_fingerprint

    runtime_path = skill / "replay" / "runtime.py"
    runtime_path.write_text(
        runtime_path.read_text(encoding="utf-8") + "\n# capability change\n",
        encoding="utf-8",
    )
    capability_changed = discover_replay_capability(skill)

    assert capability_changed is not None
    assert capability_changed.package_fingerprint != initial.package_fingerprint


@pytest.mark.replay_sandbox
@pytest.mark.parametrize(
    "result_capability_id",
    ("wrong-service", "<wrong-service>"),
)
def test_compile_result_capability_identity_failure_is_typed_for_repair(
    tmp_path: Path,
    result_capability_id: str,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'capability_id': 'fixture-service'",
            f"'capability_id': {result_capability_id!r}",
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError) as error:
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile",
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "compile_result",
            "field_path": "capability_id",
            "rule": "enum",
            "expected": ["fixture-service"],
        }
    ]
    encoded = json.dumps(error.value.details)
    assert result_capability_id not in encoded


@pytest.mark.replay_sandbox
def test_duplicate_compile_result_fixtures_publish_unique_counterexample(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'fixtures': ['fixture.txt']",
            "'fixtures': ['fixture.txt', 'fixture.txt']",
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(
        ReplayCapabilityError,
        match="result fixtures contain duplicates",
    ) as error:
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile",
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "compile_result",
            "field_path": "fixtures",
            "rule": "unique",
            "expected": [],
        }
    ]
    assert error.value.details["schema_field_violation_count"] == 1
    assert error.value.details["allowed_fixture_topologies"] == [
        "unique_path_per_requirement",
        "shared_path_with_merged_provenance",
    ]
    counterexample = error.value.details["counterexample_contracts"][0]
    assert counterexample["constraint"]["field_path"] == "fixtures"
    assert counterexample["required_checks"] == [
        "field_selector_resolves_subject",
        "schema_constraint_accepts_subject",
    ]


@pytest.mark.replay_sandbox
def test_duplicate_requirement_classification_publishes_unique_constraint(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'handled_requirements': [requirement['requirement_id']]",
            (
                "'handled_requirements': [requirement['requirement_id'], "
                "requirement['requirement_id']]"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(
        ReplayCapabilityError,
        match="result requirement lists contain duplicates",
    ) as error:
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile",
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "compile_result",
            "field_path": "handled_requirements",
            "rule": "unique",
            "expected": [],
        }
    ]


@pytest.mark.replay_sandbox
def test_skill_runtime_requires_declared_protocol_probes(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(
        ReplayCapabilityError,
        match="skill runtime service requires protocol_probes",
    ):
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile",
        )


@pytest.mark.replay_sandbox
def test_runtime_required_requirement_rejects_fixture_only_transport(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(
        ReplayCapabilityError,
        match="runtime_required requirement must use skill_runtime",
    ) as error:
        compile_and_freeze_capability(
            capability,
            _request(skill, requirement_status="runtime_required"),
            tmp_path / "compile",
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "compile_result",
            "field_path": (
                "services[*@request.requirement.status:runtime_required].transport"
            ),
            "rule": "enum",
            "expected": ["skill_runtime"],
        }
    ]


@pytest.mark.replay_sandbox
def test_skill_runtime_rejects_readiness_only_protocol_probe(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                "        'protocol_probes': [{\n"
                "            'kind': 'http', 'path': '/health',\n"
                "            'timeout_seconds': 2.0,\n"
                "        }],"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError, match="data-plane protocol probe"):
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile",
        )


@pytest.mark.replay_sandbox
def test_skill_runtime_rejects_readiness_duplicate_protocol_probe(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                "        'protocol_probes': [\n"
                "            {\n"
                "                'kind': 'http', 'path': '/health',\n"
                "                'response_contains': 'recorded fixture',\n"
                "                'timeout_seconds': 2.0,\n"
                "            },\n"
                "            {\n"
                "                'kind': 'http', 'path': '/',\n"
                "                'response_contains': 'recorded fixture',\n"
                "                'timeout_seconds': 2.0,\n"
                "            },\n"
                "        ],"
            ),
        ).replace(
            "'readiness': {'kind': 'tcp', 'timeout_seconds': 2.0}",
            (
                "'readiness': {'kind': 'http', 'path': '/health', "
                "'timeout_seconds': 2.0}"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError) as error:
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile",
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["code"] == "protocol_probe_duplicates_readiness"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "compile_result",
            "field_path": "services[*].protocol_probes[*].path",
            "rule": "enum",
            "expected": ["fixture_derived_data_plane_probe"],
            "value_domain": "source_behavior",
            "required_operations": [
                "keep_readiness_in_readiness_field",
                "declare_distinct_fixture_derived_data_plane_probe",
            ],
            "forbidden_operations": [
                "copy_readiness_endpoint_into_protocol_probes"
            ],
        }
    ]


@pytest.mark.replay_sandbox
def test_skill_runtime_aggregates_independent_protocol_repairs(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                "        'protocol_probes': [\n"
                "            {\n"
                "                'kind': 'http', 'path': '/ready',\n"
                "                'response_contains': 'recorded fixture',\n"
                "                'validate_advertised_websockets': True,\n"
                "            },\n"
                "            {\n"
                "                'kind': 'http', 'path': '/',\n"
                "                'response_contains': 'recorded fixture',\n"
                "            },\n"
                "        ],"
            ),
        ).replace(
            "'readiness': {'kind': 'tcp', 'timeout_seconds': 2.0}",
            (
                "'readiness': {'kind': 'http', 'path': '/ready', "
                "'timeout_seconds': 2.0}"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError) as error:
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile",
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["code"] == "compile_result_constraints_failed"
    assert error.value.details["constraint_failure_codes"] == [
        "advertised_websocket_probe_missing",
        "protocol_probe_duplicates_readiness",
    ]
    constraints = {
        SchemaFieldRepairConstraint.from_dict(item)
        for item in error.value.details["schema_field_constraints"]
    }
    assert constraints == {
        SchemaFieldRepairConstraint(
            schema_layer="compile_result",
            field_path="services[*].protocol_probes[*].path",
            rule="enum",
            expected=("fixture_derived_data_plane_probe",),
            value_domain="source_behavior",
            required_operations=(
                "keep_readiness_in_readiness_field",
                "declare_distinct_fixture_derived_data_plane_probe",
            ),
            forbidden_operations=(
                "copy_readiness_endpoint_into_protocol_probes",
            ),
        ),
        SchemaFieldRepairConstraint(
            schema_layer="compile_result",
            field_path="services[*].protocol_probes",
            rule="enum",
            expected=("advertised_websocket_with_data_plane_probe",),
            value_domain="source_behavior",
            required_operations=(
                "declare_websocket_data_plane_probe",
                "provide_websocket_request_text",
                "provide_fixture_derived_websocket_response",
            ),
            forbidden_operations=(
                "advertise_websocket_without_websocket_probe",
            ),
        ),
    }


@pytest.mark.replay_sandbox
def test_skill_runtime_types_relative_protocol_probe_path(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                "        'protocol_probes': [{\n"
                "            'kind': 'http', 'path': 'task-plane',\n"
                "            'response_contains': 'recorded fixture',\n"
                "        }],"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError) as error:
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile",
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["code"] == "protocol_probe_path_invalid"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "compile_result",
            "field_path": "services[*].protocol_probes[*].path",
            "rule": "starts_with",
            "expected": ["/"],
        }
    ]


@pytest.mark.replay_sandbox
@pytest.mark.parametrize(
    "path",
    (
        "/replay/data/",
        "/replay/data?view=all",
        "/foo/../bar",
        "/foo/%2e%2e/bar",
        "/foo/%5cbar",
    ),
)
def test_skill_runtime_rejects_noncanonical_http_task_entry_path(
    tmp_path: Path,
    path: str,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                "        'protocol_probes': [{\n"
                f"            'kind': 'http', 'path': {path!r},\n"
                "            'response_contains': 'recorded fixture',\n"
                "        }],"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError) as error:
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile",
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["code"] == "protocol_probe_path_not_canonical"


@pytest.mark.replay_sandbox
def test_endpoint_replacement_rejects_runtime_with_data_only_off_task_entry(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                "        'protocol_probes': [{\n"
                "            'kind': 'websocket', 'path': '/replay/data',\n"
                "            'request_text': '{\"op\":\"read\"}',\n"
                "            'response_contains': 'recorded fixture',\n"
                "            'timeout_seconds': 2.0,\n"
                "        }],"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError) as error:
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile",
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["code"] == "endpoint_replacement_entrypoint_missing"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "compile_result",
            "field_path": "services[*@endpoint_replacement].protocol_probes",
            "rule": "contains_all",
            "expected": ["unambiguous_http_task_entry_probe"],
            "value_domain": "source_behavior",
            "required_operations": [
                "declare_one_http_task_entry_probe",
                "serve_fixture_or_discovery_response_at_task_entry",
            ],
            "forbidden_operations": [
                "serve_fixture_only_on_undiscoverable_subpath",
                "make_task_entry_readiness_only",
            ],
        }
    ]


@pytest.mark.replay_sandbox
def test_endpoint_replacement_accepts_declared_non_root_runtime_task_entry(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                "        'protocol_probes': [{\n"
                "            'kind': 'http', 'path': '/replay/data',\n"
                "            'response_contains': 'recorded fixture',\n"
                "            'timeout_seconds': 2.0,\n"
                "        }],"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    frozen = compile_and_freeze_capability(
        capability,
        _request(skill),
        tmp_path / "compile",
    )

    assert frozen.endpoint_replacements == {
        "http://127.0.0.1:9222": "fixture-http"
    }
    assert frozen.services[0].protocol_probes[0].path == "/replay/data"
    assert frozen.services[0].task_entry_path == "/replay/data"


@pytest.mark.replay_sandbox
def test_skill_runtime_accepts_declared_websocket_data_plane_probe(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                "        'protocol_probes': [{\n"
                "            'kind': 'http',\n"
                "            'path': '/',\n"
                "            'timeout_seconds': 2.0,\n"
                "            'response_contains': 'recorded fixture',\n"
                "        }, {\n"
                "            'kind': 'websocket',\n"
                "            'path': '/events',\n"
                "            'timeout_seconds': 2.0,\n"
                "            'request_text': '{\\\"op\\\":\\\"read\\\"}',\n"
                "            'response_contains': 'recorded fixture',\n"
                "        }],"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    frozen = compile_and_freeze_capability(
        capability,
        _request(skill),
        tmp_path / "compile",
    )

    probe = frozen.services[0].protocol_probes[1]
    assert probe.kind == "websocket"
    assert probe.request_text == '{"op":"read"}'
    assert probe.response_contains == "recorded fixture"


@pytest.mark.replay_sandbox
def test_skill_runtime_with_recorded_responses_must_consume_response_index(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path, nested_fixture=True)
    (skill / "replay/runtime.py").write_text(
        "import os\n"
        "os.environ.get('AWORLD_REPLAY_RESPONSE_INDEX')\n"
        "print('runtime that mentions but ignores recorded responses')\n",
        encoding="utf-8",
    )
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                "        'protocol_probes': [{\n"
                "            'kind': 'http',\n"
                "            'path': '/',\n"
                "            'response_contains': 'recorded',\n"
                "        }, {\n"
                "            'kind': 'websocket',\n"
                "            'path': '/events',\n"
                "            'request_text': '{\\\"op\\\":\\\"records.query\\\"}',\n"
                "            'response_contains': 'recorded',\n"
                "        }],"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(
        ReplayCapabilityError,
        match="must consume AWORLD_REPLAY_RESPONSE_INDEX",
    ) as error:
        compile_and_freeze_capability(
            capability,
            _request(skill, recorded_value=_nested_recorded_fixture_value()),
            tmp_path / "compile",
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "runtime",
            "field_path": (
                "environment.AWORLD_REPLAY_RESPONSE_INDEX.consumer"
            ),
            "rule": "enum",
            "expected": ["json_sidecar_record_value_projector"],
            "value_domain": "source_behavior",
            "required_operations": [
                "read_environment_binding_as_path",
                "bind_environment_path_to_json_file_reader",
                "access_records_array",
                "project_record_value_field_directly",
            ],
            "forbidden_operations": [
                "coerce_environment_binding_to_numeric_index",
                "hide_environment_read_behind_zero_arg_return_helper",
                "substitute_raw_fixture_recursive_scan",
            ],
        }
    ]
    assert error.value.details["schema_field_violations"][0]["actual_type"] == (
        "string"
    )
    proof = error.value.details["source_behavior_proofs"][0]
    assert proof["proven"] is False
    assert "bind_environment_path_to_json_file_reader" in proof[
        "missing_operations"
    ]
    assert proof["proof_fingerprint"].startswith("sha256:")


@pytest.mark.parametrize(
    ("source", "accepted"),
    [
        (
            "import json, os\n"
            "from pathlib import Path\n"
            "index_path = os.environ.get('AWORLD_REPLAY_RESPONSE_INDEX')\n"
            "index = json.loads(Path(index_path).read_text())\n"
            "for record in index.get('records', []):\n"
            "    response = record.get('value')\n",
            True,
        ),
        (
            "import json, os\n"
            "def load_index():\n"
            "    index_path = os.getenv('AWORLD_REPLAY_RESPONSE_INDEX')\n"
            "    with open(index_path, encoding='utf-8') as stream:\n"
            "        return json.load(stream)\n"
            "def project(index):\n"
            "    for record in index['records']:\n"
            "        yield record['value']\n",
            True,
        ),
        (
            "import json, os\n"
            "RESPONSE_INDEX_ENV = 'AWORLD_REPLAY_RESPONSE_INDEX'\n"
            "def load_index():\n"
            "    index_path = os.environ.get(RESPONSE_INDEX_ENV)\n"
            "    with open(index_path, encoding='utf-8') as stream:\n"
            "        return json.load(stream)\n"
            "def project(index):\n"
            "    for record in index['records']:\n"
            "        yield record['value']\n",
            True,
        ),
        (
            "import json, os\n"
            "from pathlib import Path\n"
            "def load_json(path):\n"
            "    return json.loads(Path(path).read_text())\n"
            "def project_sidecar(path):\n"
            "    index = load_json(path)\n"
            "    return [record.get('value') for record in index.get('records', [])]\n"
            "def build_response():\n"
            "    index_path = os.environ.get('AWORLD_REPLAY_RESPONSE_INDEX')\n"
            "    return project_sidecar(index_path)\n",
            True,
        ),
        (
            "import json, os\n"
            "index_text = os.environ.get('AWORLD_REPLAY_RESPONSE_INDEX')\n"
            "index = json.loads(index_text)\n"
            "with open('unrelated.json') as stream:\n"
            "    unrelated = stream.read()\n"
            "for record in index.get('records', []):\n"
            "    response = record.get('value')\n",
            False,
        ),
        (
            "import json, os\n"
            "def mention_binding():\n"
            "    return os.environ.get('AWORLD_REPLAY_RESPONSE_INDEX')\n"
            "def read_unrelated():\n"
            "    with open('unrelated.json') as stream:\n"
            "        index = json.load(stream)\n"
            "    return index.get('records')[0].get('value')\n",
            False,
        ),
        (
            "import json, os\n"
            "def read_environment_binding_as_path():\n"
            "    return os.environ.get('AWORLD_REPLAY_RESPONSE_INDEX')\n"
            "def parse_json_object(path):\n"
            "    with open(path, encoding='utf-8') as stream:\n"
            "        return json.load(stream)\n"
            "def project_record_value(record):\n"
            "    return record.get('value')\n"
            "def build_response():\n"
            "    path = read_environment_binding_as_path()\n"
            "    index = parse_json_object(path)\n"
            "    return [project_record_value(record) "
            "for record in index.get('records', [])]\n",
            False,
        ),
        (
            "import json, os\n"
            "def ignore_path(path):\n"
            "    with open('unrelated.json') as stream:\n"
            "        return json.load(stream)\n"
            "def build_response():\n"
            "    index_path = os.environ.get('AWORLD_REPLAY_RESPONSE_INDEX')\n"
            "    index = ignore_path(index_path)\n"
            "    return index.get('records')[0].get('value')\n",
            False,
        ),
    ],
)
def test_response_index_source_behavior_requires_bound_file_reader_dataflow(
    source: str,
    accepted: bool,
) -> None:
    assert (
        replay_capability_module._runtime_consumes_recorded_response_index(source)
        is accepted
    )


def test_response_index_source_behavior_reports_unsupported_state_boundary() -> None:
    source = (
        "import json, os\n"
        "class Runtime:\n"
        "    response_index_path = None\n"
        "def build_response(path):\n"
        "    with open(path, encoding='utf-8') as stream:\n"
        "        index = json.load(stream)\n"
        "    return index.get('records', [])[0].get('value')\n"
        "def main():\n"
        "    local_path = os.environ.get('AWORLD_REPLAY_RESPONSE_INDEX')\n"
        "    Runtime.response_index_path = local_path\n"
        "    return build_response(Runtime.response_index_path)\n"
    )

    proof = replay_capability_module.recorded_response_index_source_behavior_proof(
        source
    )

    assert proof["proven"] is False
    assert proof["operation_status"] == {
        "read_environment_binding_as_path": True,
        "bind_environment_path_to_json_file_reader": False,
        "access_records_array": True,
        "project_record_value_field_directly": True,
    }
    assert proof["unsupported_boundaries"] == [
        {"kind": "attribute_storage", "scope": "main", "line": 10}
    ]


def test_response_index_source_behavior_rejects_stale_loop_record_projection() -> None:
    source = (
        "import json, os\n"
        "def respond():\n"
        "    path = os.getenv('AWORLD_REPLAY_RESPONSE_INDEX')\n"
        "    with open(path, encoding='utf-8') as stream:\n"
        "        index = json.load(stream)\n"
        "    records = index['records']\n"
        "    selected = None\n"
        "    for record in records:\n"
        "        if record.get('protocol_eligible') is True:\n"
        "            selected = record\n"
        "            break\n"
        "    if selected is None:\n"
        "        return None\n"
        "    return record['value']\n"
    )

    proof = replay_capability_module.recorded_response_index_source_behavior_proof(
        source
    )

    assert proof["proven"] is False
    assert proof["operation_status"]["access_records_array"] is True
    assert proof["operation_status"]["project_record_value_field_directly"] is False
    assert proof["projection_proofs"][0]["stale_record_projection_lines"] == [14]
    assert proof["topology"]["value_projection_proven"] is False


def test_response_index_source_behavior_rejects_disconnected_key_mentions() -> None:
    source = (
        "import json, os\n"
        "def load_sidecar():\n"
        "    path = os.getenv('AWORLD_REPLAY_RESPONSE_INDEX')\n"
        "    with open(path, encoding='utf-8') as stream:\n"
        "        return json.load(stream)\n"
        "def unrelated():\n"
        "    payload = {'records': [{'value': 'not-sidecar-derived'}]}\n"
        "    return payload['records'][0]['value']\n"
    )

    proof = replay_capability_module.recorded_response_index_source_behavior_proof(
        source
    )

    assert proof["proven"] is False
    assert proof["operation_status"]["access_records_array"] is False
    assert proof["operation_status"]["project_record_value_field_directly"] is False


def test_response_index_source_behavior_rejects_reassigned_environment_key() -> None:
    source = (
        "import json, os\n"
        "RESPONSE_INDEX_ENV = 'AWORLD_REPLAY_RESPONSE_INDEX'\n"
        "RESPONSE_INDEX_ENV = os.getenv('REPLAY_INDEX_KEY', 'other')\n"
        "def load_index():\n"
        "    index_path = os.environ.get(RESPONSE_INDEX_ENV)\n"
        "    with open(index_path, encoding='utf-8') as stream:\n"
        "        return json.load(stream)\n"
        "def project(index):\n"
        "    return index['records'][0]['value']\n"
    )

    proof = replay_capability_module.recorded_response_index_source_behavior_proof(
        source
    )

    assert proof["proven"] is False
    assert proof["operation_status"]["read_environment_binding_as_path"] is False
    assert (
        proof["operation_status"]["bind_environment_path_to_json_file_reader"]
        is False
    )


def test_response_index_source_behavior_fingerprint_is_topology_stable() -> None:
    def source(class_name: str, local_name: str) -> str:
        return (
            "import json, os\n"
            f"class {class_name}:\n"
            "    path = None\n"
            "def respond(path):\n"
            "    with open(path) as stream:\n"
            "        index = json.load(stream)\n"
            "    return index['records'][0]['value']\n"
            "def main():\n"
            f"    {local_name} = os.getenv('AWORLD_REPLAY_RESPONSE_INDEX')\n"
            f"    {class_name}.path = {local_name}\n"
            f"    return respond({class_name}.path)\n"
        )

    first = replay_capability_module.recorded_response_index_source_behavior_proof(
        source("Runtime", "index_path")
    )
    renamed = replay_capability_module.recorded_response_index_source_behavior_proof(
        source("Handler", "sidecar_path")
    )

    assert first["proven"] is False
    assert renamed["proven"] is False
    assert first["proof_fingerprint"] == renamed["proof_fingerprint"]


@pytest.mark.replay_sandbox
def test_frozen_verification_rejects_recorded_response_index_tampering(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path, nested_fixture=True)
    capability = discover_replay_capability(skill)
    assert capability is not None
    frozen = compile_and_freeze_capability(
        capability,
        _request(skill, recorded_value=_nested_recorded_fixture_value()),
        tmp_path / "compile",
    )
    fixture = Path(frozen.frozen_root) / "fixtures/fixture.txt"
    sidecar = fixture.with_suffix(".responses.json")
    index = json.loads(sidecar.read_text(encoding="utf-8"))
    index["records"][0]["value"] = "tampered"
    sidecar.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(
        ReplayCapabilityError,
        match="response index does not match its fixture",
    ):
        verify_frozen_replay_capability(frozen)


@pytest.mark.replay_sandbox
@pytest.mark.parametrize(
    ("probe_count", "accepted"),
    [(16, True), (17, False)],
)
def test_skill_runtime_protocol_probe_limit_is_bounded_but_covers_observed_operations(
    tmp_path: Path,
    probe_count: int,
    accepted: bool,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    probe = {
        "kind": "websocket",
        "path": "/events",
        "request_text": '{"op":"read"}',
        "response_contains": "recorded fixture",
    }
    serialized_probes = repr(
        [
            {
                "kind": "http",
                "path": "/",
                "response_contains": "recorded fixture",
            }
        ]
        + [probe] * (probe_count - 1)
    )
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                f"        'protocol_probes': {serialized_probes},"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    if accepted:
        frozen = compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile",
        )
        assert len(frozen.services[0].protocol_probes) == probe_count
    else:
        with pytest.raises(
            ReplayCapabilityError,
            match="protocol_probes cannot exceed 16 items",
        ):
            compile_and_freeze_capability(
                capability,
                _request(skill),
                tmp_path / "compile",
            )


@pytest.mark.replay_sandbox
def test_skill_runtime_advertised_websocket_validation_requires_websocket_data_plane_probe(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                "        'protocol_probes': [{\n"
                "            'kind': 'http', 'path': '/json/version',\n"
                "            'timeout_seconds': 2.0,\n"
                "            'response_contains': 'recorded fixture',\n"
                "            'validate_advertised_websockets': True,\n"
                "        }],"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(
        ReplayCapabilityError,
        match="advertised WebSocket requires a websocket data-plane protocol probe",
    ) as error:
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile",
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["code"] == "advertised_websocket_probe_missing"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "compile_result",
            "field_path": "services[*].protocol_probes",
            "rule": "enum",
            "expected": ["advertised_websocket_with_data_plane_probe"],
            "value_domain": "source_behavior",
            "required_operations": [
                "declare_websocket_data_plane_probe",
                "provide_websocket_request_text",
                "provide_fixture_derived_websocket_response",
            ],
            "forbidden_operations": [
                "advertise_websocket_without_websocket_probe"
            ],
        }
    ]


@pytest.mark.replay_sandbox
def test_skill_runtime_allows_structural_http_discovery_expectation_with_fixture_backed_websocket_probe(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                "        'protocol_probes': [{\n"
                "            'kind': 'http', 'path': '/json/version',\n"
                "            'response_contains': 'Protocol-Version',\n"
                "            'validate_advertised_websockets': True,\n"
                "        }, {\n"
                "            'kind': 'websocket', 'path': '/devtools/browser',\n"
                "            'request_text': '{\"id\":1,\"method\":\"Runtime.evaluate\"}',\n"
                "            'response_contains': 'recorded fixture',\n"
                "        }],"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    frozen = compile_and_freeze_capability(
        capability,
        _request(skill),
        tmp_path / "compile",
    )

    assert [probe.kind for probe in frozen.services[0].protocol_probes] == [
        "http",
        "websocket",
    ]


@pytest.mark.replay_sandbox
def test_skill_runtime_rejects_data_plane_expectation_not_in_fixture(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                "        'protocol_probes': [{\n"
                "            'kind': 'websocket',\n"
                "            'path': '/events',\n"
                "            'request_text': '{\\\"op\\\":\\\"read\\\"}',\n"
                "            'response_contains': 'invented response',\n"
                "        }],"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(
        ReplayCapabilityError,
        match="response_contains must be derived from the declared fixture",
    ) as error:
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile",
        )

    message = str(error.value)
    assert "kind=websocket" in message
    assert "path=/events" in message
    assert "expected_sha256=" in message
    assert "expected_bytes=17" in message
    assert "invented response" not in message
    assert error.value.code == "protocol_probe_not_fixture_derived"
    assert error.value.details["fixture_probe_violation_count"] == 1
    assert error.value.details["fixture_probe_constraints"] == [
        {
            "requirement_id": "requirement-local",
            "kind": "websocket",
            "path": "/events",
            "max_response_chars": 4096,
        }
    ]


def test_fixture_probe_compile_error_aggregates_all_requirement_shapes(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "compiled"
    fixtures = ("fixtures/object.json", "fixtures/list.json")
    for fixture, payload in zip(
        fixtures,
        ('{"value":"recorded-a"}', '["recorded-b"]'),
        strict=True,
    ):
        path = output_root / fixture
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    raw_services = [
        {
            "service_id": f"service-{index}",
            "requirement_id": f"requirement-{index}",
            "transport": "skill_runtime",
            "response_fixture": fixture,
            "runtime_entrypoint": "replay/runtime.py",
            "readiness": {"kind": "http", "path": "/healthz"},
            "protocol_probes": [
                {
                    "kind": "http",
                    "path": "/data",
                    "response_contains": f"metadata-wrapper-{index}",
                }
            ],
        }
        for index, fixture in enumerate(fixtures, start=1)
    ]

    with pytest.raises(ReplayCapabilityError) as error:
        replay_capability_module._parse_services(
            raw_services,
            output_root=output_root,
            fixtures=fixtures,
            runtime_files=("replay/runtime.py",),
            handled_requirements={"requirement-1", "requirement-2"},
            fixture_evidence_refs={
                fixture: (f"evidence-{index}",)
                for index, fixture in enumerate(fixtures, start=1)
            },
            requirement_evidence_refs={
                f"requirement-{index}": (f"evidence-{index}",)
                for index in (1, 2)
            },
        )

    assert error.value.code == "protocol_probe_not_fixture_derived"
    assert error.value.details["fixture_probe_violation_count"] == 2
    constraints = error.value.details["fixture_probe_constraints"]
    assert [item["requirement_id"] for item in constraints] == [
        "requirement-1",
        "requirement-2",
    ]


def test_service_schema_error_aggregates_all_invalid_field_instances(
    tmp_path: Path,
) -> None:
    raw_services = [
        {
            "service_id": f"service-{index}",
            "requirement_id": f"requirement-{index}",
            "transport": f"private-invalid-{index}",
            "response_fixture": f"fixtures/{index}.json",
            "readiness": {"kind": "http", "path": "/healthz"},
        }
        for index in (1, 2)
    ]

    with pytest.raises(
        ReplayCapabilityError,
        match="unsupported replay service transport",
    ) as error:
        replay_capability_module._parse_services(
            raw_services,
            output_root=tmp_path,
            fixtures=("fixtures/1.json", "fixtures/2.json"),
            runtime_files=(),
            handled_requirements={"requirement-1", "requirement-2"},
            fixture_evidence_refs={
                "fixtures/1.json": ("evidence-1",),
                "fixtures/2.json": ("evidence-2",),
            },
            requirement_evidence_refs={
                "requirement-1": ("evidence-1",),
                "requirement-2": ("evidence-2",),
            },
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["schema_field_violation_count"] == 2
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "compile_result",
            "field_path": "services[*].transport",
            "rule": "enum",
            "expected": ["http_fixture", "skill_runtime", "tcp_fixture"],
        }
    ]
    encoded = json.dumps(error.value.details)
    assert "private-invalid-1" not in encoded
    assert "private-invalid-2" not in encoded


def test_manifest_schema_enum_error_is_a_typed_field_constraint() -> None:
    with pytest.raises(ReplayCapabilityError) as error:
        replay_capability_module._parse_manifest(
            {
                "schema_version": "invalid-schema",
                "capability_id": "generic.replay",
                "protocol": "invalid-protocol",
                "entrypoint": "replay/compiler.py",
                "handles": ["local_endpoint"],
            }
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "manifest",
            "field_path": "schema_version",
            "rule": "enum",
            "expected": ["aworld.skill.replay_capability.v1"],
        }
    ]


@pytest.mark.parametrize(
    ("raw", "field_path", "rule", "expected"),
    [
        ([], "fixture_evidence_refs", "type", ["object"]),
        ({}, "fixtures[*].evidence_refs", "required", []),
    ],
)
def test_fixture_provenance_shape_errors_are_typed_schema_constraints(
    tmp_path: Path,
    raw: object,
    field_path: str,
    rule: str,
    expected: list[str],
) -> None:
    skill = _write_capability_skill(tmp_path)

    with pytest.raises(ReplayCapabilityError) as error:
        replay_capability_module._validate_fixture_provenance(
            raw,
            fixtures=("fixture.json",),
            requirement_evidence_refs={"requirement-1": ("evidence-1",)},
            request=_request(skill),
            output_root=tmp_path / "output",
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "compile_result",
            "field_path": field_path,
            "rule": rule,
            "expected": expected,
        }
    ]


@pytest.mark.replay_sandbox
def test_skill_runtime_accepts_semantically_decoded_fixture_container_expectation(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    fixture_value = {
        "action_result": {
            "content": {"value": "recorded fixture"},
        }
    }
    fixture_payload = json.dumps(
        fixture_value,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_container = json.dumps(
        {"value": "recorded fixture"},
        ensure_ascii=False,
        indent=2,
    )
    compiler_source = compiler_path.read_text(encoding="utf-8").replace(
        "'recorded fixture'",
        repr(fixture_payload),
        1,
    )
    compiler_path.write_text(
        compiler_source.replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                "        'protocol_probes': [{\n"
                "            'kind': 'http',\n"
                "            'path': '/',\n"
                "            'response_contains': 'recorded fixture',\n"
                "        }, {\n"
                "            'kind': 'websocket',\n"
                "            'path': '/events',\n"
                "            'request_text': '{\\\"op\\\":\\\"read\\\"}',\n"
                f"            'response_contains': {expected_container!r},\n"
                "        }],"
            ),
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    frozen = compile_and_freeze_capability(
        capability,
        _request(skill, recorded_value=fixture_value),
        tmp_path / "compile",
    )

    probe = frozen.services[0].protocol_probes[1]
    assert probe.response_contains == "recorded fixture"
    assert probe.response_record_id is not None
    assert probe.response_record_id.startswith("response-record-")


def test_materializes_bounded_read_only_evidence_derivation_catalog(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    request = _request(skill)

    catalog = materialize_replay_evidence_derivations(
        request,
        tmp_path / "trajectory_context" / "evidence_derivations",
    )

    evidence_ref = request.requirements[0].evidence_refs[0]
    assert evidence_ref in catalog
    entries = catalog[evidence_ref]
    assert entries
    assert any(Path(item["path"]).read_bytes() == b"recorded fixture" for item in entries)
    assert all(str(item["sha256"]).startswith("sha256:") for item in entries)
    assert all(Path(item["path"]).stat().st_mode & 0o222 == 0 for item in entries)

    enriched = ReplayCapabilityCompileRequest.create(
        requirements=request.requirements,
        context_snapshots=request.context_snapshots,
        task_inputs=request.task_inputs,
        capability_root=skill,
        context_fingerprint=request.context_fingerprint,
        capability_package_fingerprint=request.capability_package_fingerprint,
        evidence_derivations=catalog,
    )
    assert enriched.evidence_derivations[evidence_ref] == entries
    assert enriched.request_fingerprint != request.request_fingerprint


def test_compile_request_identity_ignores_cycle_local_paths(
    tmp_path: Path,
) -> None:
    first_skill = _write_capability_skill(tmp_path / "cycle-008")
    second_skill = _write_capability_skill(tmp_path / "cycle-009")

    first = _request(first_skill)
    second = _request(second_skill)

    assert first.capability_root != second.capability_root
    assert first.context_snapshots != second.context_snapshots
    assert first.capability_package_fingerprint == (
        second.capability_package_fingerprint
    )
    assert first.request_fingerprint == second.request_fingerprint


def test_freeze_rejects_invalid_python_runtime_with_typed_counterexample(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    (skill / "replay" / "runtime.py").write_text(
        "value = 0\n"
        "def handle():\n"
        "    global value\n"
        "    value += 1\n"
        "    global value\n",
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError) as error:
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile-invalid-runtime",
        )

    assert error.value.code == "runtime_python_syntax_invalid"
    assert error.value.details["source_path"] == "replay/runtime.py"
    assert error.value.details["syntax_kind"] == (
        "global_declaration_after_use"
    )
    counterexample = error.value.details["counterexample_contracts"][0]
    assert counterexample["schema_version"] == (
        "aworld.self_evolve.python_source_counterexample.v1"
    )
    assert counterexample["owner"] == "candidate"
    assert counterexample["required_action"].startswith("declare the global once")


def test_compile_rejects_invalid_python_compiler_with_typed_counterexample(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler = skill / "replay" / "compiler.py"
    compiler.write_text(
        compiler.read_text(encoding="utf-8").replace(
            "output = Path(args.output)\n",
            "output = Path(args.output)\\    invalid = True\n",
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError) as error:
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile-invalid-compiler",
        )

    assert error.value.code == "compiler_python_syntax_invalid"
    assert error.value.details["source_path"] == "replay/compiler.py"
    assert error.value.details["syntax_kind"] == "python_compile_invalid"
    counterexample = error.value.details["counterexample_contracts"][0]
    assert counterexample["code"] == "compiler_python_syntax_invalid"
    assert counterexample["owner"] == "candidate"
    assert "Python compiler" in counterexample["required_action"]


def test_recorded_response_index_reconstructs_nested_operation_payloads() -> None:
    fixture = {
        "wrapper": [
            {
                "state": {
                    "action_result": [
                        {
                            "action_name": "records.query",
                            "content": json.dumps(
                                {"records": [{"id": 1, "value": "recorded"}]}
                            ),
                        }
                    ]
                }
            }
        ]
    }

    index = _build_recorded_response_index(
        json.dumps(fixture, ensure_ascii=False).encode("utf-8")
    )

    assert index["schema_version"] == (
        "aworld.self_evolve.recorded_response_index.v1"
    )
    assert index["operations"] == ["records.query"]
    assert index["records"]
    assert any(
        record.get("value") == {"records": [{"id": 1, "value": "recorded"}]}
        for record in index["records"]
    )
    assert any(
        record.get("value") == [{"id": 1, "value": "recorded"}]
        and record.get("shape") == "array"
        for record in index["records"]
    )
    assert all(record["non_empty"] is True for record in index["records"])


def test_recorded_response_index_assigns_stable_identity_and_exact_assertion() -> None:
    raw = json.dumps(
        {
            "action_result": {
                "content": {
                    "oversized": "x" * 5000,
                    "bounded": "canonical recorded response",
                }
            }
        }
    ).encode("utf-8")

    first = _build_recorded_response_index(raw)
    second = _build_recorded_response_index(raw)
    selected = first["records"][0]

    assert selected["record_id"].startswith("response-record-")
    assert selected["record_id"] == second["records"][0]["record_id"]
    assert selected["canonical_probe_assertion"] == "canonical recorded response"
    assert selected["selector_policy"] == "framework_recorded_response_v1"
    assert len(selected["canonical_probe_assertion"]) <= 4096


def test_framework_replaces_truncated_compiler_assertion_with_exact_record() -> None:
    fixture_bytes = json.dumps(
        {
            "action_result": {
                "content": {
                    "oversized": "x" * 5000,
                    "bounded": "canonical recorded response",
                }
            }
        }
    ).encode("utf-8")
    compiler_probe = replay_capability_module.ReplayProtocolProbe(
        kind="tcp",
        timeout_seconds=1.0,
        request_text='{ "method": "records.query" }',
        response_contains="x" * 4096,
    )

    (bound_probe,) = replay_capability_module._framework_bind_protocol_probes(
        (compiler_probe,),
        fixture_bytes=fixture_bytes,
    )

    assert bound_probe.response_contains == "canonical recorded response"
    assert bound_probe.response_record_id is not None
    assert bound_probe.response_contains != compiler_probe.response_contains


def test_recorded_response_index_aliases_declared_probe_to_non_empty_value() -> None:
    fixture = {
        "wrapper": [
            {
                "action_result": [
                    {
                        "action_name": "read_file",
                        "content": json.dumps({"tweets": [{"id": "1"}]}),
                    }
                ]
            }
        ]
    }

    index = _build_recorded_response_index(
        json.dumps(fixture, ensure_ascii=False).encode("utf-8"),
        observed_operations=("Runtime.evaluate",),
    )

    assert "Runtime.evaluate" in index["operations"]
    aliases = [
        record
        for record in index["records"]
        if record.get("operation") == "Runtime.evaluate"
    ]
    assert aliases
    assert aliases[0]["derived_operation"] is True
    assert aliases[0]["non_empty"] is True
    assert aliases[0]["value"] == {"tweets": [{"id": "1"}]}


def test_recorded_response_index_prioritizes_transport_ready_decoded_records() -> None:
    fixture = {
        "wrapper": [
            {
                "action_result": [
                    {
                        "action_name": "records.query",
                        "content": '{"truncated":"' + ("x" * 8192),
                    }
                ]
            },
            {
                "action_result": [
                    {
                        "action_name": "records.query",
                        "content": json.dumps(
                            {
                                "message": "recorded response",
                                "items": [{"id": 1}],
                            }
                        ),
                    }
                ]
            },
        ]
    }

    index = _build_recorded_response_index(
        json.dumps(fixture, ensure_ascii=False).encode("utf-8")
    )

    first_non_empty = next(
        record
        for record in index["records"]
        if record.get("non_empty") is True
    )
    assert first_non_empty["value"] == {
        "message": "recorded response",
        "items": [{"id": 1}],
    }
    assert first_non_empty["protocol_eligible"] is True
    assert first_non_empty["transport_ready"] is True
    assert any(
        isinstance(record.get("value"), str)
        and record["value"].startswith('{"truncated":')
        and record["protocol_eligible"] is False
        for record in index["records"]
    )
    assert [record["ordinal"] for record in index["records"]] != list(
        range(len(index["records"]))
    )


def test_recorded_response_index_prefers_semantically_richer_transport_record() -> None:
    fixture = {
        "wrapper": [
            {
                "action_result": [
                    {
                        "action_name": "records.query",
                        "content": {
                            "status": "SUCCESS",
                            "command": "fetch content and save it to a local path",
                            "output": "112 <LOCAL_PATH>",
                        },
                    }
                ]
            },
            {
                "action_result": [
                    {
                        "action_name": "records.query",
                        "content": {
                            "body_text": "task-bearing content " * 400,
                            "source": "recorded artifact",
                        },
                    }
                ]
            },
        ]
    }

    index = _build_recorded_response_index(
        json.dumps(fixture, ensure_ascii=False).encode("utf-8")
    )

    ready = [
        record
        for record in index["records"]
        if record["transport_ready"] is True
        and record.get("operation") == "records.query"
    ]
    assert len(ready) >= 2
    assert ready[0]["value"]["body_text"].startswith("task-bearing content")
    assert ready[0]["semantic_payload_score"] > ready[1]["semantic_payload_score"]


@pytest.mark.replay_sandbox
def test_freeze_places_operation_index_next_to_nested_fixture(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path, nested_fixture=True)
    capability = discover_replay_capability(skill)
    assert capability is not None
    fixture_value = json.dumps(
        {
            "wrapper": [
                {
                    "action_result": [
                        {
                            "action_name": "records.query",
                            "content": json.dumps(
                                {"records": [{"id": 1, "value": "recorded"}]},
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }
            ]
        },
        ensure_ascii=False,
    )

    frozen = compile_and_freeze_capability(
        capability,
        _request(skill, recorded_value=fixture_value),
        tmp_path / "compile",
    )

    fixture = Path(frozen.frozen_root) / "fixtures" / "fixture.txt"
    sidecar = fixture.with_suffix(".responses.json")
    assert sidecar.is_file()
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_payload["operations"] == ["records.query"]
    assert sidecar_payload["records"][0]["non_empty"] is True


@pytest.mark.replay_sandbox
def test_freeze_rejects_sidecar_runtime_with_unrecorded_selected_fixture(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler_path = skill / "replay/compiler.py"
    compiler_path.write_text(
        compiler_path.read_text(encoding="utf-8").replace(
            "'transport': 'http_fixture',",
            (
                "'transport': 'skill_runtime',\n"
                "        'runtime_entrypoint': 'replay/runtime.py',\n"
                "        'protocol_probes': [{\n"
                "            'kind': 'http',\n"
                "            'path': '/',\n"
                "            'timeout_seconds': 2.0,\n"
                "            'response_contains': 'recorded fixture',\n"
                "        }],"
            ),
        ),
        encoding="utf-8",
    )
    base_request = _request(skill)
    evidence_ref = base_request.requirements[0].evidence_refs[0]
    rich_fixture = tmp_path / "rich-recorded.json"
    rich_fixture.write_text(
        json.dumps(
            {
                "action_result": [
                    {
                        "action_name": "records.query",
                        "content": {"value": "authoritative response"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    rich_sidecar = tmp_path / "rich-recorded.responses.json"
    rich_sidecar.write_text('{"records": [{"value": "authoritative response"}]}')
    request = ReplayCapabilityCompileRequest.create(
        requirements=base_request.requirements,
        context_snapshots=base_request.context_snapshots,
        task_inputs=base_request.task_inputs,
        capability_root=skill,
        context_fingerprint=base_request.context_fingerprint,
        evidence_derivations={
            evidence_ref: (
                {
                    "path": str(rich_fixture),
                    "byte_length": rich_fixture.stat().st_size,
                    "response_index_path": str(rich_sidecar),
                    "response_record_count": 1,
                },
            )
        },
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError) as error:
        compile_and_freeze_capability(
            capability,
            request,
            tmp_path / "compile",
        )

    assert error.value.code == "recorded_response_fixture_unselected"
    assert error.value.details["affected_service_ids"] == ["fixture-http"]
    assert error.value.details["available_recorded_source_count"] == 1
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "compiler",
            "field_path": "evidence_derivations[*].response_index_path",
            "rule": "enum",
            "expected": ["recorded_response_source"],
            "value_domain": "source_behavior",
            "required_operations": [
                "restrict_to_positive_recorded_response_sources",
                "prefer_maximum_response_record_count",
            ],
            "forbidden_operations": [
                "rank_all_sources_by_minimum_byte_length_first"
            ],
        }
    ]


def test_discovery_returns_none_without_manifest(tmp_path: Path) -> None:
    skill = tmp_path / "plain-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Plain\n", encoding="utf-8")

    assert discover_replay_capability(skill) is None


def test_discovery_rejects_entrypoint_escape(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path, entrypoint="../outside.py")
    (skill / "outside.py").write_text("print('bad')\n", encoding="utf-8")

    with pytest.raises(ReplayCapabilityError, match="inside skill root"):
        discover_replay_capability(skill)


@pytest.mark.replay_sandbox
def test_compile_and_freeze_capability_is_deterministic(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path)
    capability = discover_replay_capability(skill)
    assert capability is not None

    frozen = compile_and_freeze_capability(
        capability,
        _request(skill),
        tmp_path / "artifacts",
    )

    assert frozen.ready is True
    assert frozen.handled_requirements == ("requirement-local",)
    assert frozen.unhandled_requirements == ()
    assert frozen.endpoint_replacements == {
        "http://127.0.0.1:9222": "fixture-http"
    }
    assert Path(frozen.frozen_root, "fixtures", "fixture.txt").read_text() == (
        "recorded fixture"
    )
    assert Path(frozen.frozen_root, "runtime", "replay/runtime.py").is_file()
    assert frozen.fingerprint.startswith("sha256:")
    manifest = json.loads(
        Path(frozen.frozen_root, "frozen_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert "task_entry_path" not in manifest["services"][0]
    verify_frozen_replay_capability(frozen)


@pytest.mark.replay_sandbox
def test_compile_normalizes_requirement_id_endpoint_replacement_keys(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler = skill / "replay/compiler.py"
    compiler.write_text(
        compiler.read_text(encoding="utf-8").replace(
            "requirement['identifier']: 'fixture-http'",
            "requirement['requirement_id']: 'fixture-http'",
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    frozen = compile_and_freeze_capability(
        capability,
        _request(skill),
        tmp_path / "artifacts",
    )

    assert frozen.endpoint_replacements == {
        "http://127.0.0.1:9222": "fixture-http"
    }


@pytest.mark.replay_sandbox
def test_compile_infers_single_service_endpoint_replacement(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler = skill / "replay/compiler.py"
    compiler.write_text(
        compiler.read_text(encoding="utf-8").replace(
            "'endpoint_replacements': {\n        requirement['identifier']: 'fixture-http',\n    },",
            "'endpoint_replacements': {},",
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    frozen = compile_and_freeze_capability(
        capability,
        _request(skill),
        tmp_path / "artifacts",
    )

    assert frozen.endpoint_replacements == {
        "http://127.0.0.1:9222": "fixture-http"
    }


@pytest.mark.replay_sandbox
def test_compile_accepts_unused_evidence_backed_fixture(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler = skill / "replay/compiler.py"
    source = compiler.read_text(encoding="utf-8")
    source = source.replace(
        "(output / 'fixture.txt').write_text('recorded fixture', encoding='utf-8')",
        "(output / 'fixture.txt').write_text('recorded fixture', encoding='utf-8')\n"
        "(output / 'extra.txt').write_text('recorded fixture', encoding='utf-8')",
    ).replace(
        "'fixture.txt': request['requirements'][0]['evidence_refs'],",
        "'fixture.txt': request['requirements'][0]['evidence_refs'],\n"
        "        'extra.txt': request['requirements'][0]['evidence_refs'],",
    ).replace(
        "'fixtures': ['fixture.txt'],",
        "'fixtures': ['fixture.txt', 'extra.txt'],",
    )
    compiler.write_text(source, encoding="utf-8")
    capability = discover_replay_capability(skill)
    assert capability is not None

    frozen = compile_and_freeze_capability(
        capability,
        _request(skill),
        tmp_path / "artifacts",
    )

    assert {item.path for item in frozen.fixtures} == {"fixture.txt", "extra.txt"}


@pytest.mark.replay_sandbox
def test_double_compile_rejects_different_fixture_hashes(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path, nondeterministic=True)
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError, match="non-deterministic"):
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "artifacts",
        )


@pytest.mark.replay_sandbox
def test_compile_rejects_unrecorded_evidence_reference(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path, invalid_evidence=True)
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError, match="evidence reference"):
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "artifacts",
        )


@pytest.mark.replay_sandbox
def test_compile_rejects_fixture_bytes_not_present_in_cited_context(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler = skill / "replay/compiler.py"
    compiler.write_text(
        compiler.read_text(encoding="utf-8").replace(
            "(output / 'fixture.txt').write_text('recorded fixture'",
            "(output / 'fixture.txt').write_text('invented fixture'",
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError, match="not directly derived"):
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "artifacts",
        )


@pytest.mark.replay_sandbox
def test_compile_recomputes_context_snapshot_fingerprint(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path)
    compiler = skill / "replay/compiler.py"
    compiler.write_text(
        compiler.read_text(encoding="utf-8").replace(
            "(output / 'fixture.txt').write_text('recorded fixture'",
            "(output / 'fixture.txt').write_text('invented fixture'",
        ),
        encoding="utf-8",
    )
    capability = discover_replay_capability(skill)
    assert capability is not None
    request = _request(skill)
    context_path = Path(request.context_snapshots["case-1"])
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context["steps"].append({"observation": "invented fixture"})
    context_path.write_text(json.dumps(context), encoding="utf-8")

    with pytest.raises(ReplayCapabilityError, match="fingerprint verification"):
        compile_and_freeze_capability(
            capability,
            request,
            tmp_path / "artifacts",
        )


@pytest.mark.replay_sandbox
def test_compile_rejects_capability_that_mutates_its_package(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path, mutate_runtime=True)
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError, match="compile failed|package changed"):
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "artifacts",
        )


@pytest.mark.replay_sandbox
def test_compile_rejects_undeclared_output_files(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path, undeclared_output=True)
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError, match="undeclared output") as error:
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "artifacts",
        )

    assert error.value.code == "schema_field_validation_failed"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "compiler_output",
            "field_path": "files[*]",
            "rule": "enum",
            "expected": ["result.json", "declared_fixture"],
        }
    ]


@pytest.mark.replay_sandbox
def test_frozen_capability_verification_rejects_runtime_tampering(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    capability = discover_replay_capability(skill)
    assert capability is not None
    frozen = compile_and_freeze_capability(
        capability,
        _request(skill),
        tmp_path / "artifacts",
    )
    runtime = Path(frozen.frozen_root) / "runtime/replay/runtime.py"
    runtime.write_text("print('tampered')\n", encoding="utf-8")

    with pytest.raises(ReplayCapabilityError, match="file changed"):
        verify_frozen_replay_capability(frozen)


def test_platform_sandbox_fails_closed_by_default_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import aworld.self_evolve.replay_capability as replay_capability_module

    monkeypatch.setattr(replay_capability_module.sys, "platform", "unsupported")

    with pytest.raises(ReplayCapabilityError, match="requires an available platform sandbox"):
        build_replay_sandboxed_command(
            ["python", "compiler.py"],
            read_roots=(tmp_path,),
            writable_roots=(tmp_path,),
            allow_loopback=False,
        )


def test_resource_limited_command_applies_limits_after_exec() -> None:
    command = build_replay_resource_limited_command(
        [sys.executable, "-I", "-c", "raise SystemExit(0)"],
        max_file_bytes=1024 * 1024,
        max_memory_bytes=256 * 1024 * 1024,
        cpu_seconds=5,
    )

    assert command[:3] == [sys.executable, "-I", "-c"]
    assert subprocess.run(command, check=False, timeout=10).returncode == 0


def test_replay_service_sandbox_does_not_allow_loopback_outbound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import aworld.self_evolve.replay_capability as replay_capability_module

    monkeypatch.setattr(replay_capability_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        replay_capability_module.shutil,
        "which",
        lambda _name: "/usr/bin/sandbox-exec",
    )

    read_root = tmp_path / "read"
    writable_root = tmp_path / "write"
    command = build_replay_sandboxed_command(
        ["python", "fixture_service.py"],
        read_roots=(read_root,),
        writable_roots=(writable_root,),
        allow_loopback=True,
    )

    assert "network-bind" in command[2]
    assert "network-inbound" in command[2]
    assert "network-outbound" not in command[2]
    resolved_write = str(writable_root.resolve())
    assert f'(allow file-read* (subpath "{resolved_write}"))' in command[2]
    assert f'(allow file-write* (subpath "{resolved_write}"))' in command[2]
