from __future__ import annotations

import json

from aworld.self_evolve.repair_conformance import (
    RepairConformanceContract,
    compile_repair_conformance_contract,
    evaluate_candidate_source_conformance,
    evaluate_compiled_probe_conformance,
)
from aworld.self_evolve.replay_capability import (
    ReplayProtocolProbe,
    ReplayReadinessProbe,
    ReplayServiceSpec,
)
from aworld.self_evolve.types import CandidateFileDelta, CandidateVariant, SelfEvolveTargetRef


def _package(runtime_source: str = "def respond():\n    return {}\n") -> dict[str, object]:
    return {
        "candidate_id": "candidate-failed",
        "rationale": "claimed repair",
        "files": [
            {
                "path": "replay/capability.json",
                "operation": "upsert",
                "content": json.dumps(
                    {
                        "schema_version": "aworld.skill.replay_capability.v1",
                        "capability_id": "generic.replay",
                        "protocol": "aworld.replay.subprocess.v1",
                        "entrypoint": "replay/compiler.py",
                        "handles": ["local_endpoint"],
                        "runtime_files": ["replay/runtime.py"],
                    }
                ),
            },
            {
                "path": "replay/compiler.py",
                "operation": "upsert",
                "content": "def compile_request():\n    return None\n",
            },
            {
                "path": "replay/runtime.py",
                "operation": "upsert",
                "content": runtime_source,
            },
        ],
    }


def _candidate(*, runtime_source: str, compiler_source: str | None = None) -> CandidateVariant:
    files = [
        CandidateFileDelta(
            path="replay/compiler.py",
            content=compiler_source or "def compile_request():\n    return None\n",
        ),
        CandidateFileDelta(path="replay/runtime.py", content=runtime_source),
    ]
    return CandidateVariant(
        candidate_id="candidate-repair",
        target=SelfEvolveTargetRef(target_type="skill", target_id="generic"),
        content="# Generic\n",
        rationale="I fixed it",
        files=tuple(files),
    )


def _service(*probes: ReplayProtocolProbe) -> ReplayServiceSpec:
    return ReplayServiceSpec(
        service_id="service-1",
        requirement_id="requirement-1",
        transport="skill_runtime",
        response_fixture="fixtures/recorded.json",
        runtime_entrypoint="replay/runtime.py",
        readiness=ReplayReadinessProbe(kind="http", timeout_seconds=1, path="/ready"),
        protocol_probes=tuple(probes),
    )


def test_exact_probe_contract_rejects_rationale_only_or_unrelated_source_change() -> None:
    focus = {
        "repair_candidate_package": _package(),
        "candidate_validation_diagnostics": [
            {
                "code": "verify_declared_protocol_probe_branch",
                "stage": "replay_capability",
                "probe_kind": "websocket",
                "probe_path": "/session",
                "expected_preview": "fixture_token",
            }
        ],
    }

    contract = compile_repair_conformance_contract(focus)

    assert contract is not None
    assert contract.focus_candidate_id == "candidate-failed"
    assert contract.required_branch_paths == ("replay/runtime.py",)
    assert contract.exact_probe is not None
    assert RepairConformanceContract.from_dict(contract.to_dict()) == contract

    unchanged_runtime = _candidate(
        runtime_source="def respond():\n    return {}\n",
        compiler_source="def compile_request():\n    return 'changed only compiler'\n",
    )
    result = evaluate_candidate_source_conformance(unchanged_runtime, contract)

    assert result.passed is False
    assert result.code == "repair_branch_unchanged"

    changed_runtime = _candidate(
        runtime_source="def respond():\n    return {'token': 'fixture_token'}\n"
    )
    assert evaluate_candidate_source_conformance(changed_runtime, contract).passed is True


def test_source_conformance_treats_omitted_runtime_delta_as_unchanged() -> None:
    """Candidate files are deltas; omission must not fingerprint a blank file."""

    base_runtime = (
        "def handle(operation):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None
    # Construct the actual delta candidate with only the unrelated compiler
    # file; the baseline runtime is inherited by the overlay.
    compiler_only = CandidateVariant(
        candidate_id="candidate-compiler-only",
        target=SelfEvolveTargetRef(target_type="skill", target_id="generic"),
        content="# Generic\n",
        rationale="I fixed it",
        files=(
            CandidateFileDelta(
                path="replay/compiler.py",
                content="def compile_request():\n    return 'changed compiler only'\n",
            ),
        ),
    )
    result = evaluate_candidate_source_conformance(compiler_only, contract)
    assert result.passed is False
    assert result.code == "repair_branch_unchanged"


def test_task_plane_conformance_rejects_global_response_after_data_plane_progress() -> None:
    base_runtime = (
        "GATEWAY_KEYS = {'action_result', 'tool_outputs'}\n"
        "PAYLOAD_KEYS = {'content'}\n"
        "def handle(message):\n"
        "    method = message.get('method')\n"
        "    if method == 'records.query':\n"
        "        return {'records': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "interaction_progress": 8,
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None

    result = evaluate_candidate_source_conformance(
        _candidate(
            runtime_source=base_runtime.replace(
                "return {'records': []}",
                "return {'records': [{'value': 'recorded'}]}",
            )
        ),
        contract,
    )

    assert result.passed is False
    assert result.code == "operation_response_uncorrelated"


def test_task_plane_conformance_requires_framework_response_index_binding() -> None:
    base_runtime = (
        "def handle(message):\n"
        "    if message.get('method') == 'records.query':\n"
        "        return {'records': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "interaction_progress": 8,
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None

    operation_map_candidate = _candidate(
        runtime_source=(
            "RESPONSES_BY_OPERATION = {'records.query': {'records': [{'value': 'recorded'}]}}\n"
            "def handle(message):\n"
            "    operation = message.get('method')\n"
            "    if operation == 'records.query':\n"
            "        return RESPONSES_BY_OPERATION[operation]\n"
            "    return {}\n"
        )
    )
    result = evaluate_candidate_source_conformance(operation_map_candidate, contract)
    assert result.passed is False
    assert result.code == "operation_response_uncorrelated"
    assert "AWORLD_REPLAY_RESPONSE_INDEX" in result.details["required_change"]


def test_task_plane_conformance_rejects_outer_fixture_list_projection() -> None:
    base_runtime = (
        "def handle(operation):\n"
        "    if operation == 'records.query':\n"
        "        return {'records': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None
    candidate = _candidate(
        runtime_source=(
            "FIXTURE_DATA = []\n"
            "def _normalize_fixture_list(key):\n"
            "    if isinstance(FIXTURE_DATA, list):\n"
            "        return FIXTURE_DATA\n"
            "    return []\n\n"
            "def handle(operation):\n"
            "    if operation == 'records.query':\n"
            "        return {'records': _normalize_fixture_list('records')}\n"
            "    return {}\n"
        )
    )
    result = evaluate_candidate_source_conformance(candidate, contract)
    assert result.passed is False
    assert result.code == "forbidden_fixture_probe_derivation"
    assert result.details["violations"] == [
        {
            "path": "replay/runtime.py",
            "function": "_normalize_fixture_list",
            "line": 4,
            "construct": "top_level_fixture_projection",
        }
    ]


def test_source_conformance_requires_observed_branch_or_called_helper_change() -> None:
    base_runtime = (
        "def recorded_items():\n"
        "    return []\n\n"
        "def handle(operation):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': recorded_items()}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "stage": "replay_capability",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None
    assert contract.base_branch_fingerprints

    unused_helper = _candidate(
        runtime_source=(
            base_runtime
            + "\ndef new_fixture_helper():\n"
            + "    return ['recorded']\n"
        )
    )
    result = evaluate_candidate_source_conformance(unused_helper, contract)
    assert result.passed is False
    assert result.code == "repair_branch_unchanged"

    called_helper = _candidate(
        runtime_source=base_runtime.replace(
            "return {'items': recorded_items()}",
            "return {'items': new_fixture_helper()}",
        )
        + "\ndef new_fixture_helper():\n"
        + "    return ['recorded']\n"
    )
    assert evaluate_candidate_source_conformance(called_helper, contract).passed is True


def test_source_conformance_rejects_index_declared_but_global_task_response() -> None:
    """An index marker must reach the observed operation's returned payload."""

    base_runtime = (
        "def handle(operation):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "stage": "replay_capability",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None

    candidate = _candidate(
        runtime_source=(
            "AWORLD_REPLAY_RESPONSE_INDEX = 'responses.json'\n"
            "RESPONSE_CONTAINER = []\n\n"
            "def handle(operation):\n"
            "    if operation == 'records.query':\n"
            "        return {'items': RESPONSE_CONTAINER}\n"
            "    return {}\n"
        )
    )

    result = evaluate_candidate_source_conformance(candidate, contract)

    assert result.passed is False
    assert result.code == "operation_response_uncorrelated"
    assert "response-index record" in result.details["required_change"]


def test_source_conformance_ignores_unrelated_selector_not_reached_by_probe() -> None:
    base_runtime = (
        "FIXTURE_DATA = {}\n"
        "def _normalize_fixture_list(key):\n"
        "    return FIXTURE_DATA.get(key, [])\n\n"
        "def handle(operation):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "stage": "replay_capability",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None
    candidate = _candidate(
        runtime_source=(
            "AWORLD_REPLAY_RESPONSE_INDEX = 'responses.json'\n"
            "RESPONSE_INDEX = {'records.query': {'items': ['recorded']}}\n"
            "FIXTURE_DATA = {}\n"
            "def _normalize_fixture_list(key):\n"
            "    return FIXTURE_DATA.get(key, [])\n\n"
            "def handle(operation):\n"
            "    if operation == 'records.query':\n"
            "        response_index = RESPONSE_INDEX\n"
            "        return response_index[operation]\n"
            "    return {}\n"
        )
    )

    result = evaluate_candidate_source_conformance(candidate, contract)

    assert result.passed is True


def test_source_conformance_accepts_changed_fixture_selector_data_dependency() -> None:
    base_runtime = (
        "RESPONSE_VALUE = 'empty'\n\n"
        "def select_fixture_response(value):\n"
        "    return 'empty'\n\n"
        "def load_fixture(value):\n"
        "    global RESPONSE_VALUE\n"
        "    RESPONSE_VALUE = select_fixture_response(value)\n\n"
        "def handle(operation):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': [RESPONSE_VALUE]}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "stage": "replay_capability",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None

    repaired_selector = _candidate(
        runtime_source=base_runtime.replace(
            "return 'empty'",
            "return value if value not in ('', None) else None",
            1,
        )
    )

    result = evaluate_candidate_source_conformance(repaired_selector, contract)

    assert result.passed is True
    assert result.code == "repair_branch_changed"
    assert result.details["changed_paths"] == [
        "replay/runtime.py#select_fixture_response"
    ]


def test_source_conformance_rejects_changed_unused_existing_fixture_selector() -> None:
    base_runtime = (
        "def select_fixture_response(value):\n"
        "    return 'unused'\n\n"
        "def handle(operation):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None

    unrelated_selector_change = _candidate(
        runtime_source=base_runtime.replace(
            "return 'unused'",
            "return value",
        )
    )

    result = evaluate_candidate_source_conformance(
        unrelated_selector_change,
        contract,
    )

    assert result.passed is False
    assert result.code == "repair_branch_unchanged"


def test_source_conformance_rejects_fixture_probe_filter_or_hash_fallback() -> None:
    base_runtime = (
        "def handle(operation, value):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "stage": "replay_capability",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None

    filtered = _candidate(
        runtime_source=(
            "import re\n\n"
            "def select_payload_scalar(value):\n"
            "    if re.match(r'^[A-Za-z0-9_]{8,32}$', value):\n"
            "        return value\n"
            "    return None\n\n"
            "def handle(operation, value):\n"
            "    if operation == 'records.query':\n"
            "        return {'items': [select_payload_scalar(value)]}\n"
            "    return {}\n"
        )
    )
    result = evaluate_candidate_source_conformance(filtered, contract)

    assert result.passed is False
    assert result.code == "forbidden_fixture_probe_derivation"
    assert result.details["violations"] == [
        {
            "path": "replay/runtime.py",
            "function": "select_payload_scalar",
            "line": 4,
            "construct": "regex_scalar_filter",
        }
    ]

    unfiltered = _candidate(
        runtime_source=(
            "def select_payload_scalar(value):\n"
            "    return value if value not in ('', None) else None\n\n"
            "def handle(operation, value):\n"
            "    if operation == 'records.query':\n"
            "        return {'items': [select_payload_scalar(value)]}\n"
            "    return {}\n"
        )
    )
    assert evaluate_candidate_source_conformance(unfiltered, contract).passed is True

    placeholder = _candidate(
        runtime_source=(
            "def select_payload_scalar(value):\n"
            "    return value or 'replay_default_token'\n\n"
            "def handle(operation, value):\n"
            "    if operation == 'records.query':\n"
            "        return {'items': [select_payload_scalar(value)]}\n"
            "    return {}\n"
        )
    )
    placeholder_result = evaluate_candidate_source_conformance(
        placeholder,
        contract,
    )
    assert placeholder_result.passed is False
    assert placeholder_result.details["violations"][0]["construct"] == (
        "literal_probe_fallback"
    )


def test_source_conformance_requires_gateway_branch_after_outside_payload_failure() -> None:
    base_runtime = (
        "def handle(operation, value):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                },
                {
                    "code": "failed_gate",
                    "details": {
                        "stage": "repair_conformance",
                        "code": "late_fixture_probe_outside_recorded_payload",
                    },
                },
            ],
        }
    )
    assert contract is not None
    assert "late_fixture_probe_outside_recorded_payload" in contract.failure_codes

    global_scalar = _candidate(
        runtime_source=(
            "def select_scalar(value):\n"
            "    return value\n\n"
            "def handle(operation, value):\n"
            "    if operation == 'records.query':\n"
            "        return {'items': [select_scalar(value)]}\n"
            "    return {}\n"
        )
    )
    result = evaluate_candidate_source_conformance(global_scalar, contract)

    assert result.passed is False
    assert result.code == "fixture_gateway_discovery_missing"
    assert result.details["missing_gateway_keys"] == [
        "action_result",
        "tool_outputs",
    ]

    gateway_aware = _candidate(
        runtime_source=(
            "GATEWAY_KEYS = {'action_result', 'tool_outputs'}\n"
            "PAYLOAD_KEYS = {'content', 'response', 'result', 'output', 'body', 'data'}\n\n"
            "def select_payload_scalar(value):\n"
            "    return value\n\n"
            "def handle(operation, value):\n"
            "    if operation == 'records.query':\n"
            "        return {'items': [select_payload_scalar(value)]}\n"
            "    return {}\n"
        )
    )
    assert evaluate_candidate_source_conformance(gateway_aware, contract).passed is True


def test_source_conformance_rejects_gateway_selector_that_skips_sequence_roots() -> None:
    base_runtime = (
        "def handle(operation, value):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                },
                {
                    "code": "failed_gate",
                    "details": {
                        "code": "late_fixture_probe_outside_recorded_payload",
                    },
                },
            ],
        }
    )
    assert contract is not None

    shallow_gateway = _candidate(
        runtime_source=(
            "PAYLOAD_KEYS = ('content',)\n\n"
            "def gateway_value(obj):\n"
            "    if not isinstance(obj, dict):\n"
            "        return obj\n"
            "    for key in ('action_result', 'tool_outputs'):\n"
            "        if key in obj:\n"
            "            return gateway_value(obj[key])\n"
            "    return obj\n\n"
            "def handle(operation, value):\n"
            "    if operation == 'records.query':\n"
            "        return {'items': [gateway_value(value)]}\n"
            "    return {}\n"
        )
    )

    result = evaluate_candidate_source_conformance(shallow_gateway, contract)

    assert result.passed is False
    assert result.code == "forbidden_fixture_probe_derivation"
    assert result.details["violations"] == [
        {
            "path": "replay/runtime.py",
            "function": "gateway_value",
            "line": 4,
            "construct": "gateway_discovery_skips_nested_sequences",
        }
    ]

    recursive_gateway = _candidate(
        runtime_source=(
            "PAYLOAD_KEYS = ('content',)\n\n"
            "def discover_gateways(obj, gateways):\n"
            "    if isinstance(obj, dict):\n"
            "        for key, value in obj.items():\n"
            "            if key in ('action_result', 'tool_outputs'):\n"
            "                gateways.append(value)\n"
            "            else:\n"
            "                discover_gateways(value, gateways)\n"
            "    elif isinstance(obj, list):\n"
            "        for value in obj:\n"
            "            discover_gateways(value, gateways)\n\n"
            "def select_payload(gateway):\n"
            "    if isinstance(gateway, dict):\n"
            "        for key in PAYLOAD_KEYS:\n"
            "            if key in gateway:\n"
            "                return gateway[key]\n"
            "    return gateway\n\n"
            "def handle(operation, value):\n"
            "    if operation == 'records.query':\n"
            "        gateways = []\n"
            "        discover_gateways(value, gateways)\n"
            "        return {'items': [select_payload(item) for item in gateways]}\n"
            "    return {}\n"
        )
    )
    assert evaluate_candidate_source_conformance(
        recursive_gateway,
        contract,
    ).passed is True


def test_source_conformance_rejects_gateway_container_and_boolean_metadata() -> None:
    base_runtime = (
        "def handle(operation, value):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                },
                {
                    "code": "failed_gate",
                    "details": {
                        "code": "late_fixture_probe_outside_recorded_payload",
                    },
                },
            ],
        }
    )
    assert contract is not None

    nonconforming = _candidate(
        runtime_source=(
            "PAYLOAD_KEYS = ('content',)\n\n"
            "def select_leaf(value):\n"
            "    if isinstance(value, (str, int, float)) and value != '':\n"
            "        return str(value)\n"
            "    return None\n\n"
            "def find_gateways(root):\n"
            "    gateways = []\n"
            "    if isinstance(root, dict):\n"
            "        for key, value in root.items():\n"
            "            if key in ('action_result', 'tool_outputs'):\n"
            "                gateways.append(root)\n"
            "    return gateways\n\n"
            "def handle(operation, value):\n"
            "    if operation == 'records.query':\n"
            "        return {'items': [select_leaf(item) for item in find_gateways(value)]}\n"
            "    return {}\n"
        )
    )

    result = evaluate_candidate_source_conformance(nonconforming, contract)

    assert result.passed is False
    assert result.code == "forbidden_fixture_probe_derivation"
    assert {
        item["construct"] for item in result.details["violations"]
    } == {
        "gateway_container_selected_instead_of_subtree",
        "boolean_metadata_not_excluded",
    }


def test_source_conformance_accepts_explicit_boolean_guard() -> None:
    base_runtime = (
        "def handle(operation, value):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                },
                {
                    "code": "failed_gate",
                    "details": {"code": "late_fixture_probe_outside_recorded_payload"},
                },
            ],
        }
    )
    assert contract is not None
    repaired = _candidate(
        runtime_source=(
            "GATEWAY_KEYS = {'action_result', 'tool_outputs'}\n"
            "PAYLOAD_KEYS = {'content'}\n\n"
            "def select_leaf(value):\n"
            "    if isinstance(value, bool):\n"
            "        return None\n"
            "    if isinstance(value, (str, int, float)) and value != '':\n"
            "        return str(value)\n"
            "    return None\n\n"
            "def handle(operation, value):\n"
            "    if operation == 'records.query':\n"
            "        return {'items': [select_leaf(value)]}\n"
            "    return {}\n"
        )
    )
    result = evaluate_candidate_source_conformance(repaired, contract)
    assert result.passed is True


def test_source_conformance_rejects_declared_but_unused_payload_gate() -> None:
    base_runtime = (
        "def handle(operation, value):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                },
                {
                    "code": "failed_gate",
                    "details": {
                        "code": "late_fixture_probe_outside_recorded_payload",
                    },
                },
            ],
        }
    )
    assert contract is not None

    dead_payload_gate = _candidate(
        runtime_source=(
            "GATEWAY_KEYS = {'action_result', 'tool_outputs'}\n"
            "PAYLOAD_KEYS = {'content', 'response', 'result', 'output', 'body', 'data'}\n\n"
            "def select_payload_leaf(value):\n"
            "    if isinstance(value, dict):\n"
            "        for child in value.values():\n"
            "            selected = select_payload_leaf(child)\n"
            "            if selected:\n"
            "                return selected\n"
            "    return value if isinstance(value, str) and value else None\n\n"
            "def handle(operation, value):\n"
            "    if operation == 'records.query':\n"
            "        return {'items': [select_payload_leaf(value)]}\n"
            "    return {}\n"
        )
    )

    result = evaluate_candidate_source_conformance(dead_payload_gate, contract)

    assert result.passed is False
    assert result.code == "forbidden_fixture_probe_derivation"
    assert result.details["violations"] == [
        {
            "path": "replay/runtime.py",
            "function": "select_payload_leaf",
            "line": 4,
            "construct": "payload_key_gate_declared_but_unused",
        }
    ]

    used_payload_gate = _candidate(
        runtime_source=(
            "GATEWAY_KEYS = {'action_result', 'tool_outputs'}\n"
            "PAYLOAD_KEYS = {'content', 'response', 'result', 'output', 'body', 'data'}\n\n"
            "def select_payload_leaf(value):\n"
            "    if isinstance(value, dict):\n"
            "        for key in PAYLOAD_KEYS:\n"
            "            if key in value:\n"
            "                return value[key]\n"
            "    return value if isinstance(value, str) and value else None\n\n"
            "def handle(operation, value):\n"
            "    if operation == 'records.query':\n"
            "        return {'items': [select_payload_leaf(value)]}\n"
            "    return {}\n"
        )
    )
    assert evaluate_candidate_source_conformance(
        used_payload_gate,
        contract,
    ).passed is True


def test_source_conformance_rejects_scalar_walk_directly_on_gateway() -> None:
    base_runtime = (
        "def handle(operation, value):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                },
                {
                    "code": "failed_gate",
                    "details": {
                        "code": "late_fixture_probe_outside_recorded_payload",
                    },
                },
            ],
        }
    )
    assert contract is not None

    direct_gateway_walk = _candidate(
        runtime_source=(
            "GATEWAY_KEYS = {'action_result', 'tool_outputs'}\n"
            "PAYLOAD_KEYS = {'content', 'response', 'result', 'output', 'body', 'data'}\n\n"
            "def select_scalar_leaf(value):\n"
            "    return value\n\n"
            "def select_gateway_response(gateway):\n"
            "    return select_scalar_leaf(gateway)\n\n"
            "def handle(operation, value):\n"
            "    if operation == 'records.query':\n"
            "        return {'items': [select_gateway_response(value)]}\n"
            "    return {}\n"
        )
    )

    result = evaluate_candidate_source_conformance(
        direct_gateway_walk,
        contract,
    )

    assert result.passed is False
    assert result.code == "forbidden_fixture_probe_derivation"
    assert any(
        violation["construct"] == "gateway_scalar_selected_before_payload"
        for violation in result.details["violations"]
    )


def test_source_conformance_rejects_unconditional_root_fallback_after_gateway() -> None:
    base_runtime = (
        "def handle(operation, value):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': []}\n"
        "    return {}\n"
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(base_runtime),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                },
                {
                    "code": "failed_gate",
                    "details": {
                        "code": "late_fixture_probe_outside_recorded_payload",
                    },
                },
            ],
        }
    )
    assert contract is not None

    unconditional_fallback = _candidate(
        runtime_source=(
            "GATEWAY_KEYS = {'action_result', 'tool_outputs'}\n"
            "PAYLOAD_KEYS = {'content', 'response', 'result', 'output', 'body', 'data'}\n\n"
            "def collect_gateways(root, gateways):\n"
            "    if isinstance(root, dict):\n"
            "        for key, value in root.items():\n"
            "            if key in GATEWAY_KEYS:\n"
            "                gateways.append(value)\n"
            "            collect_gateways(value, gateways)\n"
            "    elif isinstance(root, list):\n"
            "        for value in root:\n"
            "            collect_gateways(value, gateways)\n\n"
            "def collect_payloads(gateway):\n"
            "    if isinstance(gateway, dict):\n"
            "        return [gateway[key] for key in PAYLOAD_KEYS if key in gateway]\n"
            "    return []\n\n"
            "def select_scalar_leaf(value):\n"
            "    return value if value not in ('', None) else None\n\n"
            "def select_fixture_response(root):\n"
            "    gateways = []\n"
            "    collect_gateways(root, gateways)\n"
            "    if gateways:\n"
            "        for gateway in gateways:\n"
            "            for payload in collect_payloads(gateway):\n"
            "                leaf = select_scalar_leaf(payload)\n"
            "                if leaf is not None:\n"
            "                    return leaf\n"
            "    leaf = select_scalar_leaf(root)\n"
            "    return leaf\n\n"
            "def handle(operation, value):\n"
            "    if operation == 'records.query':\n"
            "        return {'items': [select_fixture_response(value)]}\n"
            "    return {}\n"
        )
    )

    result = evaluate_candidate_source_conformance(
        unconditional_fallback,
        contract,
    )

    assert result.passed is False
    assert result.code == "forbidden_fixture_probe_derivation"
    assert any(
        violation["construct"] == "root_fallback_reachable_after_gateway"
        for violation in result.details["violations"]
    )

    mutually_exclusive_fallback = _candidate(
        runtime_source=unconditional_fallback.files[1].content.replace(
            "    leaf = select_scalar_leaf(root)\n"
            "    return leaf\n\n"
            "def handle",
            "    else:\n"
            "        leaf = select_scalar_leaf(root)\n"
            "        return leaf\n"
            "    return None\n\n"
            "def handle",
        )
    )
    assert evaluate_candidate_source_conformance(
        mutually_exclusive_fallback,
        contract,
    ).passed is True

    terminating_gateway_branch = _candidate(
        runtime_source=unconditional_fallback.files[1].content.replace(
            "                    return leaf\n"
            "    leaf = select_scalar_leaf(root)\n",
            "                    return leaf\n"
            "        return None\n"
            "    leaf = select_scalar_leaf(root)\n",
        )
    )
    assert evaluate_candidate_source_conformance(
        terminating_gateway_branch,
        contract,
    ).passed is True


def test_exact_probe_contract_requires_and_checks_the_declared_probe() -> None:
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(),
            "candidate_validation_diagnostics": [
                {
                    "code": "verify_declared_protocol_probe_branch",
                    "stage": "replay_capability",
                    "probe_kind": "websocket",
                    "probe_path": "/session",
                    "expected_preview": "fixture_token",
                }
            ],
        }
    )
    assert contract is not None

    stale = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"records.open"}',
            response_contains="placeholder",
        )
    )
    result = evaluate_compiled_probe_conformance((stale,), contract)
    assert result.passed is False
    assert result.code == "exact_repair_probe_missing"

    repaired = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"records.open"}',
            response_contains="fixture_token",
        )
    )
    assert evaluate_compiled_probe_conformance(
        (repaired,),
        contract,
        fixture_leaf_values={
            "fixtures/recorded.json": ("fixture_token",),
        },
    ).passed is True

    key_only = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"records.open"}',
            response_contains="ext_info",
        )
    )
    key_result = evaluate_compiled_probe_conformance(
        (key_only,),
        contract,
        fixture_leaf_values={
            "fixtures/recorded.json": ("fixture_token",),
        },
    )
    assert key_result.passed is False
    assert key_result.code == "exact_repair_probe_not_recorded"


def test_exact_probe_can_replace_stale_key_evidence_with_a_recorded_leaf() -> None:
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(),
            "candidate_validation_diagnostics": [
                {
                    "code": "verify_declared_protocol_probe_branch",
                    "stage": "replay_capability",
                    "probe_kind": "websocket",
                    "probe_path": "/session",
                    "expected_preview": "ext_info",
                }
            ],
        }
    )
    assert contract is not None
    repaired = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"records.open"}',
            response_contains="recorded_value",
        )
    )

    result = evaluate_compiled_probe_conformance(
        (repaired,),
        contract,
        fixture_leaf_values={
            "fixtures/recorded.json": ("recorded_value",),
        },
    )

    assert result.passed is True


def test_exact_probe_can_replace_a_stale_recorded_preview_with_another_leaf() -> None:
    """A failed preview is evidence, not a value the next repair must echo."""

    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": _package(),
            "candidate_validation_diagnostics": [
                {
                    "code": "verify_declared_protocol_probe_branch",
                    "stage": "replay_capability",
                    "probe_kind": "websocket",
                    "probe_path": "/session",
                    # This value happens to exist in the fixture, but it was
                    # the stale value emitted by the failed implementation.
                    "expected_preview": "stale-recorded-value",
                }
            ],
        }
    )
    assert contract is not None
    repaired = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"records.open"}',
            response_contains="new-recorded-value",
        )
    )

    result = evaluate_compiled_probe_conformance(
        (repaired,),
        contract,
        fixture_leaf_values={
            "fixtures/recorded.json": (
                "stale-recorded-value",
                "new-recorded-value",
            ),
        },
    )

    assert result.passed is True


def test_task_plane_contract_requires_late_non_empty_fixture_probe() -> None:
    contract = compile_repair_conformance_contract(
        {
            "interaction_progress": 138,
            "repair_candidate_package": _package(),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "stage": "replay_capability",
                    "observed_request_operations": [
                        "session.open",
                        "records.query",
                    ],
                }
            ],
        }
    )

    assert contract is not None
    assert contract.requires_fixture_derived_probe is True
    assert contract.late_observed_operations == ("session.open", "records.query")

    readiness_only = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"session.open"}',
            response_contains="fixture_token",
        )
    )
    result = evaluate_compiled_probe_conformance((readiness_only,), contract)
    assert result.passed is False
    assert result.code == "late_fixture_probe_missing"

    late_fixture_probe = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"records.query","arguments":{"limit":1}}',
            response_contains="recorded_value",
        )
    )
    assert evaluate_compiled_probe_conformance(
        (late_fixture_probe,),
        contract,
        fixture_leaf_values={
            "fixtures/recorded.json": ("recorded_value",),
        },
    ).passed is True


def test_task_plane_contract_without_observed_operation_fails_closed() -> None:
    contract = compile_repair_conformance_contract(
        {
            "interaction_progress": 12,
            "repair_candidate_package": _package(),
            "candidate_validation_diagnostics": [
                {"code": "implement_observed_endpoint_interactions"}
            ],
        }
    )
    assert contract is not None
    result = evaluate_compiled_probe_conformance(
        (_service(
            ReplayProtocolProbe(
                kind="websocket",
                path="/session",
                timeout_seconds=1,
                request_text='{"operation":"ready"}',
                response_contains="recorded-value",
            )
        ),),
        contract,
        fixture_leaf_values={"fixtures/recorded.json": ("recorded-value",)},
    )
    assert result.passed is False
    assert result.code == "late_observed_operation_missing"


def test_task_plane_contract_requires_new_frontier_probe_before_repeated_operation() -> None:
    parent = compile_repair_conformance_contract(
        {
            "interaction_progress": 40,
            "repair_candidate_package": _package(),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": [
                        "session.open",
                        "records.query",
                    ],
                }
            ],
        }
    )
    assert parent is not None

    contract = compile_repair_conformance_contract(
        {
            "interaction_progress": 80,
            "repair_candidate_package": _package(
                "def respond():\n    return {'changed': True}\n"
            ),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": [
                        "session.open",
                        "snapshot.read",
                        "records.query",
                    ],
                },
                {
                    "code": "failed_gate",
                    "details": {"repair_conformance": parent.to_dict()},
                },
            ],
        }
    )

    assert contract is not None
    assert contract.late_observed_operations == (
        "session.open",
        "snapshot.read",
        "records.query",
    )
    assert contract.required_fixture_probe_operations == (
        "records.query",
        "snapshot.read",
    )
    assert RepairConformanceContract.from_dict(contract.to_dict()) == contract

    repeated_old_probe = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"records.query"}',
            response_contains="recorded_value",
        )
    )
    stale_result = evaluate_compiled_probe_conformance(
        (repeated_old_probe,),
        contract,
        fixture_leaf_values={
            "fixtures/recorded.json": ("recorded_value",),
        },
    )
    assert stale_result.passed is False
    assert stale_result.code == "late_fixture_probe_missing"
    assert stale_result.details["required_probe_operation"] == "snapshot.read"

    frontier_only_probe = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"snapshot.read"}',
            response_contains="recorded_value",
        )
    )
    frontier_only_result = evaluate_compiled_probe_conformance(
        (frontier_only_probe,),
        contract,
        fixture_leaf_values={
            "fixtures/recorded.json": ("recorded_value",),
        },
    )
    assert frontier_only_result.passed is False
    assert frontier_only_result.details["missing_probe_operations"] == [
        "records.query"
    ]

    cumulative_probes = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"records.query"}',
            response_contains="recorded_value",
        ),
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"snapshot.read"}',
            response_contains="recorded_value",
        ),
    )
    assert evaluate_compiled_probe_conformance(
        (cumulative_probes,),
        contract,
        fixture_leaf_values={
            "fixtures/recorded.json": ("recorded_value",),
        },
    ).passed is True


def test_task_plane_probe_must_use_a_nested_fixture_leaf_not_a_key_token() -> None:
    contract = compile_repair_conformance_contract(
        {
            "interaction_progress": 12,
            "repair_candidate_package": _package(),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None

    key_only_probe = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"records.query"}',
            response_contains="result",
        )
    )
    result = evaluate_compiled_probe_conformance(
        (key_only_probe,),
        contract,
        fixture_leaf_values={
            "fixtures/recorded.json": ("recorded nested value",),
        },
    )

    assert result.passed is False
    assert result.code == "late_fixture_probe_not_recorded"
    assert result.details["required_reconstruction_algorithm"] == [
        "parse the recorded fixture as JSON or JSONL",
        "recursively decode bounded JSON object or array strings",
        "search arbitrary fixture nesting with a bounded node count rather than a shallow depth cutoff",
        "use a gateway-discovery pass before scalar selection and never fall back to non-output trajectory branches when a gateway exists",
        "during discovery collect gateway subtrees only: never collect or return any scalar until the complete gateway list is known",
        "keep trajectory gateway keys limited to action_result and tool_outputs; treat content, response, result, output, body, and data only as payload keys after a gateway",
        "when gateways exist, call the payload collector on each gateway and call the scalar selector only on those payload subtrees; never scalar-walk a gateway directly",
        "treat payload selection inside gateways as phase 2; only use a generic parsed-root fallback when the complete gateway list is empty",
        "recursively traverse mapping values and list items",
        "for trajectory envelopes enter through action_result or tool_outputs at any depth, then ignore action-result metadata until reaching a content, response, result, output, body, or data payload",
        "when a gateway value is a list, apply payload-key selection to each item instead of sending the whole list to generic scalar traversal",
        "select a deterministic non-empty scalar leaf without arbitrary alphanumeric or narrow length filters",
        "reuse one selected leaf across probes unless distinct values are required",
        "reuse the same selector in compiler and runtime",
        "return the surrounding decoded recorded container in the protocol result payload",
        "choose probe request inputs that execute the fixture-derived handler branch rather than a constant-result branch",
    ]
    assert "mapping keys" in result.details["forbidden_derivations"]
    assert "raw-byte regex tokens" in result.details["forbidden_derivations"]
    assert (
        "action-result metadata such as tool names, call ids, success flags, or timing fields"
        in result.details["forbidden_derivations"]
    )
    assert (
        "hash or placeholder fallbacks when no leaf matches an arbitrary token regex"
        in result.details["forbidden_derivations"]
    )


def test_task_plane_probe_must_use_recorded_response_context_when_available() -> None:
    contract = compile_repair_conformance_contract(
        {
            "interaction_progress": 12,
            "repair_candidate_package": _package(),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None
    request_value_probe = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"records.query"}',
            response_contains="ignored request value",
        )
    )

    rejected = evaluate_compiled_probe_conformance(
        (request_value_probe,),
        contract,
        fixture_leaf_values={
            "fixtures/recorded.json": (
                "ignored request value",
                "recorded response value",
            ),
        },
        fixture_response_leaf_values={
            "fixtures/recorded.json": ("recorded response value",),
        },
    )

    assert rejected.passed is False
    assert rejected.code == "late_fixture_probe_outside_recorded_payload"
    assert rejected.details["declared_value_classification"] == (
        "fixture_scalar_outside_recorded_payload"
    )
    assert rejected.details["recorded_response_leaf_counts"] == {
        "fixtures/recorded.json": 1,
    }

    recorded_probe = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"records.query"}',
            response_contains="recorded response value",
        )
    )
    assert evaluate_compiled_probe_conformance(
        (recorded_probe,),
        contract,
        fixture_leaf_values={
            "fixtures/recorded.json": ("recorded response value",),
        },
        fixture_response_leaf_values={
            "fixtures/recorded.json": ("recorded response value",),
        },
    ).passed is True


def test_task_plane_probe_does_not_match_mapping_key_inside_encoded_container() -> None:
    contract = compile_repair_conformance_contract(
        {
            "interaction_progress": 12,
            "repair_candidate_package": _package(),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None
    probe = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"records.query"}',
            response_contains="result",
        )
    )

    result = evaluate_compiled_probe_conformance(
        (probe,),
        contract,
        fixture_leaf_values={
            "fixtures/recorded.json": ('{"result":"recorded"}',),
        },
        fixture_response_leaf_values={
            "fixtures/recorded.json": ('{"result":"recorded"}', "recorded"),
        },
    )

    assert result.passed is False
    assert result.code == "late_fixture_probe_not_recorded"


def test_task_plane_probe_rejects_fixture_leaf_when_response_context_is_empty() -> None:
    """An empty response-context map must not silently fall back to metadata."""

    contract = compile_repair_conformance_contract(
        {
            "interaction_progress": 12,
            "repair_candidate_package": _package(),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None
    probe = _service(
        ReplayProtocolProbe(
            kind="websocket",
            path="/session",
            timeout_seconds=1,
            request_text='{"operation":"records.query"}',
            response_contains="metadata-value",
        )
    )
    result = evaluate_compiled_probe_conformance(
        (probe,),
        contract,
        fixture_leaf_values={"fixtures/recorded.json": ("metadata-value",)},
        fixture_response_leaf_values={},
    )
    assert result.passed is False
    assert result.code == "late_fixture_probe_outside_recorded_payload"


def test_repair_contract_inherits_task_plane_constraints_across_failed_repairs() -> None:
    original = compile_repair_conformance_contract(
        {
            "interaction_progress": 152,
            "repair_candidate_package": _package(),
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": [
                        "session.open",
                        "records.query",
                    ],
                }
            ],
        }
    )
    assert original is not None

    next_package = _package(
        "def handle(operation):\n"
        "    if operation == 'records.query':\n"
        "        return {'items': []}\n"
        "    return {}\n"
    )
    next_package["candidate_id"] = "candidate-repair-1"
    inherited = compile_repair_conformance_contract(
        {
            "repair_candidate_package": next_package,
            "candidate_validation_diagnostics": [
                {
                    "code": "failed_gate",
                    "stage": "candidate_repair_conformance",
                    "details": {
                        "code": "late_fixture_probe_missing",
                        "repair_conformance": original.to_dict(),
                    },
                }
            ],
        }
    )

    assert inherited is not None
    assert inherited.focus_candidate_id == "candidate-repair-1"
    assert inherited.requires_fixture_derived_probe is True
    assert inherited.late_observed_operations == (
        "session.open",
        "records.query",
    )
    assert inherited.interaction_progress == 152
    assert "implement_observed_endpoint_interactions" in inherited.failure_codes
