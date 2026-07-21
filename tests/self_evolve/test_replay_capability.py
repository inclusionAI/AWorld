from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from aworld.self_evolve import replay_capability as replay_capability_module
from aworld.self_evolve.replay_adaptation import ReplayCapabilityRequirement
from aworld.self_evolve.replay_capability import (
    REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION,
    ReplayCapabilityCompileRequest,
    ReplayCapabilityError,
    _build_recorded_response_index,
    build_replay_sandboxed_command,
    compile_and_freeze_capability,
    discover_replay_capability,
    materialize_replay_evidence_derivations,
    replay_process_memory_bytes,
    verify_frozen_replay_capability,
)


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


def test_runtime_required_requirement_rejects_fixture_only_transport(
    tmp_path: Path,
) -> None:
    skill = _write_capability_skill(tmp_path)
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(
        ReplayCapabilityError,
        match="runtime_required requirement must use skill_runtime",
    ):
        compile_and_freeze_capability(
            capability,
            _request(skill, requirement_status="runtime_required"),
            tmp_path / "compile",
        )


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

    probe = frozen.services[0].protocol_probes[0]
    assert probe.kind == "websocket"
    assert probe.request_text == '{"op":"read"}'
    assert probe.response_contains == "recorded fixture"


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
    ):
        compile_and_freeze_capability(
            capability,
            _request(skill, recorded_value=_nested_recorded_fixture_value()),
            tmp_path / "compile",
        )


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
    serialized_probes = repr([probe] * probe_count)
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
    ):
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "compile",
        )


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
    assert "expected_preview=invented response" in message
    assert "expected_sha256=" in message


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

    assert frozen.services[0].protocol_probes[0].response_contains == (
        expected_container
    )


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


def test_compile_rejects_undeclared_output_files(tmp_path: Path) -> None:
    skill = _write_capability_skill(tmp_path, undeclared_output=True)
    capability = discover_replay_capability(skill)
    assert capability is not None

    with pytest.raises(ReplayCapabilityError, match="undeclared output"):
        compile_and_freeze_capability(
            capability,
            _request(skill),
            tmp_path / "artifacts",
        )


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
