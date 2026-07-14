from __future__ import annotations

import json
from pathlib import Path

from aworld.self_evolve.datasets import (
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayAdapterBinding,
    ReplayAdaptationCompiler,
    ReplayDependency,
)


def _dataset(task: str, *, task_id: str = "task-1"):
    trajectory = [
        {
            "meta": {"task_id": task_id, "step": 1},
            "state": {"input": {"content": task}},
            "action": {"content": "historical result", "tool_calls": []},
            "reward": {"status": "success"},
        }
    ]
    return build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id=task_id,
    )


def test_compiler_normalizes_workspace_paths_and_persists_bundle(tmp_path: Path) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    (workspace / "input.txt").write_text("fixture", encoding="utf-8")
    dataset = _dataset(
        "Read /Users/old/Documents/workspace/demo/input.txt and summarize it."
    )

    bundle = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    case = bundle.case("task-1")
    assert (
        "${AWORLD_REPLAY_WORKSPACE}/input.txt"
        in case.adapted_task_input["content"]
    )
    assert Path(bundle.workspace_seed).is_dir()
    assert (Path(bundle.workspace_seed) / "input.txt").read_text(encoding="utf-8") == "fixture"
    assert Path(bundle.manifest_path).is_file()
    assert bundle.workspace_seed_fingerprint.startswith("sha256:")
    assert bundle.adaptation_fingerprint.startswith("sha256:")
    assert bundle.ready is True
    persisted = json.loads(
        (tmp_path / "run" / "adaptation" / "bundle.json").read_text(encoding="utf-8")
    )
    assert persisted["adaptation_fingerprint"] == bundle.adaptation_fingerprint


def test_compiler_marks_continuation_without_prior_context_incomplete(tmp_path: Path) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset("Continue the current task with this additional operator steering: retry."),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    case = bundle.case("task-1")
    assert case.readiness == "context_incomplete"
    assert any(item.status == "context_incomplete" for item in case.dependencies)
    assert bundle.ready is False


def test_compiler_marks_unbound_local_endpoint_runtime_required(tmp_path: Path) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset("Connect to http://[::1]:9222 and inspect the page."),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    dependency = next(
        item for item in bundle.case("task-1").dependencies if item.kind == "local_endpoint"
    )
    assert dependency.status == "runtime_required"
    assert dependency.deterministic is False
    assert bundle.ready is False


def test_compiler_snapshots_explicit_bounded_external_file(tmp_path: Path) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    external = tmp_path / "inputs" / "article.txt"
    external.parent.mkdir()
    external.write_text("recorded input", encoding="utf-8")

    bundle = ReplayAdaptationCompiler(max_external_file_bytes=1024).compile(
        dataset=_dataset(f"Summarize {external}"),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    case = bundle.case("task-1")
    dependency = next(item for item in case.dependencies if item.kind == "local_file")
    assert dependency.status == "snapshotted"
    adapted_path = case.adapted_task_input["content"].split("Summarize ", 1)[1]
    assert adapted_path.startswith("${AWORLD_REPLAY_WORKSPACE}/.aworld_replay_fixtures/")
    relative = adapted_path.removeprefix("${AWORLD_REPLAY_WORKSPACE}/")
    assert (Path(bundle.workspace_seed) / relative).read_text(encoding="utf-8") == "recorded input"
    assert bundle.ready is True


def test_compiler_does_not_snapshot_secret_like_external_file(tmp_path: Path) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    secret = tmp_path / "inputs" / ".env"
    secret.parent.mkdir()
    secret.write_text("TOKEN=secret", encoding="utf-8")

    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset(f"Read {secret}"),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    dependency = next(
        item for item in bundle.case("task-1").dependencies if item.kind == "local_file"
    )
    assert dependency.status == "unresolved"
    assert bundle.ready is False
    assert "secret" not in json.dumps(json.loads((tmp_path / "run" / "adaptation" / "bundle.json").read_text()), ensure_ascii=False)


def test_registered_adapter_can_make_local_endpoint_deterministic(tmp_path: Path) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    class LocalEndpointFixtureAdapter:
        adapter_id = "test.local-endpoint-fixture.v1"

        def bind(self, dependency, *, context):
            if dependency.kind != "local_endpoint":
                return None
            fixture = context.artifact_root / "fixtures" / "cdp.json"
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_text('{"pages": []}', encoding="utf-8")
            return ReplayAdapterBinding(
                adapter_id=self.adapter_id,
                dependency_id=dependency.identifier,
                deterministic=True,
                environment={"AWORLD_REPLAY_CDP_FIXTURE": str(fixture)},
                fixture_paths=(str(fixture),),
            )

    bundle = ReplayAdaptationCompiler(
        adapters=(LocalEndpointFixtureAdapter(),)
    ).compile(
        dataset=_dataset("Connect to http://127.0.0.1:9222 and inspect the page."),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    case = bundle.case("task-1")
    dependency: ReplayDependency = next(
        item for item in case.dependencies if item.kind == "local_endpoint"
    )
    assert dependency.status == "adapter_bound"
    assert dependency.adapter_id == "test.local-endpoint-fixture.v1"
    assert case.bindings[0].environment["AWORLD_REPLAY_CDP_FIXTURE"].endswith("cdp.json")
    assert bundle.ready is True
