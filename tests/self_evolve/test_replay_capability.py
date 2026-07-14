from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aworld.self_evolve.replay_adaptation import ReplayCapabilityRequirement
from aworld.self_evolve.replay_capability import (
    REPLAY_CAPABILITY_RESULT_SCHEMA_VERSION,
    ReplayCapabilityCompileRequest,
    ReplayCapabilityError,
    build_replay_sandboxed_command,
    compile_and_freeze_capability,
    discover_replay_capability,
    verify_frozen_replay_capability,
)


def _request(skill_root: Path) -> ReplayCapabilityCompileRequest:
    context_path = skill_root.parent / "trajectory_context" / "case-1.json"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_payload = {
        "case_id": "case-1",
        "steps": [
            {"observation": "recorded fixture"},
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
        status="runtime_required",
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
        "print('runtime')\n",
        encoding="utf-8",
    )
    if entrypoint == "replay/compiler.py":
        fixture_expression = (
            "('recorded fixture a' if 'compile-a' in args.output "
            "else 'recorded fixture b')"
            if nondeterministic
            else "'recorded fixture'"
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

    command = build_replay_sandboxed_command(
        ["python", "fixture_service.py"],
        read_roots=(tmp_path,),
        writable_roots=(tmp_path,),
        allow_loopback=True,
    )

    assert "network-bind" in command[2]
    assert "network-inbound" in command[2]
    assert "network-outbound" not in command[2]
