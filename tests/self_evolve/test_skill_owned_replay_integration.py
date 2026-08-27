from __future__ import annotations

import json
import socket
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from aworld.self_evolve.datasets import (
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.optimizers.base import OptimizerResult
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    ReplayExecutionRequest,
    ReplayExecutionResult,
    load_candidate_replay_result,
)
from aworld.self_evolve.runner import SelfEvolveRunner
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SkillTextTarget
from aworld.self_evolve.trace_pack import build_trace_pack
from aworld.self_evolve.types import CandidateFileDelta, CandidateVariant


@pytest.mark.asyncio
@pytest.mark.replay_sandbox
async def test_candidate_owned_replay_capability_runs_end_to_end(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original = "---\nname: demo\n---\n# Demo\n\nOriginal guidance.\n"
    skill_path.write_text(original, encoding="utf-8")
    target = SkillTextTarget(skill_path)
    trajectory = [
        {
            "meta": {"task_id": "task-1", "step": 1},
            "state": {
                "input": {"content": "Inspect http://127.0.0.1:9222"}
            },
            "action": {"content": "recorded response"},
            "reward": {"status": "failed"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="task-1",
    )
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="task-1",
    )
    manifest = {
        "schema_version": "aworld.skill.replay_capability.v1",
        "capability_id": "recorded-fixture",
        "protocol": "aworld.replay.subprocess.v1",
        "entrypoint": "replay/compiler.py",
        "handles": ["local_endpoint"],
        "runtime_files": ["replay/runtime.py"],
    }
    compiler = r"""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--request', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
request = json.loads(Path(args.request).read_text(encoding='utf-8'))
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
(output / 'recording.txt').write_text('recorded response', encoding='utf-8')
requirement = request['requirements'][0]
result = {
    'schema_version': 'aworld.replay.capability_result.v1',
    'capability_id': 'recorded-fixture',
    'deterministic': True,
    'handled_requirements': [requirement['requirement_id']],
    'unhandled_requirements': [],
    'evidence_refs': {
        requirement['requirement_id']: requirement['evidence_refs'],
    },
    'fixture_evidence_refs': {
        'recording.txt': requirement['evidence_refs'],
    },
    'fixtures': ['recording.txt'],
    'endpoint_replacements': {requirement['identifier']: 'fixture-http'},
    'services': [{
        'service_id': 'fixture-http',
        'requirement_id': requirement['requirement_id'],
        'transport': 'skill_runtime',
        'response_fixture': 'recording.txt',
        'runtime_entrypoint': 'replay.runtime:main',
        'readiness': {'kind': 'tcp', 'timeout_seconds': 3},
        'protocol_probes': [
            {
                'kind': 'http',
                'path': '/replay/data',
                'timeout_seconds': 3,
                'response_contains': 'recorded response',
            },
        ],
    }],
}
(output / 'result.json').write_text(json.dumps(result, sort_keys=True), encoding='utf-8')
"""
    runtime = r"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--port', required=True, type=int)
parser.add_argument('--fixture', required=True)
parser.add_argument('--scratch', required=True)
args = parser.parse_args()
trace_path = Path(args.scratch) / 'protocol_trace.jsonl'
sequence = 0

def trace(direction, kind, fields, correlation):
    global sequence
    sequence += 1
    with trace_path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps({
            'direction': direction,
            'sequence': sequence,
            'kind': kind,
            'fields': fields,
            'correlation': correlation,
        }, sort_keys=True) + '\n')

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        trace('in', 'http_request', ['method', 'path'], {
            'method': 'GET', 'path': self.path,
        })
        if self.path != '/replay/data':
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(open(args.fixture, 'rb').read())
        trace('out', 'http_response', ['status', 'path'], {
            'status': 200, 'path': self.path,
        })
    def log_message(self, *args):
        pass

HTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
"""
    candidate = CandidateVariant(
        candidate_id="candidate-replay-capability",
        target=target.identity,
        content="---\nname: demo\n---\n# Demo\n\nUse recorded replay evidence.\n",
        rationale="make the local dependency replayable",
        target_fingerprint=target.fingerprint_current_content(),
        files=(
            CandidateFileDelta(
                path="replay/capability.json",
                content=json.dumps(manifest),
            ),
            CandidateFileDelta(path="replay/compiler.py", content=compiler),
            CandidateFileDelta(path="replay/runtime.py", content=runtime),
        ),
    )

    class FixedOptimizer:
        async def propose(self, request):
            return OptimizerResult(candidates=(candidate,))

    observed_ports: list[int] = []
    observed_skill_contents: list[str] = []

    async def executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        assert request.skill_root is not None
        skill_files = [
            path
            for path in Path(request.skill_root).rglob("SKILL.md")
            if path.parent.name == "demo"
        ]
        assert len(skill_files) == 1
        active_skill_content = skill_files[0].read_text(encoding="utf-8")
        observed_skill_contents.append(active_skill_content)
        corrected_behavior_loaded = (
            "Use recorded replay evidence." in active_skill_content
        )
        url = request.task_input["content"].split()[-1]
        parsed_url = urlsplit(url)
        assert parsed_url.path == "/replay/data"
        assert parsed_url.port is not None
        port = parsed_url.port
        observed_ports.append(port)
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            connection.sendall(
                b"GET /replay/data HTTP/1.0\r\nHost: localhost\r\n\r\n"
            )
            response = b""
            while b"recorded response" not in response:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                response += chunk
            assert b"recorded response" in response
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {
                        "content": (
                            "corrected behavior"
                            if corrected_behavior_loaded
                            else "original behavior"
                        )
                    },
                }
            ],
            metrics={
                "task_success": 1.0 if corrected_behavior_loaded else 0.0,
                "loaded_skill_behavior_marker": corrected_behavior_loaded,
            },
        )

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=FixedOptimizer(),
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=AWorldCliCandidateReplayBackend(executor=executor),
    )

    result = await runner.run_explicit_target(
        run_id="run-skill-owned-replay",
        target=target,
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="proposal",
    )

    assert result.run.status.value == "succeeded"
    assert skill_path.read_text(encoding="utf-8") == original
    assert not (skill_path.parent / "replay").exists()
    assert len(observed_ports) == 2 and len(set(observed_ports)) == 2
    assert len(observed_skill_contents) == 2
    assert "Original guidance." in observed_skill_contents[0]
    assert "Use recorded replay evidence." in observed_skill_contents[1]
    report = json.loads(
        (store.run_path("run-skill-owned-replay") / "report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["replay_capability"]["source"] == "candidate"
    assert report["replay_capability"]["ready"] is True
    baseline_metrics = report["replay"]["baseline"]["metrics"]
    candidate_metrics = report["replay"]["candidate"]["metrics"]
    assert baseline_metrics["task_success"] == 0.0
    assert candidate_metrics["task_success"] == 1.0
    assert baseline_metrics["frozen_capability_fingerprint"] == (
        candidate_metrics["frozen_capability_fingerprint"]
    )
    assert baseline_metrics["service_endpoint"] != candidate_metrics["service_endpoint"]
    loaded = load_candidate_replay_result(
        store.run_path("run-skill-owned-replay")
        / "replay"
        / candidate.candidate_id
    )
    assert loaded.request.replay_adaptation is not None
    assert loaded.request.replay_adaptation.replay_capability is not None
    loaded_service = loaded.request.replay_adaptation.replay_capability.services[0]
    assert loaded_service.runtime_entrypoint == "replay/runtime.py"
    assert loaded_service.protocol_probes[0].kind == "http"
    assert loaded_service.task_entry_path == "/replay/data"
    assert loaded_service.protocol_probes[0].response_contains == "recorded response"
    assert (
        loaded.request.replay_adaptation.replay_capability.fingerprint
        == report["replay_capability"]["frozen_capability_fingerprint"]
    )
