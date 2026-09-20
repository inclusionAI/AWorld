import importlib.util
import hashlib
import json
import shutil
import threading
from http.server import HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from aworld.self_evolve.replay_adaptation import ReplayCapabilityRequirement
from aworld.self_evolve.replay_capability import (
    ReplayCapabilityCompileRequest,
    compile_and_freeze_capability,
    discover_replay_capability,
    materialize_replay_evidence_derivations,
)


REPLAY_ROOT = Path(__file__).parents[2] / "aworld-skills" / "agent-browser" / "replay"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compiler = _load_module("agent_browser_replay_compiler", REPLAY_ROOT / "compiler.py")
runtime_module = _load_module(
    "agent_browser_replay_runtime", REPLAY_ROOT / "runtime.py"
)


def _write_response_index(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "aworld.self_evolve.recorded_response_index.v1",
                "records": [
                    {
                        "record_id": "response-record-first",
                        "value": {"body": "first"},
                    },
                    {
                        "record_id": "response-record-selected",
                        "value": {"body": "selected"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_compiler_derives_runtime_entry_and_probe_path_from_requirement_url(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps({"action_result": {"content": "recorded response"}}),
        encoding="utf-8",
    )
    request = {
        "evidence_derivations": {
            "evidence-one": [{"path": str(source)}],
        },
        "requirements": [
            {
                "requirement_id": "requirement-paper",
                "identifier": ("https://example.test/abs/2605.11182?download=1#paper"),
                "status": "runtime_required",
                "evidence_refs": ["evidence-one"],
            }
        ],
    }

    result = compiler.compile_request(request, tmp_path / "compiled")

    assert len(result["services"]) == 1
    service = result["services"][0]
    assert service["task_entry_path"] == "/abs/2605.11182"
    assert service["protocol_probes"][0]["path"] == "/abs/2605.11182"
    assert service["readiness"]["path"] == "/healthz"
    assert compiler._derive_task_entry_path("https://example.test") == "/"
    assert compiler._derive_task_entry_path("https://example.test/papers/") == "/"
    assert (
        compiler._derive_task_entry_path("https://example.test/foo/%2e%2e/")
        == "/foo/%2e%2e/"
    )
    assert compiler._derive_task_entry_path("prior-task-context") == "/"
    assert compiler._derive_readiness_path("/healthz") == "/_readiness"


def test_compiler_prefers_source_with_most_recorded_responses(
    tmp_path: Path,
) -> None:
    unindexed = tmp_path / "unindexed.json"
    unindexed.write_text('{"content":"unindexed"}', encoding="utf-8")
    partial = tmp_path / "partial.json"
    partial.write_text('{"content":"partial"}', encoding="utf-8")
    complete = tmp_path / "complete.json"
    complete.write_text('{"content":"complete"}', encoding="utf-8")
    request = {
        "evidence_derivations": {
            "evidence-one": [
                {"path": str(unindexed), "byte_length": 23},
                {
                    "path": str(partial),
                    "byte_length": 21,
                    "response_index_path": str(tmp_path / "partial.responses.json"),
                    "response_record_count": 2,
                },
                {
                    "path": str(complete),
                    "byte_length": 22,
                    "response_index_path": str(tmp_path / "complete.responses.json"),
                    "response_record_count": 7,
                },
            ],
        },
        "requirements": [
            {
                "requirement_id": "requirement-paper",
                "identifier": "https://example.test/paper",
                "status": "runtime_required",
                "evidence_refs": ["evidence-one"],
            }
        ],
    }

    output = tmp_path / "compiled"
    result = compiler.compile_request(request, output)

    fixture = output / result["services"][0]["response_fixture"]
    assert json.loads(fixture.read_text(encoding="utf-8")) == {"content": "complete"}


def test_compiler_recorded_source_selector_rejects_invalid_counts() -> None:
    selected = compiler._select_source(
        ["evidence-one"],
        {
            "evidence-one": [
                {
                    "path": "boolean",
                    "response_index_path": "boolean.responses.json",
                    "response_record_count": True,
                },
                {
                    "path": "zero",
                    "response_index_path": "zero.responses.json",
                    "response_record_count": 0,
                },
                {
                    "path": "string",
                    "response_index_path": "string.responses.json",
                    "response_record_count": "99",
                },
                {
                    "path": "valid",
                    "response_index_path": "valid.responses.json",
                    "response_record_count": 1,
                },
            ]
        },
    )

    assert selected is not None
    assert selected["path"] == "valid"


@pytest.mark.replay_sandbox
@pytest.mark.parametrize(
    ("identifier", "expected_path"),
    (
        (
            "https://example.test/abs/2605.11182?view=compact",
            "/abs/2605.11182",
        ),
        ("https://example.test/papers/", "/papers/"),
    ),
)
def test_agent_browser_capability_freezes_exact_http_task_entry(
    tmp_path: Path,
    identifier: str,
    expected_path: str,
) -> None:
    skill_root = tmp_path / "agent-browser"
    shutil.copytree(REPLAY_ROOT.parent, skill_root)
    context_payload = {
        "case_id": "case-paper",
        "steps": [
            {
                "observation": {
                    "action_result": {"content": {"body": "canonical recorded paper"}}
                }
            }
        ],
    }
    context_fingerprint = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                context_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    context_path = tmp_path / "context.json"
    context_path.write_text(
        json.dumps({**context_payload, "fingerprint": context_fingerprint}),
        encoding="utf-8",
    )
    evidence_ref = f"context:case-paper:{context_fingerprint}"
    requirement = ReplayCapabilityRequirement(
        requirement_id="requirement-paper",
        kind="http_resource",
        identifier=identifier,
        case_ids=("case-paper",),
        evidence_refs=(evidence_ref,),
        status="runtime_required",
    )
    request = ReplayCapabilityCompileRequest.create(
        requirements=(requirement,),
        context_snapshots={"case-paper": str(context_path)},
        task_inputs={"case-paper": {"content": "Read the recorded paper."}},
        capability_root=skill_root,
        context_fingerprint="sha256:context-set",
    )
    evidence_derivations = materialize_replay_evidence_derivations(
        request,
        tmp_path / "evidence-derivations",
    )
    request = ReplayCapabilityCompileRequest.create(
        requirements=(requirement,),
        context_snapshots={"case-paper": str(context_path)},
        task_inputs={"case-paper": {"content": "Read the recorded paper."}},
        capability_root=skill_root,
        context_fingerprint="sha256:context-set",
        evidence_derivations=evidence_derivations,
    )
    capability = discover_replay_capability(skill_root)
    assert capability is not None

    frozen = compile_and_freeze_capability(
        capability,
        request,
        tmp_path / "compiled-capability",
    )

    assert frozen.ready is True
    service = frozen.services[0]
    assert service.task_entry_path == expected_path
    assert service.protocol_probes[0].path == expected_path
    assert service.protocol_probes[0].response_record_id is not None


def test_runtime_selects_exact_response_record_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"content":"fixture fallback"}', encoding="utf-8")
    response_index = tmp_path / "fixture.responses.json"
    _write_response_index(response_index)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("AWORLD_REPLAY_RESPONSE_INDEX", str(response_index))
    monkeypatch.setenv("AWORLD_REPLAY_RESPONSE_RECORD_ID", "response-record-selected")

    replay_runtime = runtime_module.ReplayRuntime(fixture, scratch, 0)
    try:
        assert replay_runtime.resolve_payload() == (
            True,
            {"body": "selected"},
        )

        monkeypatch.setenv(
            "AWORLD_REPLAY_RESPONSE_RECORD_ID", "response-record-missing"
        )
        assert replay_runtime.resolve_payload() == (
            False,
            {"error": "not-recorded"},
        )
    finally:
        replay_runtime.close()


@pytest.mark.parametrize("legacy_shape", ["mapping", "list"])
def test_runtime_preserves_legacy_sidecar_without_record_selector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    legacy_shape: str,
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"content":"fixture fallback"}', encoding="utf-8")
    records = [{"value": "first"}, {"value": {"body": "second"}}]
    sidecar = {"records": records} if legacy_shape == "mapping" else records
    response_index = tmp_path / "fixture.responses.json"
    response_index.write_text(json.dumps(sidecar), encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("AWORLD_REPLAY_RESPONSE_INDEX", str(response_index))
    monkeypatch.delenv("AWORLD_REPLAY_RESPONSE_RECORD_ID", raising=False)

    replay_runtime = runtime_module.ReplayRuntime(fixture, scratch, 0)
    try:
        assert replay_runtime.resolve_payload() == (
            True,
            {"values": ["first", {"body": "second"}]},
        )
    finally:
        replay_runtime.close()


def test_runtime_serves_only_readiness_and_exact_task_entry_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"content":"fixture fallback"}', encoding="utf-8")
    response_index = tmp_path / "fixture.responses.json"
    _write_response_index(response_index)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("AWORLD_REPLAY_RESPONSE_INDEX", str(response_index))
    monkeypatch.setenv("AWORLD_REPLAY_RESPONSE_RECORD_ID", "response-record-selected")
    monkeypatch.setenv("AWORLD_REPLAY_TASK_ENTRY_PATH", "/abs/2605.11182")

    replay_runtime = runtime_module.ReplayRuntime(fixture, scratch, 0)
    runtime_module.Handler.runtime = replay_runtime
    server = HTTPServer(("127.0.0.1", 0), runtime_module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        with urlopen(base_url + "/abs/2605.11182?view=compact", timeout=5) as response:
            assert response.status == 200
            assert json.load(response) == {"body": "selected"}

        request = Request(
            base_url + "/abs/2605.11182",
            data=b'{"request_id":"request-one"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            assert response.status == 200
            assert json.load(response) == {"body": "selected"}

        with urlopen(base_url + "/healthz", timeout=5) as response:
            assert response.status == 200
            assert json.load(response) == {"status": "ok"}

        for path in ("/html/2605.11182", "/pdf/2605.11182", "/favicon.ico"):
            with pytest.raises(HTTPError) as error:
                urlopen(base_url + path, timeout=5)
            assert error.value.code == 404
            assert json.load(error.value) == {"error": "not-recorded"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        replay_runtime.close()

    trace = [
        json.loads(line)
        for line in (scratch / "protocol_trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    responses = [item for item in trace if item["kind"] == "http_response"]
    assert any(
        item.get("path") == "/abs/2605.11182"
        and item["correlation"].get("status") == 200
        for item in responses
    )
    assert any(
        item.get("path") == "/pdf/2605.11182"
        and item["correlation"].get("status") == 404
        for item in responses
    )
