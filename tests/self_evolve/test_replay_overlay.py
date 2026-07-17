from __future__ import annotations

import base64
import hashlib
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.overlay import create_candidate_skill_overlay
from aworld.self_evolve.overlay import cleanup_self_evolve_overlays
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    AWorldCliReplayExecutor,
    CandidateReplayRequest,
    CandidateReplayResult,
    ReplayExecutionRequest,
    ReplayExecutionResult,
    ReplayServiceProtocolError,
    ReplayVariantResult,
    build_paired_replay_dataset,
    build_replay_request,
    candidate_replay_is_comparable,
    load_candidate_replay_result,
    _invalid_evidence_manifest_entry_reason,
    _member_artifact_name,
    _member_baseline_replay_dir,
    _probe_advertised_websockets,
    _attach_replay_service_protocol_diagnostics,
    _preserve_replay_service_protocol_trace,
    _probe_replay_service,
    _read_websocket_frame,
    _replay_capability_fixture_summaries,
    replay_capability_fixture_leaf_values,
    replay_capability_fixture_response_leaf_values,
    _replay_service_failure_with_stderr,
    _replay_failure_outcome,
    _run_replay_cli,
    _validate_replay_service_protocol_trace,
    _validate_websocket_handshake_response,
)
from aworld.self_evolve.replay_adaptation import ReplayAdaptationCompiler
from aworld.self_evolve.replay_adaptation import ReplayAdapterBinding
from aworld.self_evolve.replay_capability import (
    FrozenReplayCapability,
    FrozenReplayFile,
    ReplayReadinessProbe,
    ReplayServiceSpec,
)
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    DatasetRecipe,
    SelfEvolveTargetRef,
)
from aworld.skills.compat_provider import build_compat_registry


def _candidate(content: str, candidate_id: str = "cand-1") -> CandidateVariant:
    return CandidateVariant(
        candidate_id=candidate_id,
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content=content,
        rationale="test candidate",
        target_fingerprint="sha256:old",
    )


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, default=lambda value: value.__dict__, indent=2),
        encoding="utf-8",
    )


def test_replay_capability_fixture_summary_exposes_shape_without_content(
    tmp_path: Path,
) -> None:
    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixture.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('[{"private": "secret-value"}]', encoding="utf-8")
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixture.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    summaries = _replay_capability_fixture_summaries(capability)

    assert summaries == [
        {
            "service_id": "svc-1",
            "fixture_bytes": fixture.stat().st_size,
            "json_root_type": "array",
        }
    ]
    assert "secret-value" not in json.dumps(summaries)


def test_replay_capability_fixture_leaf_values_walk_arbitrary_nested_arrays(
    tmp_path: Path,
) -> None:
    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        json.dumps(
            {
                "envelope": [
                    {"response": {"items": []}},
                    {"response": [{"payload": "recorded nested value"}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixtures/recorded.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = replay_capability_fixture_leaf_values(capability)

    assert values == {
        "fixtures/recorded.json": ("recorded nested value",),
    }


def test_fixture_response_leaf_values_decode_nested_trajectory_outputs(
    tmp_path: Path,
) -> None:
    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture.parent.mkdir(parents=True)
    encoded_payload = json.dumps(
        {
            "result": {
                "items": [{"text": "recorded response value"}]
            }
        }
    )
    fixture.write_text(
        json.dumps(
            [
                {
                    "action": {
                        "result": {"value": "ignored result value"},
                        "tool_calls": [
                            {
                                "function": {
                                    "arguments": json.dumps(
                                        {"request": "ignored request value"}
                                    )
                                }
                            }
                        ]
                    },
                    "state": {
                        "input": {
                            "action_result": [
                                {
                                    "name": "ignored tool name",
                                    "tool_call_id": "ignored-tool-call-id",
                                    "success": True,
                                    "content": encoded_payload
                                }
                            ]
                        }
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixtures/recorded.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = replay_capability_fixture_response_leaf_values(capability)

    assert values == {
        "fixtures/recorded.json": (
            encoded_payload,
            "recorded response value",
        ),
    }


def test_fixture_response_leaf_values_find_nested_gateway_without_top_level_trace_keys(
    tmp_path: Path,
) -> None:
    """Nested trajectory gateways must not leak envelope metadata as payload."""

    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        json.dumps(
            {
                "context": {
                    "state": {
                        "action_result": [
                            {
                                "tool_name": "ignored metadata",
                                "success": True,
                                "content": json.dumps(
                                    {
                                        "records": [
                                            {"payload": "late recorded value"}
                                        ]
                                    }
                                ),
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixtures/recorded.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = replay_capability_fixture_response_leaf_values(capability)

    assert values == {
        "fixtures/recorded.json": (
            json.dumps({"records": [{"payload": "late recorded value"}]}),
            "late recorded value",
        ),
    }


def test_fixture_response_leaf_values_treat_tool_outputs_as_gateway_before_payload(
    tmp_path: Path,
) -> None:
    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        json.dumps(
            {
                "wrapper": {
                    "tool_outputs": [
                        {
                            "tool_name": "ignored tool name",
                            "success": True,
                            "response": {
                                "items": [{"text": "tool output value"}]
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixtures/recorded.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = replay_capability_fixture_response_leaf_values(capability)

    assert values == {
        "fixtures/recorded.json": ("tool output value",),
    }


def test_fixture_response_leaf_values_skip_metadata_inside_encoded_content_envelope(
    tmp_path: Path,
) -> None:
    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture.parent.mkdir(parents=True)
    encoded_content = json.dumps(
        {
            "type": "text",
            "content": "actual recorded output",
            "is_done": False,
        }
    )
    fixture.write_text(
        json.dumps(
            {
                "nested": {
                    "action_result": [
                        {
                            "success": "False",
                            "content": encoded_content,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixtures/recorded.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = replay_capability_fixture_response_leaf_values(capability)

    assert values == {
        "fixtures/recorded.json": (
            encoded_content,
            "actual recorded output",
        ),
    }


def test_fixture_response_leaf_values_support_deep_bounded_nesting(
    tmp_path: Path,
) -> None:
    """Discovery is bounded by nodes, not an arbitrary shallow depth cutoff."""

    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture.parent.mkdir(parents=True)
    nested: object = {
        "action_result": [
            {"content": "deep recorded output", "success": "False"}
        ]
    }
    for _ in range(72):
        nested = {"wrapper": nested}
    fixture.write_text(json.dumps(nested), encoding="utf-8")
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixtures/recorded.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = replay_capability_fixture_response_leaf_values(capability)

    assert values == {"fixtures/recorded.json": ("deep recorded output",)}


def test_fixture_response_leaf_values_keep_message_after_encoded_success_metadata(
    tmp_path: Path,
) -> None:
    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        json.dumps(
            {
                "action_result": [
                    {
                        "content": json.dumps(
                            {
                                "success": True,
                                "message": "recorded message output",
                            }
                        )
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixtures/recorded.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = replay_capability_fixture_response_leaf_values(capability)

    assert values == {
        "fixtures/recorded.json": (
            json.dumps({"success": True, "message": "recorded message output"}),
            "recorded message output",
        )
    }


def test_candidate_skill_overlay_materializes_shadow_root_without_mutating_real_skill(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    demo_path = skills_root / "demo" / "SKILL.md"
    helper_path = skills_root / "helper" / "SKILL.md"
    demo_path.parent.mkdir(parents=True)
    helper_path.parent.mkdir(parents=True)
    original_demo = "---\nname: demo\n---\n# Demo\n\nOriginal.\n"
    candidate_demo = "---\nname: demo\n---\n# Demo\n\nCandidate.\n"
    demo_path.write_text(original_demo, encoding="utf-8")
    helper_path.write_text("---\nname: helper\n---\n# Helper\n", encoding="utf-8")

    overlay = create_candidate_skill_overlay(
        workspace_root=tmp_path,
        run_id="run-1",
        candidate=_candidate(candidate_demo),
        target_skill_path=demo_path,
        baseline_skill_roots=(skills_root,),
    )

    assert overlay.shadow_root == tmp_path / ".aworld" / "self_evolve" / "run-1" / "overlays" / "cand-1" / "skills"
    assert overlay.candidate_skill_path.read_text(encoding="utf-8") == candidate_demo
    assert (overlay.shadow_root / "helper" / "SKILL.md").exists()
    assert demo_path.read_text(encoding="utf-8") == original_demo
    assert overlay.candidate_skill_package_fingerprint.startswith("sha256:")

    registry = build_compat_registry(overlay.shadow_root)
    descriptors = {descriptor.skill_name: descriptor for descriptor in registry.list_descriptors()}
    loaded_demo = registry.load_content(descriptors["demo"].skill_id)
    loaded_helper = registry.load_content(descriptors["helper"].skill_id)
    assert "Candidate." in loaded_demo.usage
    assert "Original." not in loaded_demo.usage
    assert "Helper" in loaded_helper.usage


def test_candidate_overlay_applies_replay_package_on_copy_of_target_skill(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    skill_root = skills_root / "demo"
    skill_path = skill_root / "SKILL.md"
    replay_root = skill_root / "replay"
    replay_root.mkdir(parents=True)
    skill_path.write_text("# Original\n", encoding="utf-8")
    (skill_root / "reference.md").write_text("keep\n", encoding="utf-8")
    (replay_root / "obsolete.py").write_text("old\n", encoding="utf-8")
    candidate = CandidateVariant(
        candidate_id="cand-package",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Candidate\n",
        rationale="add replay capability",
        target_fingerprint="sha256:old",
        files=(
            CandidateFileDelta(
                path="replay/compiler.py",
                content="print('compile')\n",
                executable=True,
            ),
            CandidateFileDelta(
                path="replay/obsolete.py",
                operation="delete",
            ),
        ),
    )

    overlay = create_candidate_skill_overlay(
        workspace_root=tmp_path,
        run_id="run-package",
        candidate=candidate,
        target_skill_path=skill_path,
        baseline_skill_roots=(skills_root,),
    )

    candidate_root = overlay.candidate_skill_path.parent
    assert overlay.candidate_skill_path.read_text(encoding="utf-8") == "# Candidate\n"
    assert (candidate_root / "reference.md").read_text(encoding="utf-8") == "keep\n"
    assert (candidate_root / "replay/compiler.py").read_text(encoding="utf-8") == (
        "print('compile')\n"
    )
    assert (candidate_root / "replay/compiler.py").stat().st_mode & 0o111
    assert not (candidate_root / "replay/obsolete.py").exists()
    assert (replay_root / "obsolete.py").read_text(encoding="utf-8") == "old\n"


def test_cleanup_self_evolve_overlays_retains_latest_runs(tmp_path: Path) -> None:
    root = tmp_path / ".aworld" / "self_evolve"
    old_overlay = root / "run-old" / "overlays" / "cand-1" / "skills"
    new_overlay = root / "run-new" / "overlays" / "cand-2" / "skills"
    old_overlay.mkdir(parents=True)
    new_overlay.mkdir(parents=True)
    old_report = root / "run-old" / "report.json"
    new_report = root / "run-new" / "report.json"
    old_report.write_text("{}", encoding="utf-8")
    new_report.write_text("{}", encoding="utf-8")

    cleanup = cleanup_self_evolve_overlays(tmp_path, keep_latest_runs=1)

    assert cleanup["removed_run_count"] == 1
    assert not (root / "run-old" / "overlays").exists()
    assert (root / "run-new" / "overlays").exists()


def test_http_probe_automatically_rejects_unreachable_advertised_websocket() -> None:
    class DiscoveryOnlyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps(
                {
                    "socket": (
                        f"ws://127.0.0.1:{self.server.server_port}/stateful"
                    )
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DiscoveryOnlyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(
            OSError,
            match=(
                "advertised WebSocket handshake (?:failed|requires HTTP/1.1)"
            ),
        ):
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "http",
                "/json/version",
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_skill_runtime_http_readiness_accepts_live_advertised_websocket() -> None:
    class StatefulHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            if self.headers.get("Upgrade", "").lower() == "websocket":
                key = self.headers["Sec-WebSocket-Key"]
                accept = base64.b64encode(
                    hashlib.sha1(
                        (
                            key
                            + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                        ).encode("ascii")
                    ).digest()
                ).decode("ascii")
                self.send_response(101)
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", accept)
                self.end_headers()
                header = self.connection.recv(2)
                length = header[1] & 0x7F
                mask = self.connection.recv(4)
                payload = self.connection.recv(length)
                payload = bytes(
                    value ^ mask[index % 4]
                    for index, value in enumerate(payload)
                )
                self.connection.sendall(bytes([0x8A, len(payload)]) + payload)
                return
            body = json.dumps(
                {
                    "socket": (
                        f"ws://127.0.0.1:{self.server.server_port}/stateful"
                    )
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), StatefulHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _probe_replay_service(
            "127.0.0.1",
            server.server_port,
            "http",
            "/json/version",
            validate_advertised_websockets=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_protocol_probe_mismatch_reports_actionable_bounded_diagnostics() -> None:
    class DiscoveryHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            body = b'{"Browser":"ReplayChrome","webSocketDebuggerUrl":"ws://local"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ReplayServiceProtocolError) as error:
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "http",
                "/json/version",
                response_contains="recorded fixture marker",
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    message = str(error.value)
    assert "kind=http path=/json/version" in message
    assert "expected_sha256=" in message
    assert "expected_bytes=23" in message
    assert "match=substring" in message
    assert "expected_preview=recorded fixture marker" in message
    assert 'response_preview={"Browser":"ReplayChrome"' in message
    assert len(message) < 500


def test_http_probe_accepts_semantically_equivalent_json_descendant() -> None:
    expected_value = {
        "success": True,
        "message": "recorded fixture\nwith escaped lines",
    }

    class SemanticDiscoveryHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            body = json.dumps(
                {"recorded_container": expected_value},
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SemanticDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _probe_replay_service(
            "127.0.0.1",
            server.server_port,
            "http",
            "/json/version",
            response_contains=json.dumps(
                expected_value,
                ensure_ascii=False,
                indent=2,
            ),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_advertised_websocket_invalid_port_reports_actionable_protocol_error() -> None:
    response = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
        b'{"webSocketDebuggerUrl":"ws://127.0.0.1:REPLACE_PORT/devtools/browser"}'
    )

    with pytest.raises(
        ReplayServiceProtocolError,
        match=(
            "advertised WebSocket URL has an invalid port; construct it from "
            "the supplied --port integer"
        ),
    ):
        _probe_advertised_websockets(
            response,
            expected_host="127.0.0.1",
            expected_port=54321,
        )


def test_websocket_probe_rejects_http_1_0_upgrade_response() -> None:
    with pytest.raises(OSError, match="requires HTTP/1.1"):
        _validate_websocket_handshake_response(
            (
                b"HTTP/1.0 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: expected\r\n\r\n"
            ),
            expected_accept="expected",
        )


def test_websocket_probe_reports_bounded_invalid_handshake_preview() -> None:
    with pytest.raises(ReplayServiceProtocolError) as error:
        _validate_websocket_handshake_response(
            (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n\r\n"
                b'{"error":"upgrade route not reached"}'
            ),
            expected_accept="expected",
        )

    message = str(error.value)
    assert "response_bytes=" in message
    assert "response_preview=HTTP/1.1 200 OK" in message
    assert len(message) < 500


def test_skill_runtime_rejects_websocket_handshake_only_stub() -> None:
    class HandshakeOnlyHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            if self.headers.get("Upgrade", "").lower() == "websocket":
                key = self.headers["Sec-WebSocket-Key"]
                accept = base64.b64encode(
                    hashlib.sha1(
                        (
                            key
                            + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                        ).encode("ascii")
                    ).digest()
                ).decode("ascii")
                self.send_response(101)
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", accept)
                self.end_headers()
                return
            body = json.dumps(
                {
                    "socket": (
                        f"ws://127.0.0.1:{self.server.server_port}/stateful"
                    )
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), HandshakeOnlyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(OSError, match="WebSocket control frame failed"):
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "http",
                "/json/version",
                validate_advertised_websockets=True,
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_skill_runtime_websocket_data_plane_probe_validates_response() -> None:
    class DataPlaneHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            key = self.headers["Sec-WebSocket-Key"]
            accept = base64.b64encode(
                hashlib.sha1(
                    (
                        key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                    ).encode("ascii")
                ).digest()
            ).decode("ascii")
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()

            opcode, payload = self._read_masked_frame()
            assert opcode == 0x9
            self.connection.sendall(bytes([0x8A, len(payload)]) + payload)
            opcode, payload = self._read_masked_frame()
            assert opcode == 0x1
            assert json.loads(payload) == {"op": "read"}
            response = b'{"result":"recorded fixture"}'
            self.connection.sendall(bytes([0x81, len(response)]) + response)

        def _read_masked_frame(self) -> tuple[int, bytes]:
            header = self.connection.recv(2)
            length = header[1] & 0x7F
            mask = self.connection.recv(4)
            payload = self.connection.recv(length)
            return (
                header[0] & 0x0F,
                bytes(
                    value ^ mask[index % 4]
                    for index, value in enumerate(payload)
                ),
            )

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DataPlaneHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _probe_replay_service(
            "127.0.0.1",
            server.server_port,
            "websocket",
            "/stateful",
            request_text='{"op":"read"}',
            response_contains="recorded fixture",
        )
        with pytest.raises(
            OSError,
            match="protocol probe response mismatch: kind=websocket",
        ):
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "websocket",
                "/stateful",
                request_text='{"op":"read"}',
                response_contains="missing fixture",
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_task_plane_probe_requires_fixture_content_in_nonempty_correlated_result() -> None:
    class CorrelatedDataPlaneHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            key = self.headers["Sec-WebSocket-Key"]
            accept = base64.b64encode(
                hashlib.sha1(
                    (
                        key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                    ).encode("ascii")
                ).digest()
            ).decode("ascii")
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()

            opcode, payload = self._read_masked_frame()
            assert opcode == 0x9
            self.connection.sendall(bytes([0x8A, len(payload)]) + payload)
            opcode, payload = self._read_masked_frame()
            assert opcode == 0x1
            request = json.loads(payload)
            if request["method"] == "records.empty":
                response_payload = {
                    "id": request["id"],
                    "result": [],
                    "replay_token": "recorded fixture",
                }
            elif request["method"] == "records.query":
                response_payload = {
                    "id": request["id"],
                    "result": {"records": ["recorded fixture"]},
                }
            else:
                response_payload = {
                    "id": request["id"],
                    "result": {
                        "records": [
                            "recorded fixture",
                            "second recorded value",
                        ]
                    },
                }
            response = json.dumps(response_payload).encode("utf-8")
            self.connection.sendall(bytes([0x81, len(response)]) + response)

        def _read_masked_frame(self) -> tuple[int, bytes]:
            header = self.connection.recv(2)
            length = header[1] & 0x7F
            mask = self.connection.recv(4)
            payload = self.connection.recv(length)
            return (
                header[0] & 0x0F,
                bytes(
                    value ^ mask[index % 4]
                    for index, value in enumerate(payload)
                ),
            )

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), CorrelatedDataPlaneHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(
            ReplayServiceProtocolError,
            match="fixture-derived content must be inside a non-empty correlated result",
        ):
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "websocket",
                "/stateful",
                request_text='{"id":7,"method":"records.empty"}',
                response_contains="recorded fixture",
                require_nonempty_correlated_response=True,
            )

        _probe_replay_service(
            "127.0.0.1",
            server.server_port,
            "websocket",
            "/stateful",
            request_text='{"id":8,"method":"records.query"}',
            response_contains="recorded fixture",
            require_nonempty_correlated_response=True,
        )

        with pytest.raises(
            ReplayServiceProtocolError,
            match="surrounding recorded response context",
        ):
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "websocket",
                "/stateful",
                request_text='{"id":9,"method":"records.query"}',
                response_contains="recorded fixture",
                require_nonempty_correlated_response=True,
                required_recorded_response_values=(
                    "recorded fixture",
                    "second recorded value",
                ),
            )

        _probe_replay_service(
            "127.0.0.1",
            server.server_port,
            "websocket",
            "/stateful",
            request_text='{"id":10,"method":"records.structured"}',
            response_contains="recorded fixture",
            require_nonempty_correlated_response=True,
            required_recorded_response_values=(
                "recorded fixture",
                "second recorded value",
            ),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_task_plane_probe_accepts_short_recorded_response_leaf() -> None:
    """Fixture reconstruction must not impose an arbitrary token length."""

    class ShortLeafHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            key = self.headers["Sec-WebSocket-Key"]
            accept = base64.b64encode(
                hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode(
                        "ascii"
                    )
                ).digest()
            ).decode("ascii")
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            opcode, payload = self._read_masked_frame()
            assert opcode == 0x9
            self.connection.sendall(bytes([0x8A, len(payload)]) + payload)
            opcode, payload = self._read_masked_frame()
            assert opcode == 0x1
            request = json.loads(payload)
            response = json.dumps(
                {
                    "id": request["id"],
                    "result": {"content": "OK"},
                }
            ).encode("utf-8")
            self.connection.sendall(bytes([0x81, len(response)]) + response)

        def _read_masked_frame(self) -> tuple[int, bytes]:
            header = self.connection.recv(2)
            length = header[1] & 0x7F
            mask = self.connection.recv(4)
            payload = self.connection.recv(length)
            return (
                header[0] & 0x0F,
                bytes(
                    value ^ mask[index % 4]
                    for index, value in enumerate(payload)
                ),
            )

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ShortLeafHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(
            ReplayServiceProtocolError,
            match="surrounding recorded response context",
        ):
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "websocket",
                "/stateful",
                request_text='{"id":11,"method":"records.query"}',
                response_contains="OK",
                require_nonempty_correlated_response=True,
                required_recorded_response_values=("OK", "YES"),
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_websocket_probe_rejects_masked_server_frame() -> None:
    client, server = socket.socketpair()
    try:
        payload = b"pong"
        mask = b"mask"
        masked = bytes(
            value ^ mask[index % 4]
            for index, value in enumerate(payload)
        )
        server.sendall(
            bytes([0x81, 0x80 | len(payload)]) + mask + masked
        )

        with pytest.raises(
            ReplayServiceProtocolError,
            match="WebSocket server frame must not be masked",
        ):
            _read_websocket_frame(client)
    finally:
        client.close()
        server.close()


def test_candidate_owned_runtime_protocol_failure_is_not_infrastructure() -> None:
    assert _replay_failure_outcome(
        {
            "type": "ReplayServiceProtocolError",
            "reason": "advertised WebSocket handshake failed",
            "outcome": "candidate_failure",
        }
    ) == "candidate_failure"


def test_replay_service_failure_includes_bounded_sanitized_runtime_stderr(
    tmp_path: Path,
) -> None:
    stderr_path = tmp_path / "stderr.txt"
    stderr_path.write_text(
        "Traceback at /private/tmp/runtime.py\n"
        "OSError: [Errno 48] Address already in use\n",
        encoding="utf-8",
    )

    enriched = _replay_service_failure_with_stderr(
        TimeoutError("replay service readiness timed out"),
        stderr_path=stderr_path,
    )

    assert isinstance(enriched, TimeoutError)
    assert "Address already in use" in str(enriched)
    assert "/private/tmp/runtime.py" not in str(enriched)
    assert "<LOCAL_PATH>" in str(enriched)


def test_replay_service_protocol_trace_is_bounded_and_sanitized(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scratch" / "protocol_trace.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(
        ("x" * 80_000)
        + '\n{"direction":"client_to_runtime","token=very-secret":"ignored"}'
        + '\n{"direction":"runtime_to_client","path":"/Users/me/private.json"}\n',
        encoding="utf-8",
    )
    destination = tmp_path / "diagnostics" / "protocol_trace.log"

    assert _preserve_replay_service_protocol_trace(source, destination) is True

    preserved = destination.read_text(encoding="utf-8")
    assert len(preserved) <= 64 * 1024
    assert "very-secret" not in preserved
    assert "<REDACTED_SECRET>" in preserved
    assert "/Users/me/private.json" not in preserved
    assert "<LOCAL_PATH>" in preserved


def test_failed_replay_includes_preserved_protocol_trace_diagnostics(
    tmp_path: Path,
) -> None:
    trace = (
        tmp_path
        / "artifacts"
        / "replay_services"
        / "recorded-endpoint"
        / "protocol_trace.log"
    )
    trace.parent.mkdir(parents=True)
    trace.write_text(
        '{"direction":"client_to_runtime","fields":["id","channel"]}\n'
        '{"direction":"runtime_to_client","fields":["id"]}\n',
        encoding="utf-8",
    )
    result = ReplayExecutionResult(
        status="failed",
        trajectory=[],
        failure={
            "type": "TimeoutExpired",
            "reason": "replay timed out",
            "outcome": "candidate_failure",
        },
    )

    attached = _attach_replay_service_protocol_diagnostics(
        result,
        artifact_dir=tmp_path / "artifacts",
    )

    assert attached.failure is not None
    assert attached.failure["outcome"] == "candidate_failure"
    assert attached.failure["diagnostics"]["replay_service_protocol_traces"] == [
        {
            "path": "replay_services/recorded-endpoint/protocol_trace.log",
            "tail": (
                '{"direction":"client_to_runtime","fields":["id","channel"]}\n'
                '{"direction":"runtime_to_client","fields":["id"]}'
            ),
        }
    ]


def test_protocol_trace_diagnostics_keep_terminal_interactions(
    tmp_path: Path,
) -> None:
    trace = (
        tmp_path
        / "artifacts"
        / "replay_services"
        / "recorded-endpoint"
        / "protocol_trace.log"
    )
    trace.parent.mkdir(parents=True)
    trace.write_text(
        ("old-interaction\n" * 600)
        + '{"sequence":999,"kind":"terminal_unmatched_request"}\n',
        encoding="utf-8",
    )
    result = ReplayExecutionResult(
        status="failed",
        trajectory=[],
        failure={"type": "TimeoutExpired", "reason": "replay timed out"},
    )

    attached = _attach_replay_service_protocol_diagnostics(
        result,
        artifact_dir=tmp_path / "artifacts",
    )

    assert attached.failure is not None
    tail = attached.failure["diagnostics"][
        "replay_service_protocol_traces"
    ][0]["tail"]
    assert len(tail) <= 4_000
    assert "terminal_unmatched_request" in tail
    assert tail.endswith('{"sequence":999,"kind":"terminal_unmatched_request"}')


def test_replay_service_protocol_trace_contract_requires_bidirectional_records(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "protocol_trace.jsonl"
    trace.write_text(
        json.dumps(
            {
                "direction": "in",
                "sequence": 1,
                "kind": "request",
                "fields": ["id", "method"],
                "correlation": {"id": 1},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ReplayServiceProtocolError,
        match="must record both received and emitted interactions",
    ):
        _validate_replay_service_protocol_trace(trace)


def test_replay_service_protocol_trace_contract_accepts_sanitized_summary(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "protocol_trace.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "direction": "inbound",
                    "sequence": 1,
                    "kind": "request",
                    "fields": ["id", "method", "sessionId"],
                    "correlation": {"id": 1, "sessionId": "opaque"},
                },
                {
                    "direction": "outbound",
                    "sequence": 2,
                    "kind": "response",
                    "fields": ["id", "result", "sessionId"],
                    "correlation": {"id": 1, "sessionId": "opaque"},
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    _validate_replay_service_protocol_trace(trace)


def test_replay_service_protocol_trace_contract_accepts_recv_send_directions(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "protocol_trace.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "direction": "recv",
                    "sequence": 1,
                    "kind": "request",
                    "fields": ["id", "method"],
                    "correlation": {"id": 1},
                },
                {
                    "direction": "send",
                    "sequence": 2,
                    "kind": "response",
                    "fields": ["id", "result"],
                    "correlation": {"id": 1},
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    _validate_replay_service_protocol_trace(trace)


def test_replay_service_protocol_trace_contract_rejects_missing_trace(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ReplayServiceProtocolError,
        match="did not write protocol_trace.jsonl",
    ):
        _validate_replay_service_protocol_trace(
            tmp_path / "protocol_trace.jsonl"
        )


@pytest.mark.asyncio
async def test_skill_owned_replay_service_is_isolated_per_variant(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "frozen" / "runtime" / "replay" / "runtime.py"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(
        """
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer

parser = argparse.ArgumentParser()
parser.add_argument('--port', required=True, type=int)
args = parser.parse_args()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'candidate-controlled-response')
    def log_message(self, *args):
        pass

HTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
""",
        encoding="utf-8",
    )
    fixtures = tmp_path / "frozen" / "fixtures"
    fixtures.mkdir(parents=True)
    response_fixture = fixtures / "recording.txt"
    response_fixture.write_bytes(b"recorded")
    frozen = FrozenReplayCapability(
        capability_id="recorded-http",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(tmp_path / "frozen"),
        handled_requirements=("requirement-local",),
        unhandled_requirements=(),
        evidence_refs={"requirement-local": ("context:task-1",)},
        fixture_evidence_refs={"recording.txt": ("context:task-1",)},
        fixtures=(
            FrozenReplayFile(
                path="recording.txt",
                sha256="sha256:"
                + hashlib.sha256(response_fixture.read_bytes()).hexdigest(),
                size=response_fixture.stat().st_size,
            ),
        ),
        runtime_files=(
            FrozenReplayFile(
                path="replay/runtime.py",
                sha256="sha256:" + hashlib.sha256(runtime.read_bytes()).hexdigest(),
                size=runtime.stat().st_size,
            ),
        ),
        endpoint_replacements={
            "http://127.0.0.1:9222": "recorded-http",
        },
        services=(
            ReplayServiceSpec(
                service_id="recorded-http",
                requirement_id="requirement-local",
                transport="http_fixture",
                response_fixture="recording.txt",
                readiness=ReplayReadinessProbe(kind="tcp", timeout_seconds=2),
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )
    frozen_payload = {
        "schema_version": "aworld.replay.capability_result.v1",
        "capability_id": frozen.capability_id,
        "capability_package_fingerprint": frozen.capability_package_fingerprint,
        "request_fingerprint": frozen.request_fingerprint,
        "handled_requirements": list(frozen.handled_requirements),
        "unhandled_requirements": list(frozen.unhandled_requirements),
        "evidence_refs": frozen.evidence_refs,
        "fixture_evidence_refs": frozen.fixture_evidence_refs,
        "fixtures": [asdict(item) for item in frozen.fixtures],
        "runtime_files": [asdict(item) for item in frozen.runtime_files],
        "endpoint_replacements": frozen.endpoint_replacements,
        "services": [asdict(item) for item in frozen.services],
        "deterministic": frozen.deterministic,
    }
    frozen_fingerprint = "sha256:" + hashlib.sha256(
        json.dumps(
            frozen_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    frozen = replace(frozen, fingerprint=frozen_fingerprint)
    (tmp_path / "frozen/frozen_manifest.json").write_text(
        json.dumps(
            {**frozen_payload, "fingerprint": frozen_fingerprint},
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    class LocalEndpointAdapter:
        adapter_id = "test.local-endpoint"

        def bind(self, dependency, *, context):
            if dependency.kind != "local_endpoint":
                return None
            return ReplayAdapterBinding(
                adapter_id=self.adapter_id,
                dependency_id=dependency.identifier,
                deterministic=True,
            )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="task-1",
                input={"content": "Inspect http://127.0.0.1:9222"},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    adaptation = ReplayAdaptationCompiler(
        adapters=(LocalEndpointAdapter(),)
    ).compile(
        dataset=dataset,
        workspace_root=tmp_path,
        artifact_root=tmp_path / "adaptation",
    )
    adaptation = replace(adaptation, replay_capability=frozen)
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-service-isolation",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        replay_adaptation=adaptation,
    )
    observed_ports: list[int] = []

    async def fake_executor(
        execution_request: ReplayExecutionRequest,
    ) -> ReplayExecutionResult:
        url = execution_request.task_input["content"].split()[-1]
        port = int(url.rsplit(":", 1)[1])
        observed_ports.append(port)
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            connection.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
            response = connection.recv(4096)
            assert b"recorded" in response
            assert str(execution_request.artifact_dir).encode() not in response
            assert b"baseline" not in response
            assert b"cand-1" not in response
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": execution_request.variant_id}}],
        )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    assert result.succeeded is True
    assert result.baseline.metrics["frozen_capability_fingerprint"] == (
        result.candidate.metrics["frozen_capability_fingerprint"]
    )
    assert result.baseline.metrics["service_endpoint"] != (
        result.candidate.metrics["service_endpoint"]
    )
    assert result.baseline.metrics["service_cleanup_status"] == "stopped"
    assert result.candidate.metrics["service_cleanup_status"] == "stopped"
    assert len(observed_ports) == 2
    assert len(set(observed_ports)) == 2
    for port in observed_ports:
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.1)


def test_paired_replay_dataset_maps_baseline_and_candidate_trajectories() -> None:
    baseline_trajectory = [
        {"state": {"input": {"content": "task"}}, "action": {"content": "old"}, "reward": {}}
    ]
    candidate_trajectory = [
        {"state": {"input": {"content": "task"}}, "action": {"content": "new"}, "reward": {}}
    ]
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="task-1",
                input={"content": "task"},
                metadata={"baseline_trajectory": baseline_trajectory},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-1",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input={"content": "task"},
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="succeeded",
            trajectory=baseline_trajectory,
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=candidate_trajectory,
            metrics={"latency_ms": 120.0},
        ),
    )

    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=replay,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
    )

    assert paired.cases[0].metadata["variant_trajectories"]["baseline"] == baseline_trajectory
    assert paired.cases[0].metadata["variant_trajectories"]["cand-1"] == candidate_trajectory
    assert paired.cases[0].metadata["replay"]["candidate"]["metrics"]["latency_ms"] == 120.0


def test_build_replay_request_skips_framework_generated_eval_cases(tmp_path: Path) -> None:
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="framework-evaluator-case",
                input={
                    "content": json.dumps(
                        {
                            "evaluation_runtime_contract": {
                                "do_not_call_external_tools": True,
                                "trajectory_log_path": str(
                                    tmp_path
                                    / ".aworld"
                                    / "self_evolve"
                                    / "evaluator"
                                    / "old-run"
                                    / "trajectory.log"
                                ),
                            },
                            "report_output_path": str(tmp_path / "report.json"),
                        }
                    )
                },
                metadata={"framework_meta_trajectory": True},
            ),
            EvalCase(
                case_id="user-task",
                input={"content": "Summarize the referenced page with grounded citations."},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["framework-evaluator-case", "user-task"], "validation": [], "held_out": []},
        ),
    )

    request = build_replay_request(
        run_id="run-1",
        workspace_root=tmp_path,
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    assert request.task_id == "user-task"
    assert request.task_input == {"content": "Summarize the referenced page with grounded citations."}


def test_build_replay_request_rejects_framework_only_dataset(tmp_path: Path) -> None:
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="framework-evaluator-case",
                input={
                    "content": (
                        "evaluation_runtime_contract: do_not_call_external_tools=true "
                        f"trajectory_log_path={tmp_path}/.aworld/self_evolve/evaluator/run/trajectory.log"
                    )
                },
                metadata={"framework_meta_trajectory": True},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["framework-evaluator-case"], "validation": [], "held_out": []},
        ),
    )

    with pytest.raises(ValueError, match="user task eval case"):
        build_replay_request(
            run_id="run-1",
            workspace_root=tmp_path,
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
            overlay_skill_root=tmp_path / "overlay-skills",
            dataset=dataset,
        )


def test_paired_replay_dataset_expands_repetition_trajectories_into_eval_cases() -> None:
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input={"content": "task"}),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    baseline_1 = ReplayVariantResult(
        variant_id="baseline-1",
        status="succeeded",
        trajectory=[{"action": {"content": "baseline-1"}}],
    )
    baseline_2 = ReplayVariantResult(
        variant_id="baseline-2",
        status="succeeded",
        trajectory=[{"action": {"content": "baseline-2"}}],
    )
    candidate_1 = ReplayVariantResult(
        variant_id="cand-1-1",
        status="succeeded",
        trajectory=[{"action": {"content": "candidate-1"}}],
    )
    candidate_2 = ReplayVariantResult(
        variant_id="cand-1-2",
        status="succeeded",
        trajectory=[{"action": {"content": "candidate-2"}}],
    )
    candidate_3 = ReplayVariantResult(
        variant_id="cand-1-3",
        status="succeeded",
        trajectory=[{"action": {"content": "candidate-3"}}],
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-1",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input={"content": "task"},
            baseline_repetitions=2,
            candidate_repetitions=3,
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="succeeded",
            trajectory=baseline_2.trajectory,
            metrics={"repetition_count": 2, "successful_repetition_count": 2},
            repetition_results=(baseline_1, baseline_2),
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=candidate_3.trajectory,
            metrics={"repetition_count": 3, "successful_repetition_count": 3},
            repetition_results=(candidate_1, candidate_2, candidate_3),
        ),
    )

    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=replay,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
    )

    assert [case.case_id for case in paired.cases] == [
        "task-1__replay_1",
        "task-1__replay_2",
        "task-1__replay_3",
    ]
    assert [
        case.metadata["variant_trajectories"]["baseline"][0]["action"]["content"]
        for case in paired.cases
    ] == ["baseline-1", "baseline-2", "baseline-1"]
    assert [
        case.metadata["variant_trajectories"]["cand-1"][0]["action"]["content"]
        for case in paired.cases
    ] == ["candidate-1", "candidate-2", "candidate-3"]
    assert paired.recipe.source["paired_replay"] is True
    assert paired.recipe.source["original_case_count"] == 1
    assert paired.recipe.source["replay_case_count"] == 3
    assert paired.recipe.splits["train"] == [
        "task-1__replay_1",
        "task-1__replay_2",
        "task-1__replay_3",
    ]


def test_load_candidate_replay_result_restores_repetition_artifacts(tmp_path: Path) -> None:
    replay_dir = tmp_path / "replay" / "cand-1"
    request = CandidateReplayRequest(
        run_id="run-1",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input={"content": "task"},
        baseline_repetitions=2,
        candidate_repetitions=3,
    )
    _write_json(replay_dir / "request.json", request)
    for variant_root, base_variant_id, count in (
        (replay_dir / "baseline", "baseline", 2),
        (replay_dir / "cand-1", "cand-1", 3),
    ):
        variant_root.mkdir(parents=True)
        _write_json(
            variant_root / "aggregate_metrics.json",
            {
                "repetition_count": count,
                "successful_repetition_count": count,
                "failed_repetition_count": 0,
            },
        )
        for index in range(1, count + 1):
            repetition_dir = variant_root / str(index)
            repetition_dir.mkdir()
            (repetition_dir / "stdout.txt").write_text("", encoding="utf-8")
            (repetition_dir / "stderr.txt").write_text("", encoding="utf-8")
            _write_json(repetition_dir / "metrics.json", {"returncode": 0})
            _write_json(
                repetition_dir / "trajectory.json",
                [{"action": {"content": f"{base_variant_id}-{index}"}}],
            )

    loaded = load_candidate_replay_result(replay_dir)

    assert loaded.request.candidate_id == "cand-1"
    assert loaded.succeeded is True
    assert len(loaded.baseline.repetition_results) == 2
    assert len(loaded.candidate.repetition_results) == 3
    assert loaded.candidate.trajectory[0]["action"]["content"] == "cand-1-3"


def test_load_candidate_replay_result_prefers_successful_single_evidence_retry(
    tmp_path: Path,
) -> None:
    replay_dir = tmp_path / "replay" / "cand-1"
    request = CandidateReplayRequest(
        run_id="run-single-retry",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input={"content": "task"},
    )
    _write_json(replay_dir / "request.json", request)
    baseline_dir = replay_dir / "baseline"
    _write_json(
        baseline_dir / "trajectory.json",
        [{"action": {"content": "compacted baseline"}}],
    )
    _write_json(
        baseline_dir / "failure.json",
        {"reason": "evidence_quality_failed"},
    )
    retry_dir = baseline_dir / "evidence_retry_2"
    _write_json(
        retry_dir / "trajectory.json",
        [{"action": {"content": "complete baseline"}}],
    )
    _write_json(retry_dir / "metrics.json", {"evidence_strategy_passed": True})
    candidate_dir = replay_dir / "cand-1"
    _write_json(
        candidate_dir / "trajectory.json",
        [{"action": {"content": "candidate"}}],
    )
    _write_json(candidate_dir / "metrics.json", {})

    loaded = load_candidate_replay_result(replay_dir)

    assert loaded.baseline.succeeded is True
    assert loaded.baseline.trajectory[0]["action"]["content"] == "complete baseline"
    assert loaded.succeeded is True


def test_paired_replay_dataset_requires_successful_candidate_replay() -> None:
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input="task"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-1",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input="task",
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="succeeded",
            trajectory=[],
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="failed",
            trajectory=[],
            failure={"reason": "missing browser"},
        ),
    )

    with pytest.raises(ValueError, match="candidate replay did not succeed"):
        build_paired_replay_dataset(
            dataset=dataset,
            replay_result=replay,
            candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
        )


def test_paired_replay_dataset_rejects_source_trajectory_baseline_fallback() -> None:
    source_trajectory = [
        {
            "state": {"input": {"content": "task"}},
            "action": {"content": "baseline did not finish"},
            "reward": {"status": "failed"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=source_trajectory,
        task_id="task-1",
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-task-failure",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input="task",
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="failed",
            trajectory=[],
            failure={"type": "TimeoutExpired", "reason": "replay timed out"},
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=[{"action": {"content": "candidate completed"}}],
        ),
    )

    assert candidate_replay_is_comparable(dataset=dataset, replay_result=replay) is False
    with pytest.raises(ValueError, match="comparable paired outcomes"):
        build_paired_replay_dataset(
            dataset=dataset,
            replay_result=replay,
            candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
        )


def test_paired_replay_dataset_rejects_infrastructure_baseline_failure() -> None:
    source_trajectory = [
        {
            "state": {"input": {"content": "task"}},
            "action": {"content": "baseline"},
            "reward": {"status": "failed"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=source_trajectory,
        task_id="task-1",
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-infrastructure-failure",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input="task",
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="failed",
            trajectory=[],
            failure={"type": "ProcessError", "reason": "aworld-cli run failed"},
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=[{"action": {"content": "candidate completed"}}],
        ),
    )

    assert candidate_replay_is_comparable(dataset=dataset, replay_result=replay) is False
    with pytest.raises(ValueError, match="comparable paired outcomes"):
        build_paired_replay_dataset(
            dataset=dataset,
            replay_result=replay,
            candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
        )


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_aggregates_repetitions(
    tmp_path: Path,
) -> None:
    calls = []
    scores = {
        "baseline-1": 0.4,
        "baseline-2": 0.6,
        "cand-1-1": 0.8,
        "cand-1-2": 0.9,
        "cand-1-3": 1.0,
    }

    async def fake_executor(request):
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={"score": scores[request.variant_id]},
        )

    request = CandidateReplayRequest(
        run_id="run-repetitions",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=2,
        candidate_repetitions=3,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert [call.variant_id for call in calls] == [
        "baseline-1",
        "baseline-2",
        "cand-1-1",
        "cand-1-2",
        "cand-1-3",
    ]
    assert result.baseline.variant_id == "baseline"
    assert result.baseline.metrics["repetition_count"] == 2
    assert result.baseline.metrics["score"] == pytest.approx(0.5)
    assert result.candidate.variant_id == "cand-1"
    assert result.candidate.metrics["repetition_count"] == 3
    assert result.candidate.metrics["score"] == pytest.approx(0.9)
    assert [item.variant_id for item in result.candidate.repetition_results] == [
        "cand-1-1",
        "cand-1-2",
        "cand-1-3",
    ]
    assert result.candidate.trajectory[0]["action"]["content"] == "cand-1-3"


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_does_not_reuse_legacy_baseline_without_provenance(
    tmp_path: Path,
) -> None:
    baseline_dir = tmp_path / "stored-baseline"
    (baseline_dir / "1").mkdir(parents=True)
    (baseline_dir / "1" / "trajectory.json").write_text(
        json.dumps([{"action": {"content": "stored baseline"}}]),
        encoding="utf-8",
    )
    (baseline_dir / "1" / "metrics.json").write_text(
        json.dumps({"score": 0.7}),
        encoding="utf-8",
    )
    (baseline_dir / "2").mkdir(parents=True)
    (baseline_dir / "2" / "trajectory.json").write_text(
        json.dumps([{"action": {"content": "stored baseline selected"}}]),
        encoding="utf-8",
    )
    (baseline_dir / "2" / "metrics.json").write_text(
        json.dumps({"score": 0.9}),
        encoding="utf-8",
    )
    (baseline_dir / "aggregate_metrics.json").write_text(
        json.dumps(
            {
                "repetition_count": 2,
                "successful_repetition_count": 2,
                "score": 0.8,
            }
        ),
        encoding="utf-8",
    )

    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request):
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
            metrics={"score": 1.0},
        )

    request = CandidateReplayRequest(
        run_id="run-baseline-reuse",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=2,
        candidate_repetitions=3,
        baseline_replay_dir=str(baseline_dir),
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert [call.variant_id for call in calls] == [
        "baseline-1",
        "baseline-2",
        "cand-1-1",
        "cand-1-2",
        "cand-1-3",
    ]
    assert result.baseline.succeeded is True
    assert result.baseline.metrics["repetition_count"] == 2
    assert result.baseline.trajectory[0]["action"]["content"] == "baseline-2"
    assert result.candidate.succeeded is True


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_allows_partial_repetition_success(
    tmp_path: Path,
) -> None:
    async def fake_executor(request):
        if request.variant_id == "baseline-2":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={"type": "TimeoutExpired", "reason": "replay timed out"},
                metrics={"latency_ms": 600000},
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={"latency_ms": 1000},
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input="Replay this task"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-partial-repetitions",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=2,
        candidate_repetitions=3,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=dataset,
    )

    assert result.succeeded is True
    assert result.baseline.succeeded is True
    assert result.baseline.metrics["repetition_count"] == 2
    assert result.baseline.metrics["successful_repetition_count"] == 1
    assert result.baseline.metrics["failed_repetition_count"] == 1
    assert result.baseline.metrics["repetition_failures"] == [
        {"type": "TimeoutExpired", "reason": "replay timed out"}
    ]
    assert result.baseline.trajectory[0]["action"]["content"] == "baseline-1"
    assert result.baseline.failure is None

    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=result,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
    )

    assert [case.case_id for case in paired.cases] == [
        "task-1__replay_1",
        "task-1__replay_2",
        "task-1__replay_3",
    ]
    assert {
        case.metadata["variant_trajectories"]["baseline"][0]["action"]["content"]
        for case in paired.cases
    } == {"baseline-1"}
    assert paired.cases[0].metadata["replay"]["baseline"]["metrics"][
        "failed_repetition_count"
    ] == 1


@pytest.mark.asyncio
async def test_multi_member_replay_executes_and_maps_each_member_independently(
    tmp_path: Path,
) -> None:
    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {
                        "content": f"{request.task_id}:{request.variant_id}"
                    },
                    "reward": {"status": "ok"},
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={
                "train": ["task-a"],
                "validation": [],
                "held_out": ["task-b"],
            },
            trainable_case_ids=("task-a",),
            held_out_case_ids=("task-b",),
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\n",
        candidate_id="cand-1",
    )
    request = build_replay_request(
        run_id="run-members",
        workspace_root=tmp_path,
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )

    assert [(call.task_id, call.variant_id) for call in calls] == [
        ("task-a", "baseline"),
        ("task-b", "baseline"),
        ("task-a", "cand-1"),
        ("task-b", "cand-1"),
    ]
    assert [member.case_id for member in result.member_results] == [
        "task-a",
        "task-b",
    ]
    assert len({Path(call.artifact_dir) for call in calls}) == 4
    assert len({Path(call.artifact_dir).parent for call in calls}) == 2

    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=result,
        candidate=candidate,
    )

    assert [case.case_id for case in paired.cases] == ["task-a", "task-b"]
    for case in paired.cases:
        variants = case.metadata["variant_trajectories"]
        assert variants["baseline"][0]["action"]["content"] == (
            f"{case.case_id}:baseline"
        )
        assert variants["cand-1"][0]["action"]["content"] == (
            f"{case.case_id}:cand-1"
        )
    assert paired.recipe.splits == {
        "train": ["task-a"],
        "validation": [],
        "held_out": ["task-b"],
    }
    assert paired.recipe.trainable_case_ids == ("task-a",)
    assert paired.recipe.held_out_case_ids == ("task-b",)


@pytest.mark.asyncio
async def test_multi_member_replay_distributes_repetition_budget_across_members(
    tmp_path: Path,
) -> None:
    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": f"{request.task_id}:{request.variant_id}"},
                    "reward": {"status": "ok"},
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=tuple(
            EvalCase(case_id=f"task-{index}", input=f"Replay task {index}")
            for index in range(1, 5)
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 4},
            split_seed="seed",
            splits={
                "train": ["task-1", "task-2"],
                "validation": ["task-3"],
                "held_out": ["task-4"],
            },
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-distributed-repetitions",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
        baseline_repetitions=2,
        candidate_repetitions=3,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )

    assert [(call.task_id, call.variant_id) for call in calls] == [
        ("task-1", "baseline"),
        ("task-2", "baseline"),
        ("task-3", "baseline"),
        ("task-4", "baseline"),
        ("task-1", "cand-1"),
        ("task-2", "cand-1"),
        ("task-3", "cand-1"),
        ("task-4", "cand-1"),
    ]
    assert result.baseline.metrics["repetition_count"] == 4
    assert result.candidate.metrics["repetition_count"] == 4
    assert all(
        member.baseline.metrics["repetition_count"] == 1
        and member.candidate.metrics["repetition_count"] == 1
        for member in result.member_results
    )


@pytest.mark.asyncio
async def test_multi_member_replay_stops_after_shared_baseline_infrastructure_failure(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append((request.task_id, request.variant_id))
        if request.task_id == "task-a" and request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={
                    "type": "TimeoutExpired",
                    "reason": "replay timed out",
                    "outcome": "infrastructure_failure",
                    "diagnostics": {
                        "task_artifacts": [
                            {
                                "path": "artifact/workspace/scrape.log",
                                "tail": "CDP endpoint does not implement /json/version",
                            }
                        ]
                    },
                },
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.task_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=tuple(
            EvalCase(case_id=f"task-{suffix}", input=f"Replay task {suffix}")
            for suffix in ("a", "b", "c")
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 3},
            split_seed="seed",
            splits={
                "train": ["task-a", "task-b"],
                "validation": [],
                "held_out": ["task-c"],
            },
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-baseline-infrastructure-fail-fast",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    assert calls == [("task-a", "baseline")]
    assert [member.case_id for member in result.member_results] == [
        "task-a",
        "task-b",
        "task-c",
    ]
    assert result.member_results[0].baseline.failure["diagnostics"] == {
        "task_artifacts": [
            {
                "path": "artifact/workspace/scrape.log",
                "tail": "CDP endpoint does not implement /json/version",
            }
        ]
    }
    for member in result.member_results[1:]:
        assert member.baseline.failure == {
            "outcome": "infrastructure_failure",
            "reason": "baseline_preflight_aborted",
            "detail": (
                "baseline replay skipped because shared replay infrastructure "
                "failed for task-a"
            ),
            "blocked_by_case_id": "task-a",
        }
    assert all(
        member.candidate.failure["reason"] == "baseline_preflight_failed"
        for member in result.member_results
    )


@pytest.mark.asyncio
async def test_single_member_replay_skips_candidate_after_capability_preflight_failure(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request.variant_id)
        return ReplayExecutionResult(
            status="failed",
            trajectory=[],
            failure={
                "type": "ReplayServiceProtocolError",
                "reason": "advertised WebSocket handshake requires HTTP/1.1",
                "outcome": "candidate_failure",
            },
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-a", input="Replay task A"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-a"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-single-capability-preflight",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    assert calls == ["baseline"]
    assert result.baseline.failure["outcome"] == "candidate_failure"
    assert result.candidate.failure["reason"] == "baseline_preflight_failed"


@pytest.mark.asyncio
async def test_single_member_replay_runs_candidate_after_rollout_capability_failure(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request.variant_id)
        if request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={
                    "type": "TimeoutExpired",
                    "reason": "replay timed out",
                    "outcome": "infrastructure_failure",
                    "failure_class": "candidate_replay_capability",
                    "failure_stage": "task_rollout",
                    "repairable": True,
                    "diagnostics": {
                        "task_artifacts": [
                            {
                                "path": "artifact/protocol.json",
                                "tail": "recorded data plane is incomplete",
                            }
                        ]
                    },
                },
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": "candidate completed"}}],
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-a", input="Replay task A"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-a"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-single-rollout-capability-failure",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    assert calls == ["baseline", "cand-1"]
    assert result.baseline.succeeded is False
    assert result.candidate.succeeded is True
    assert candidate_replay_is_comparable(dataset=dataset, replay_result=result)


@pytest.mark.asyncio
async def test_multi_member_replay_reports_failed_case_without_masking_it(
    tmp_path: Path,
) -> None:
    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        if request.task_id == "task-b" and request.variant_id == "cand-1":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={"type": "TaskFailure", "reason": "task-b failed"},
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.task_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-member-failure",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    assert result.succeeded is False
    assert result.candidate.status == "failed"
    assert result.candidate.metrics["successful_member_count"] == 1
    assert result.candidate.metrics["failed_member_count"] == 1
    assert result.candidate.metrics["member_failures"] == [
        {
            "case_id": "task-b",
            "failure": {"type": "TaskFailure", "reason": "task-b failed"},
        }
    ]


@pytest.mark.asyncio
async def test_load_candidate_replay_result_restores_multi_member_mapping(
    tmp_path: Path,
) -> None:
    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {
                        "content": f"{request.task_id}:{request.variant_id}"
                    },
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a"], "validation": [], "held_out": ["task-b"]},
            trainable_case_ids=("task-a",),
            held_out_case_ids=("task-b",),
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-load-members",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
        baseline_repetitions=2,
        candidate_repetitions=2,
    )
    await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )
    replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-load-members"
        / "replay"
        / "cand-1"
    )

    loaded = load_candidate_replay_result(replay_dir)

    assert loaded.succeeded is True
    assert [member.case_id for member in loaded.member_results] == [
        "task-a",
        "task-b",
    ]
    assert all(
        len(member.baseline.repetition_results) == 0
        and member.baseline.metrics["repetition_count"] == 1
        and len(member.candidate.repetition_results) == 0
        and member.candidate.metrics["repetition_count"] == 1
        for member in loaded.member_results
    )
    assert loaded.baseline.metrics["repetition_count"] == 2
    assert loaded.candidate.metrics["repetition_count"] == 2
    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=loaded,
        candidate=candidate,
    )
    assert {
        case.case_id.split("__replay_", 1)[0]: case.metadata[
            "variant_trajectories"
        ]["cand-1"][0]["action"]["content"].split(":", 1)[0]
        for case in paired.cases
    } == {"task-a": "task-a", "task-b": "task-b"}


@pytest.mark.asyncio
async def test_multi_member_replay_reuses_each_members_baseline(
    tmp_path: Path,
) -> None:
    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"], "validation": [], "held_out": []},
        ),
    )
    first_candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nFirst.\n",
        candidate_id="cand-1",
    )
    replay_adaptation = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=tmp_path,
        artifact_root=tmp_path / ".aworld" / "self_evolve" / "run-reuse-members" / "adaptation",
    )
    first_request = build_replay_request(
        run_id="run-reuse-members",
        workspace_root=tmp_path,
        target=first_candidate.target,
        candidate=first_candidate,
        overlay_skill_root=tmp_path / "overlay-1",
        dataset=dataset,
        replay_adaptation=replay_adaptation,
    )
    backend = AWorldCliCandidateReplayBackend(executor=fake_executor)
    await backend.replay_candidate(
        first_request,
        candidate=first_candidate,
        dataset=dataset,
    )
    calls.clear()
    second_candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nSecond.\n",
        candidate_id="cand-2",
    )
    members_root = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-reuse-members"
        / "replay"
        / "cand-1"
        / "members"
    )
    second_request = build_replay_request(
        run_id="run-reuse-members",
        workspace_root=tmp_path,
        target=second_candidate.target,
        candidate=second_candidate,
        overlay_skill_root=tmp_path / "overlay-2",
        dataset=dataset,
        baseline_replay_dir=members_root,
        replay_adaptation=replay_adaptation,
    )

    result = await backend.replay_candidate(
        second_request,
        candidate=second_candidate,
        dataset=dataset,
    )

    assert result.succeeded is True
    assert [(call.task_id, call.variant_id) for call in calls] == [
        ("task-a", "cand-2"),
        ("task-b", "cand-2"),
    ]
    assert all(member.baseline.succeeded for member in result.member_results)
    second_replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-reuse-members"
        / "replay"
        / "cand-2"
    )
    loaded = load_candidate_replay_result(second_replay_dir)
    assert loaded.succeeded is True
    assert [member.case_id for member in loaded.member_results] == [
        "task-a",
        "task-b",
    ]
    assert all(member.baseline.succeeded for member in loaded.member_results)


@pytest.mark.asyncio
async def test_multi_member_replay_reuses_successful_baselines_and_retries_failed_member(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    baseline_attempts: dict[str, int] = {}

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append((request.task_id, request.variant_id))
        if request.variant_id == "baseline":
            baseline_attempts[request.task_id] = (
                baseline_attempts.get(request.task_id, 0) + 1
            )
            if request.task_id == "task-b" and baseline_attempts[request.task_id] == 1:
                return ReplayExecutionResult(
                    status="failed",
                    trajectory=[],
                    failure={"type": "TimeoutExpired", "reason": "replay timed out"},
                )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": f"{request.task_id}:{request.variant_id}"},
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"], "validation": [], "held_out": []},
        ),
    )
    backend = AWorldCliCandidateReplayBackend(executor=fake_executor)
    replay_adaptation = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=tmp_path,
        artifact_root=(
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-partial-member-cache"
            / "adaptation"
        ),
    )
    first_candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nFirst.\n",
        candidate_id="cand-1",
    )
    first_request = build_replay_request(
        run_id="run-partial-member-cache",
        workspace_root=tmp_path,
        target=first_candidate.target,
        candidate=first_candidate,
        overlay_skill_root=tmp_path / "overlay-1",
        dataset=dataset,
        replay_adaptation=replay_adaptation,
    )

    first_result = await backend.replay_candidate(
        first_request,
        candidate=first_candidate,
        dataset=dataset,
    )

    assert first_result.baseline.succeeded is False
    members_root = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-partial-member-cache"
        / "replay"
        / "cand-1"
        / "members"
    )
    calls.clear()
    second_candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nSecond.\n",
        candidate_id="cand-2",
    )
    second_request = build_replay_request(
        run_id="run-partial-member-cache",
        workspace_root=tmp_path,
        target=second_candidate.target,
        candidate=second_candidate,
        overlay_skill_root=tmp_path / "overlay-2",
        dataset=dataset,
        baseline_replay_dir=members_root,
        replay_adaptation=replay_adaptation,
    )

    second_result = await backend.replay_candidate(
        second_request,
        candidate=second_candidate,
        dataset=dataset,
    )

    assert second_result.baseline.succeeded is True
    assert calls == [
        ("task-b", "baseline"),
        ("task-a", "cand-2"),
        ("task-b", "cand-2"),
    ]


@pytest.mark.asyncio
async def test_multi_member_replay_stops_before_candidates_when_baseline_preflight_fails(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append((request.task_id, request.variant_id))
        if request.task_id == "task-b" and request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={"reason": "replay_compacted_argument_unavailable"},
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nCandidate.\n",
        candidate_id="cand-1",
    )
    request = build_replay_request(
        run_id="run-baseline-preflight",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )

    assert calls == [("task-a", "baseline"), ("task-b", "baseline")]
    assert result.baseline.succeeded is False
    assert all(
        member.candidate.failure
        == {
            "reason": "baseline_preflight_failed",
            "detail": "candidate replay skipped because baseline infrastructure replay failed",
        }
        for member in result.member_results
    )


def test_member_baseline_replay_dir_maps_legacy_member_root_without_manifest(
    tmp_path: Path,
) -> None:
    members_root = tmp_path / "members"
    case_id = "task-a"
    member_dir = members_root / _member_artifact_name(case_id)
    member_dir.mkdir(parents=True)
    (member_dir / "request.json").write_text(
        json.dumps(
            {
                "run_id": "old-run",
                "task_id": case_id,
                "workspace_root": str(tmp_path),
                "target": {"target_type": "skill", "target_id": "demo"},
                "candidate_id": "cand-1",
                "overlay_skill_root": str(tmp_path / "overlay"),
                "task_input": "Replay task A",
            }
        ),
        encoding="utf-8",
    )

    assert _member_baseline_replay_dir(str(members_root), case_id) == str(
        member_dir / "baseline"
    )


def test_member_baseline_replay_dir_rejects_mismatched_chained_baseline(
    tmp_path: Path,
) -> None:
    members_root = tmp_path / "members"
    case_id = "task-b"
    member_name = _member_artifact_name(case_id)
    member_dir = members_root / member_name
    member_dir.mkdir(parents=True)
    stale_replay_root = tmp_path / "old-replay"
    stale_baseline = stale_replay_root / "baseline"
    stale_baseline.mkdir(parents=True)
    (stale_replay_root / "request.json").write_text(
        json.dumps({"task_id": "task-a"}),
        encoding="utf-8",
    )
    (member_dir / "request.json").write_text(
        json.dumps(
            {
                "task_id": case_id,
                "baseline_replay_dir": str(stale_baseline),
            }
        ),
        encoding="utf-8",
    )
    (members_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld.self_evolve.member_replay.v1",
                "members": [
                    {
                        "case_id": case_id,
                        "path": member_name,
                        "succeeded": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert _member_baseline_replay_dir(str(members_root), case_id) is None


def test_member_baseline_replay_dir_follows_matching_chained_baseline(
    tmp_path: Path,
) -> None:
    members_root = tmp_path / "members"
    case_id = "task-a"
    member_name = _member_artifact_name(case_id)
    member_dir = members_root / member_name
    member_dir.mkdir(parents=True)
    prior_replay_root = tmp_path / "old-replay"
    prior_baseline = prior_replay_root / "baseline"
    prior_baseline.mkdir(parents=True)
    (prior_baseline / "trajectory.json").write_text(
        json.dumps([{"action": {"content": "stored baseline"}}]),
        encoding="utf-8",
    )
    (prior_baseline / "metrics.json").write_text("{}\n", encoding="utf-8")
    (prior_replay_root / "request.json").write_text(
        json.dumps({"task_id": case_id}),
        encoding="utf-8",
    )
    (member_dir / "request.json").write_text(
        json.dumps(
            {
                "task_id": case_id,
                "baseline_replay_dir": str(prior_baseline),
            }
        ),
        encoding="utf-8",
    )
    (members_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld.self_evolve.member_replay.v1",
                "members": [
                    {
                        "case_id": case_id,
                        "path": member_name,
                        "succeeded": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert _member_baseline_replay_dir(str(members_root), case_id) == str(
        prior_baseline
    )


@pytest.mark.asyncio
async def test_multi_member_replay_paths_do_not_collide_after_sanitization(
    tmp_path: Path,
) -> None:
    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.task_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task/a", input="Task A"),
            EvalCase(case_id="task?a", input="Task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task/a", "task?a"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-collision",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
    )
    await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )
    replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-collision"
        / "replay"
        / "cand-1"
    )

    loaded = load_candidate_replay_result(replay_dir)

    assert [member.case_id for member in loaded.member_results] == ["task/a", "task?a"]
    assert [
        member.candidate.trajectory[0]["action"]["content"]
        for member in loaded.member_results
    ] == ["task/a", "task?a"]


@pytest.mark.asyncio
async def test_replay_excludes_framework_advisory_members_from_paired_dataset(
    tmp_path: Path,
) -> None:
    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.task_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="user-task", input="Replay user task"),
            EvalCase(
                case_id="prior-run-summary",
                input={"status": "rejected"},
                source={"kind": "prior_self_evolve_run", "framework_generated": True},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={
                "train": ["user-task", "prior-run-summary"],
                "validation": [],
                "held_out": [],
            },
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-advisory",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )
    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=result,
        candidate=candidate,
    )

    assert [(call.task_id, call.variant_id) for call in calls] == [
        ("user-task", "baseline"),
        ("user-task", "cand-1"),
    ]
    assert [member.case_id for member in result.member_results] == ["user-task"]
    assert [case.case_id for case in paired.cases] == ["user-task"]


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_aggregates_evidence_metrics(
    tmp_path: Path,
) -> None:
    async def fake_executor(request):
        compacted = request.variant_id == "cand-1-2"
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={
                "evidence_compacted": compacted,
                "evidence_strategy_passed": not compacted,
                "evidence_compaction_signals": (
                    ["tool_output_compacted"] if compacted else []
                ),
            },
        )

    request = CandidateReplayRequest(
        run_id="run-evidence-metrics",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=1,
        candidate_repetitions=3,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert result.candidate.succeeded is True
    assert result.candidate.metrics["evidence_compacted"] is False
    assert result.candidate.metrics["evidence_strategy_passed"] is True
    assert result.candidate.metrics["evidence_retry_count"] == 1.0
    assert result.candidate.metrics["evidence_compaction_signals"] == [
        "tool_output_compacted"
    ]


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_fails_when_evidence_retries_still_compact(
    tmp_path: Path,
) -> None:
    async def fake_executor(request):
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={
                "evidence_compacted": True,
                "evidence_strategy_passed": False,
                "evidence_compaction_signals": ["tool_output_compacted"],
            },
        )

    request = CandidateReplayRequest(
        run_id="run-evidence-hard-fail",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=1,
        candidate_repetitions=1,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert result.succeeded is False
    assert result.candidate.status == "failed"
    assert result.candidate.failure["reason"] == "evidence_quality_failed"
    assert result.candidate.metrics["evidence_retry_count"] == 1
    assert result.candidate.metrics["evidence_compaction_signals"] == [
        "tool_output_compacted"
    ]


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_runs_baseline_and_candidate_with_skill_roots(
    tmp_path: Path,
) -> None:
    calls = []

    async def fake_executor(request):
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": f"{request.variant_id} output"},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={"score": 0.9 if request.variant_id == "cand-1" else 0.4},
            stdout=f"{request.variant_id} stdout",
            stderr="",
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input={"content": "Replay this task"}),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-1",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="demo",
            path=str(tmp_path / "skills" / "demo" / "SKILL.md"),
        ),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        baseline_skill_root=str(tmp_path / "skills"),
        task_input={"content": "Replay this task"},
        agent="Aworld",
        timeout_seconds=42,
        max_steps=5,
        max_tokens=100,
    )

    backend = AWorldCliCandidateReplayBackend(executor=fake_executor)

    result = await backend.replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=dataset,
    )

    assert result.succeeded is True
    assert [call.variant_id for call in calls] == ["baseline", "cand-1"]
    assert calls[0].skill_root == str(tmp_path / "skills")
    assert calls[1].skill_root == str(tmp_path / "overlay-skills")
    assert calls[0].task_text == "Replay this task"
    assert calls[1].agent == "Aworld"
    assert calls[1].timeout_seconds == 42
    assert result.baseline.trajectory[0]["action"]["content"] == "baseline output"
    assert result.candidate.trajectory[0]["action"]["content"] == "cand-1 output"

    replay_dir = tmp_path / ".aworld" / "self_evolve" / "run-1" / "replay" / "cand-1"
    assert (replay_dir / "request.json").exists()
    assert (replay_dir / "baseline" / "stdout.txt").read_text(encoding="utf-8") == "baseline stdout"
    assert (replay_dir / "cand-1" / "metrics.json").exists()


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_leaves_baseline_loader_default(
    tmp_path: Path,
) -> None:
    calls = []

    async def fake_executor(request):
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input={"content": "Replay this task"}),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-1",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="draft-skill",
            path=str(
                tmp_path
                / ".aworld"
                / "self_evolve"
                / "drafts"
                / "skills"
                / "draft-skill"
                / "SKILL.md"
            ),
        ),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        baseline_skill_root=None,
        task_input={"content": "Replay this task"},
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: draft-skill\n---\n# Draft\n", candidate_id="cand-1"),
        dataset=dataset,
    )

    assert result.succeeded is True
    assert [call.variant_id for call in calls] == ["baseline", "cand-1"]
    assert calls[0].skill_root is None
    assert calls[1].skill_root == str(tmp_path / "overlay-skills")


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_logs_replay_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = []
    monkeypatch.setattr(
        "aworld.self_evolve.replay.logger.info",
        messages.append,
    )

    async def fake_executor(request):
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input="Replay this task"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-logs",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
    )

    await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=dataset,
    )

    assert any("self_evolve.replay.start" in message for message in messages)
    assert any(
        "self_evolve.replay.repetition.start" in message and "variant_id=baseline" in message
        for message in messages
    )
    assert any(
        "self_evolve.replay.repetition.end" in message and "variant_id=cand-1" in message
        for message in messages
    )
    assert any("self_evolve.replay.end" in message for message in messages)


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_requests_machine_readable_trajectory_and_disables_auto_drain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NO_PROXY", "internal.example")
    monkeypatch.setenv("no_proxy", "legacy.example")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1080")
    captured: dict[str, object] = {}
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {"input": {"content": "Replay this task"}},
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        runtime_root = Path(kwargs["env"]["HOME"]).parent
        captured["runtime_root"] = runtime_root
        captured["runtime_paths_existed"] = all(
            path.is_dir()
            for path in (
                runtime_root / "home",
                runtime_root / "xdg-config",
                runtime_root / "xdg-cache",
                runtime_root / "xdg-data",
                runtime_root / "xdg-state",
                runtime_root / "tmp",
                runtime_root / "memory",
            )
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="human output\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            agent="Aworld",
        )
    )

    assert result.succeeded is True
    assert result.trajectory == trajectory
    assert "--emit-trajectory" in captured["command"]
    task_index = captured["command"].index("--task") + 1
    task_text = captured["command"][task_index]
    assert task_text.startswith("Replay this task")
    assert "Self-evolve replay evidence requirements" in task_text
    assert "artifact-first" in task_text
    assert "bounded structured summaries" in task_text
    assert "compacted" in task_text
    assert "Self-evolve replay runtime contract" in task_text
    assert "Task-plane operations required by the original task are allowed" in task_text
    assert "explicitly authorizes a control-plane operation" in task_text
    assert "Do not terminate, restart, reconfigure, or replace externally managed prerequisites" in task_text
    assert "Do not copy or substitute credentials, sessions, profiles, or private state" in task_text
    assert "Do not override the supplied HOME, TMPDIR, XDG_*" in task_text
    assert "Only endpoints supplied through AWORLD_REPLAY_ENDPOINT_*" in task_text
    assert "Do not enumerate or connect to any other loopback port" in task_text
    assert (
        "On the first terminal protocol signal from a supplied endpoint" in task_text
    )
    assert "Do not retry alternate URL forms or inspect host ports" in task_text
    assert "fail the replay with a prerequisite-unavailable reason" in task_text
    assert (
        "Once the requested output artifact and a valid evidence manifest exist, "
        "stop evidence collection and return the final answer"
    ) in task_text
    assert (
        "For bounded replay validation, prefer the smallest representative evidence path"
    ) in task_text
    assert (
        "After the first successful structured extraction, immediately persist replay "
        "artifacts"
    ) in task_text
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["env"]["AWORLD_SELF_EVOLVE_AUTO_DRAIN"] == "0"
    assert captured["kwargs"]["env"]["AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"] == str(
        tmp_path / "artifacts"
    )
    assert captured["kwargs"]["env"]["AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST"] == str(
        tmp_path / "artifacts" / "evidence_manifest.jsonl"
    )
    assert captured["kwargs"]["env"]["AWORLD_LOG_PATH"] == str(
        tmp_path / "artifacts" / "logs"
    )
    assert captured["kwargs"]["env"]["AWORLD_TRAJECTORY_LOG_DISABLED"] == "1"
    assert captured["kwargs"]["env"]["AWORLD_TOOL_CALL_LIMIT"] == "24"
    assert captured["kwargs"]["env"][
        "AWORLD_PROMPT_BUDGET_RESERVED_OUTPUT_TOKENS"
    ] == "4096"
    assert captured["kwargs"]["env"][
        "AWORLD_MCP_STDIO_INHERIT_ENV_PREFIXES"
    ] == "AWORLD_REPLAY_"
    assert captured["kwargs"]["start_new_session"] is True
    runtime_root = captured["runtime_root"]
    assert isinstance(runtime_root, Path)
    assert runtime_root.parent.resolve() == Path("/tmp").resolve()
    assert runtime_root.name.startswith("aworld-replay-runtime-")
    assert len(str(runtime_root)) < 100
    assert captured["kwargs"]["env"]["HOME"] == str(runtime_root / "home")
    assert captured["kwargs"]["env"]["XDG_CONFIG_HOME"] == str(
        runtime_root / "xdg-config"
    )
    assert captured["kwargs"]["env"]["XDG_CACHE_HOME"] == str(
        runtime_root / "xdg-cache"
    )
    assert captured["kwargs"]["env"]["XDG_DATA_HOME"] == str(
        runtime_root / "xdg-data"
    )
    assert captured["kwargs"]["env"]["XDG_STATE_HOME"] == str(
        runtime_root / "xdg-state"
    )
    assert captured["kwargs"]["env"]["TMPDIR"] == str(runtime_root / "tmp")
    assert captured["kwargs"]["env"]["AWORLD_MEMORY_ROOT"] == str(
        runtime_root / "memory"
    )
    assert captured["kwargs"]["env"]["NO_PROXY"] == (
        "internal.example,127.0.0.1,localhost,::1"
    )
    assert captured["kwargs"]["env"]["no_proxy"] == (
        "legacy.example,127.0.0.1,localhost,::1"
    )
    assert captured["kwargs"]["env"]["ALL_PROXY"] == (
        "socks5://127.0.0.1:1080"
    )
    assert captured["runtime_paths_existed"] is True
    assert not runtime_root.exists()
    assert "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR" in task_text
    assert str(tmp_path / "artifacts") in task_text
    assert str(tmp_path / "artifacts" / "evidence_manifest.jsonl") in task_text
    assert "evidence_manifest.jsonl" in task_text


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_rejects_undeclared_loopback_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld"},
            "state": {"input": {"content": "Replay this task"}},
            "action": {
                "content": "Try the host service as a fallback.",
                "tool_calls": [
                    {
                        "function": {
                            "name": "mcp",
                            "arguments": json.dumps(
                                {
                                    "command": (
                                        "curl http://127.0.0.1:9222/json/version"
                                    )
                                }
                            ),
                        }
                    }
                ],
            },
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:54321"
            },
        )
    )

    assert result.status == "failed"
    assert result.failure == {
        "type": "ReplayBoundaryViolation",
        "reason": "replay_dependency_boundary_violation",
        "outcome": "task_failure",
        "undeclared_loopback_endpoints": ["http://127.0.0.1:9222"],
    }
    assert result.metrics["replay_dependency_boundary_passed"] is False
    assert result.metrics["undeclared_loopback_endpoint_count"] == 1


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_normalizes_stale_workspace_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    workspace_root = tmp_path / "aworld"
    workspace_root.mkdir()
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {"input": {"content": "Replay this task"}},
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(workspace_root),
            task_input={"content": "Replay this task"},
            task_text=(
                "Use /Users/manwu/Documents/workspace/aworld/examples/skill_agent/"
                "skills/x-scraper and write /Users/manwu/Documents/workspace/"
                "aworld/x_ai_daily_extra.json"
            ),
            skill_root=str(workspace_root / "skills"),
            artifact_dir=str(workspace_root / "artifacts"),
        )
    )

    task_index = captured["command"].index("--task") + 1
    task_text = captured["command"][task_index]
    assert "/Users/manwu/Documents/workspace/aworld" not in task_text
    assert str(workspace_root / "examples" / "skill_agent" / "skills" / "x-scraper") in task_text
    assert str(workspace_root / "x_ai_daily_extra.json") in task_text


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_accepts_compacted_markers_with_valid_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir(parents=True)
        evidence_path = artifact_dir / "episode_extract.txt"
        evidence_path.write_text("bounded non-compacted evidence excerpt", encoding="utf-8")
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "episode_raw",
                    "artifact_path": "episode_extract.txt",
                    "extraction_method": "raw_download",
                    "size_bytes": evidence_path.stat().st_size,
                }
            )
            + "\n"
            + json.dumps(
                {
                    "source_id": "episode",
                    "artifact_path": "episode_extract.txt",
                    "extraction_method": "bounded_extract",
                    "bounded_excerpts": {
                        "summary": "bounded non-compacted evidence excerpt",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    assert result.succeeded is True
    assert result.failure is None
    assert result.metrics["evidence_compacted"] is True
    assert result.metrics["evidence_strategy_passed"] is True
    assert result.metrics["evidence_manifest_present"] is True
    assert result.metrics["evidence_manifest_entry_count"] == 2
    assert "evidence_manifest_invalid_entry_count" not in result.metrics
    bundle = json.loads((tmp_path / "artifacts" / "evidence_bundle.json").read_text())
    assert bundle["valid"] is True
    assert bundle["entries"][0]["bounded_evidence"]["source"] == "artifact_preview"
    assert (
        bundle["entries"][0]["bounded_evidence"]["bounded_excerpt"]
        == "bounded non-compacted evidence excerpt"
    )
    assert bundle["entries"][0]["bounded_evidence"]["truncated"] is False


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_writes_canonical_evidence_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir(parents=True)
        evidence_path = artifact_dir / "bounded_extract.txt"
        evidence_path.write_text("bounded non-compacted evidence excerpt", encoding="utf-8")
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "source-1",
                    "evidence_type": "file",
                    "artifact_path": "bounded_extract.txt",
                    "extraction_method": "bounded_extract",
                    "bounded_excerpt": "bounded non-compacted evidence excerpt",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    bundle_path = tmp_path / "artifacts" / "evidence_bundle.json"
    evidence_path = tmp_path / "artifacts" / "bounded_extract.txt"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    assert result.succeeded is True
    assert result.metrics["evidence_compacted"] is True
    assert result.metrics["evidence_strategy_passed"] is True
    assert result.metrics["evidence_bundle_present"] is True
    assert result.metrics["evidence_bundle_valid"] is True
    assert result.metrics["evidence_bundle_entry_count"] == 1
    assert result.metrics["evidence_bundle_path"] == str(bundle_path)
    assert bundle["format"] == "aworld.self_evolve.evidence_bundle"
    assert bundle["entries"][0]["source_id"] == "source-1"
    assert bundle["entries"][0]["artifact_path"] == str(evidence_path)
    assert bundle["entries"][0]["bounded_evidence"]["bounded_excerpt"] == (
        "bounded non-compacted evidence excerpt"
    )


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_accepts_non_file_evidence_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Notification scheduled.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "scheduled_notification",
                    "evidence_type": "metadata",
                    "extraction_method": "scheduler_response",
                    "metadata": {
                        "operation": "schedule_notification",
                        "reference_id": "job-123",
                        "status": "scheduled",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    assert result.succeeded is True
    assert result.metrics["evidence_bundle_valid"] is True
    bundle = json.loads((tmp_path / "artifacts" / "evidence_bundle.json").read_text())
    assert bundle["entries"] == [
        {
            "bounded_evidence": {},
            "evidence_type": "metadata",
            "extraction_method": "scheduler_response",
            "metadata": {
                "operation": "schedule_notification",
                "reference_id": "job-123",
                "status": "scheduled",
            },
            "source_id": "scheduled_notification",
        }
    ]


def test_replay_evidence_manifest_rejects_oversized_metadata(tmp_path: Path) -> None:
    reason = _invalid_evidence_manifest_entry_reason(
        {
            "source_id": "operation_result",
            "evidence_type": "metadata",
            "extraction_method": "structured_result",
            "metadata": {"value": "x" * 20_000},
        },
        artifact_dir=tmp_path,
    )

    assert reason == "metadata exceeds bounded evidence limit"


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_reports_compacted_argument_without_evidence_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": (
                            "replay_compacted_argument_unavailable: tool call argument "
                            "contains compacted_string_field"
                        ),
                    }
                ]
            },
            "action": {"content": "Replay stopped.", "is_agent_finished": "True"},
            "reward": {"status": "failed"},
        }
    ]

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    assert result.succeeded is False
    assert result.failure == {
        "reason": "replay_compacted_argument_unavailable",
        "detail": "replay stopped before executing compacted tool arguments",
    }
    assert result.metrics["replay_compacted_argument_blocked"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_archives_workspace_manifest_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        artifact_dir = workspace_root / "artifacts"
        artifact_dir.mkdir(parents=True)
        output_path = workspace_root / "x_ai_daily_extra.json"
        output_path.write_text(
            json.dumps({"meta": {"count": 1}, "tweets": [{"text": "AI news"}]}),
            encoding="utf-8",
        )
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "workspace_output",
                    "artifact_path": str(output_path),
                    "extraction_method": "task_output_json",
                    "fields_used": ["content"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path / "workspace"),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "workspace" / "skills"),
            artifact_dir=str(tmp_path / "workspace" / "artifacts"),
        )
    )

    assert result.succeeded is True
    assert result.failure is None
    assert result.metrics["evidence_manifest_entry_count"] == 1
    assert result.metrics["evidence_manifest_archived_entry_count"] == 1
    assert "evidence_manifest_invalid_entry_count" not in result.metrics

    bundle = json.loads(
        (tmp_path / "workspace" / "artifacts" / "evidence_bundle.json").read_text(
            encoding="utf-8"
        )
    )
    archived_path = Path(bundle["entries"][0]["artifact_path"])
    assert archived_path.is_relative_to(tmp_path / "workspace" / "artifacts")
    assert archived_path.exists()
    assert bundle["entries"][0]["bounded_evidence"]["source"] == "artifact_preview"


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_rejects_untrusted_manifest_artifact_outside_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        artifact_dir = workspace_root / "artifacts"
        artifact_dir.mkdir(parents=True)
        outside_path = tmp_path / "outside.txt"
        outside_path.write_text("secret should not be allowlisted", encoding="utf-8")
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "outside",
                    "artifact_path": str(outside_path),
                    "extraction_method": "outside_file",
                    "fields_used": ["content"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path / "workspace"),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "workspace" / "skills"),
            artifact_dir=str(tmp_path / "workspace" / "artifacts"),
        )
    )

    assert result.succeeded is False
    assert result.failure["reason"] == "evidence_quality_failed"
    assert result.metrics["evidence_manifest_entry_count"] == 0
    assert result.metrics["evidence_manifest_invalid_entry_count"] == 1
    assert result.metrics["evidence_manifest_invalid_reasons"] == [
        "line 1: artifact_path is outside trusted replay/workspace directories"
    ]


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_accepts_bounded_excerpt_for_outside_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        artifact_dir = workspace_root / "artifacts"
        artifact_dir.mkdir(parents=True)
        outside_path = tmp_path / "scrape_stderr.log"
        outside_path.write_text("large outside log should not be read", encoding="utf-8")
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "scrape_stderr_log",
                    "artifact_path": str(outside_path),
                    "extraction_method": "stderr capture",
                    "fields": ["scroll_rounds", "final_total", "ai_count"],
                    "bounded_excerpt": (
                        "search flow: 10 scrolls, 121 raw -> 20 deduped; "
                        "RESULT: total=20, ai_count=16"
                    ),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path / "workspace"),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "workspace" / "skills"),
            artifact_dir=str(tmp_path / "workspace" / "artifacts"),
        )
    )

    assert result.succeeded is True
    assert result.failure is None
    assert result.metrics["evidence_manifest_entry_count"] == 1
    assert "evidence_manifest_invalid_entry_count" not in result.metrics
    bundle = json.loads(
        (tmp_path / "workspace" / "artifacts" / "evidence_bundle.json").read_text(
            encoding="utf-8"
        )
    )
    assert bundle["valid"] is True
    assert bundle["entries"][0]["bounded_evidence"]["bounded_excerpt"].startswith(
        "search flow"
    )
    assert bundle["entries"][0]["bounded_evidence"]["fields"] == [
        "scroll_rounds",
        "final_total",
        "ai_count",
    ]


def test_replay_aggregate_metrics_include_bundle_validity() -> None:
    from aworld.self_evolve.replay import _aggregate_variant_results

    artifact_dir = Path("/tmp/self-evolve-replay-aggregate")
    results = [
        ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=[{"action": {"content": "answer 1"}}],
            metrics={
                "evidence_bundle_valid": True,
                "evidence_bundle_entry_count": 2,
                "evidence_bundle_path": "/tmp/bundle-1.json",
            },
        ),
        ReplayVariantResult(
            variant_id="cand-2",
            status="succeeded",
            trajectory=[{"action": {"content": "answer 2"}}],
            metrics={
                "evidence_bundle_valid": True,
                "evidence_bundle_entry_count": 4,
                "evidence_bundle_path": "/tmp/bundle-2.json",
            },
        ),
    ]

    aggregate = _aggregate_variant_results(
        base_variant_id="candidate",
        results=results,
        artifact_dir=artifact_dir,
    )

    assert aggregate.metrics["evidence_bundle_valid"] is True
    assert aggregate.metrics["evidence_bundle_valid_values"] == [True, True]
    assert aggregate.metrics["evidence_bundle_entry_count"] == 3.0
    assert aggregate.metrics["evidence_bundle_entry_count_values"] == [2.0, 4.0]
    assert aggregate.metrics["evidence_bundle_path"] == "/tmp/bundle-2.json"


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_rejects_compacted_evidence_without_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    assert result.succeeded is False
    assert result.failure["reason"] == "evidence_quality_failed"
    assert result.metrics["evidence_compacted"] is True
    assert result.metrics["evidence_strategy_passed"] is False
    assert result.metrics["evidence_compaction_signals"] == [
        "tool_output_compacted"
    ]


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_rejects_summary_synthetic_trajectory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": [
                        {
                            "state": {"input": {"content": "Replay this task"}},
                            "action": {"content": "summary only", "tool_calls": []},
                            "reward": {"status": "ok"},
                        }
                    ],
                    "trajectory_capture_mode": "summary_synthetic",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            agent="Aworld",
        )
    )

    assert result.succeeded is False
    assert result.failure == {
        "reason": "trajectory_capture_mode_unsupported",
        "detail": "self-evolve replay requires TaskResponse.trajectory evidence",
        "trajectory_capture_mode": "summary_synthetic",
    }


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_decodes_timeout_output_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        (tmp_path / "scrape_output.log").write_text(
            "WebSocket protocol error: HTTP version must be 1.1 or higher; "
            "API_KEY=top-secret-value",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                b"partial stdout: CDP discovery failed at "
                b"/Users/example/private/runtime.py"
            ),
            stderr=b"partial stderr API_KEY=top-secret-value",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.succeeded is False
    assert result.stdout.startswith("partial stdout: CDP discovery failed")
    assert result.stderr.startswith("partial stderr")
    assert result.failure == {
        "type": "TimeoutExpired",
        "reason": "replay timed out",
        "outcome": "candidate_failure",
        "failure_class": "candidate_replay_capability",
        "failure_stage": "task_rollout",
        "repairable": True,
        "diagnostics": {
            "stdout_tail": "partial stdout: CDP discovery failed at <LOCAL_PATH>",
            "stderr_tail": "partial stderr <REDACTED_SECRET>",
            "task_artifacts": [
                {
                    "path": "workspace/scrape_output.log",
                    "tail": (
                        "WebSocket protocol error: HTTP version must be 1.1 "
                        "or higher; <REDACTED_SECRET>"
                    ),
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_ignores_static_task_contract_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                "Current task: use http://127.0.0.1:49533\n"
                "On the first terminal protocol signal, such as a protocol error, "
                "report a replay capability mismatch.\n"
                "🔄 Running task: task_20260716110210\n"
            ).encode(),
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.succeeded is False
    assert result.failure["type"] == "TimeoutExpired"
    assert "failure_class" not in result.failure
    assert "repairable" not in result.failure


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_closed_cdp_response_channel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        (tmp_path / "scrape_stdout.log").write_text(
            "CDP response channel closed",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=b"The browser task remained active without producing output",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_browser_operation_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        (tmp_path / "scrape_stdout.log").write_text(
            "Operation timed out. The page may still be loading or the element may not exist.",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=b"The browser replay task did not produce an artifact",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_replay_endpoint_navigation_stall(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                b"The script hung during navigation against "
                b"http://127.0.0.1:49533 while waiting for the page to load"
            ),
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_incomplete_navigation_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                b"The script is still navigating on http://127.0.0.1:49533; "
                b"it exited without producing output"
            ),
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_localized_navigation_stall(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                "Replay endpoint: http://127.0.0.1:49533\n"
                "[03:12:23] 正在导航到 X 首页..."
            ).encode(),
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_unresponsive_bound_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                b"The task is stuck while connecting to "
                b"http://127.0.0.1:49533 and the service is unresponsive"
            ),
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_wrong_bound_endpoint_protocol(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                b"The endpoint at http://127.0.0.1:49533 appears to be a "
                b"fixture service, not a required browser protocol endpoint"
            ),
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_bound_endpoint_schema_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                b"The supplied endpoint at port 49533 failed to deserialize "
                b"the protocol response: missing field sessionId"
            ),
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_trusts_scoped_task_protocol_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "scrape_output.log").write_text(
            "Failed to deserialize protocol response: missing field sessionId",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=b"The replay task did not finish",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_recovers_timeout_with_valid_artifact_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir(parents=True)
        evidence_path = artifact_dir / "x_ai_daily_extra.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "meta": {"count": 1, "ai_related_count": 1},
                    "tweets": [
                        {
                            "author_name": "A",
                            "author_handle": "@a",
                            "time": "now",
                            "text": "OpenAI agent update",
                            "link": "https://x.com/a/status/1",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "final_output",
                    "artifact_path": "x_ai_daily_extra.json",
                    "extraction_method": "bounded_replay_extract",
                    "fields": ["meta.count", "meta.ai_related_count", "tweets[].link"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=b"partial stdout",
            stderr=b"partial stderr",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
        )
    )

    assert result.succeeded is True
    assert result.failure is None
    assert result.stdout == "partial stdout"
    assert result.stderr == "partial stderr"
    assert result.metrics["timeout_recovered_with_artifact_evidence"] is True
    assert result.metrics["evidence_bundle_valid"] is True
    assert result.trajectory == [
        {
            "state": {"input": {"content": "Replay this task"}},
            "action": {
                "content": "Replay completed from artifact-backed evidence manifest.",
                "is_agent_finished": "True",
            },
            "reward": {"status": "ok"},
            "meta": {
                "trajectory_capture_mode": "artifact_manifest",
                "evidence_manifest_path": str(tmp_path / "artifacts" / "evidence_manifest.jsonl"),
                "evidence_bundle_path": str(tmp_path / "artifacts" / "evidence_bundle.json"),
            },
        }
    ]


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_does_not_recover_dependency_mismatch_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "diag_replay_capability_mismatch.json").write_text(
            json.dumps(
                {
                    "diagnostic_type": "replay_capability_mismatch",
                    "endpoint": "http://127.0.0.1:49533",
                    "error": "WebSocket protocol error: missing upgrade header",
                }
            ),
            encoding="utf-8",
        )
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "replay_mismatch",
                    "extraction_method": "diagnostic",
                    "evidence_type": "metadata",
                    "metadata": {
                        "diagnostic_type": "replay_capability_mismatch",
                        "endpoint": "http://127.0.0.1:49533",
                        "error": "WebSocket protocol error",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.succeeded is False
    assert result.metrics.get("timeout_recovered_with_artifact_evidence") is None
    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True
    assert result.failure["diagnostics"]["task_artifacts"][0]["path"] == (
        "artifact/diag_replay_capability_mismatch.json"
    )


def test_replay_cli_supervisor_stops_on_terminal_dependency_diagnostic(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    diagnostic_path = artifact_dir / "diag_replay_capability_mismatch.json"
    diagnostic = json.dumps(
        {
            "diagnostic_type": "replay_capability_mismatch",
            "endpoint": "http://127.0.0.1:49533",
            "error": "protocol mismatch",
        }
    )
    script = (
        "from pathlib import Path; import time; "
        f"Path({str(diagnostic_path)!r}).write_text("
        f"{diagnostic!r}); "
        "time.sleep(30)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=20,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            execution_started_at=time.time(),
            replay_environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )

    assert time.monotonic() - started < 5
    assert getattr(exc_info.value, "terminal_diagnostic", False) is True


def test_replay_cli_supervisor_does_not_stop_on_live_progress_artifact(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    progress_path = artifact_dir / "scrape_output.log"
    script = (
        "from pathlib import Path; import time; "
        "print('Replay runtime contract for http://127.0.0.1:49533: on a "
        "protocol error report replay capability mismatch', flush=True); "
        f"Path({str(progress_path)!r}).write_text("
        "'[03:12:23] 正在导航到 X 首页...'); "
        "time.sleep(1.2); print('completed', flush=True)"
    )
    started = time.monotonic()

    completed = _run_replay_cli(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        text=True,
        capture_output=True,
        timeout=5,
        start_new_session=True,
        env={},
        artifact_dir=artifact_dir,
        execution_started_at=time.time(),
        replay_environment={
            "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
        },
    )

    assert completed.returncode == 0
    assert completed.stdout.rstrip().endswith("completed")
    assert time.monotonic() - started >= 1


def test_replay_cli_supervisor_stops_on_skill_owned_capability_mismatch(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    diagnostic_path = artifact_dir / "replay_capability_mismatch.json"
    diagnostic = json.dumps(
        {
            "diagnostic_type": "replay_capability_mismatch",
            "endpoint": "http://127.0.0.1:49533",
            "observed_errors": ["All protocol discovery methods failed"],
        }
    )
    script = (
        "from pathlib import Path; import time; "
        f"Path({str(diagnostic_path)!r}).write_text("
        f"{diagnostic!r}); "
        "time.sleep(30)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=20,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            execution_started_at=time.time(),
            replay_environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )

    assert time.monotonic() - started < 5
    assert getattr(exc_info.value, "terminal_diagnostic", False) is True


def test_replay_cli_supervisor_stops_on_partial_process_diagnostic(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    script = (
        "import time; "
        "print('The task is still navigating on http://127.0.0.1:49533 and "
        "exited without producing output', flush=True); "
        "time.sleep(30)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=20,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            execution_started_at=time.time(),
            replay_environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )

    assert time.monotonic() - started < 5
    assert getattr(exc_info.value, "terminal_diagnostic", False) is True


def test_replay_cli_supervisor_stops_on_observed_protocol_signal(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    script = (
        "import time; "
        "print('Replay endpoint http://127.0.0.1:49533 returned not_found for '"
        "      'the required protocol path. This is a protocol signal.', "
        "      flush=True); "
        "time.sleep(30)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=20,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            execution_started_at=time.time(),
            replay_environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )

    assert time.monotonic() - started < 5
    assert getattr(exc_info.value, "terminal_diagnostic", False) is True


def test_replay_cli_supervisor_stops_on_workspace_root_diagnostic(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    workspace_dir = artifact_dir / "workspace"
    workspace_dir.mkdir(parents=True)
    diagnostic_path = workspace_dir / "scrape_stdout.log"
    diagnostic = (
        "All protocol discovery methods failed for "
        "http://127.0.0.1:49533"
    )
    script = (
        "from pathlib import Path; import time; "
        f"Path({str(diagnostic_path)!r}).write_text({diagnostic!r}); "
        "time.sleep(30)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=20,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            execution_started_at=time.time(),
            replay_environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )

    assert time.monotonic() - started < 5
    assert getattr(exc_info.value, "terminal_diagnostic", False) is True


def test_replay_cli_supervisor_combines_live_artifact_with_endpoint_context(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    diagnostic_path = artifact_dir / "scrape_stdout.log"
    script = (
        "from pathlib import Path; import time; "
        "print('Using replay endpoint http://127.0.0.1:49533', flush=True); "
        f"Path({str(diagnostic_path)!r}).write_text("
        "'Failed to deserialize response: missing field targetInfos'); "
        "time.sleep(30)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=20,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            execution_started_at=time.time(),
            replay_environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )

    assert time.monotonic() - started < 5
    assert getattr(exc_info.value, "terminal_diagnostic", False) is True


def test_replay_cli_supervisor_ignores_static_contract_language(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    script = (
        "import time; "
        "print('If a supplied endpoint does not implement the protocol, report a '"
        "      'replay capability mismatch.', flush=True); "
        "time.sleep(5)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=1.5,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            execution_started_at=time.time(),
            replay_environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )

    assert time.monotonic() - started >= 1.0
    assert getattr(exc_info.value, "terminal_diagnostic", False) is False


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_returns_structured_failure(
    tmp_path: Path,
) -> None:
    async def failing_executor(request):
        if request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="succeeded",
                trajectory=[{"action": {"content": "baseline"}}],
            )
        return ReplayExecutionResult(
            status="failed",
            trajectory=[],
            failure={"reason": "missing model configuration"},
            stdout="",
            stderr="No model configuration",
        )

    request = CandidateReplayRequest(
        run_id="run-failure",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
    )

    result = await AWorldCliCandidateReplayBackend(executor=failing_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert result.succeeded is False
    assert result.candidate.status == "failed"
    assert result.candidate.failure == {"reason": "missing model configuration"}
    failure_path = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-failure"
        / "replay"
        / "cand-1"
        / "cand-1"
        / "failure.json"
    )
    assert failure_path.exists()
