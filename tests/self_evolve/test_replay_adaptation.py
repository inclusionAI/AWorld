from __future__ import annotations

import json
import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.replay_adaptation import (
    IsolationBindingCoverage,
    IsolationDecision,
    IsolationExclusiveFallback,
    IsolationGrant,
    IsolationGrantSet,
    IsolationResourceIdentity,
    IsolationServiceIdentity,
    ReplayAdapterBinding,
    ReplayAdaptationCompiler,
    ReplayAdaptationError,
    ReplayDependency,
    ReplayCapabilityRequirement,
    ReplayIsolationTopology,
    compile_isolation_decision_artifact,
    compile_isolation_grant,
    compile_replay_adaptation_isolation_decision,
    isolation_grants_compatible,
    materialize_replay_workspace,
    validate_replay_binding_concurrency,
)
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    CandidateReplayResult,
    ReplayExecutionRequest,
    ReplayExecutionResult,
    ReplayVariantResult,
    build_replay_request,
    candidate_replay_is_comparable,
)
from aworld.self_evolve.controllers.screening_execution import (
    find_reusable_baseline_replay_dir as _find_reusable_baseline_replay_dir,
)
from aworld.self_evolve.runner import SelfEvolveRunner
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SkillTextTarget
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    SelfEvolveTargetRef,
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
    assert Path(bundle.environment_snapshot_path).is_file()
    assert bundle.workspace_seed_fingerprint.startswith("sha256:")
    assert bundle.environment_fingerprint.startswith("sha256:")
    assert bundle.adaptation_fingerprint.startswith("sha256:")
    assert bundle.ready is True
    persisted = json.loads(
        (tmp_path / "run" / "adaptation" / "bundle.json").read_text(encoding="utf-8")
    )
    assert persisted["adaptation_fingerprint"] == bundle.adaptation_fingerprint
    environment = json.loads(Path(bundle.environment_snapshot_path).read_text())
    assert environment["runtime"]["python_version"]
    assert not any("token" in key.lower() for key in environment["environment"])


def test_environment_identity_excludes_staged_workload_tool_names(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def dataset(tool_name: str, task_id: str) -> SelfEvolveDataset:
        return build_dataset_from_source(
            SelfEvolveEvalSourceConfig(kind="current_trajectory"),
            current_trajectory=(
                {
                    "meta": {"task_id": task_id, "step": 1},
                    "state": {"input": {"content": "Inspect the fixture."}},
                    "action": {
                        "content": "historical result",
                        "tool_calls": [
                            {"function": {"name": tool_name}}
                        ],
                    },
                    "reward": {"status": "success"},
                },
            ),
            task_id=task_id,
        )

    screening = ReplayAdaptationCompiler().compile(
        dataset=dataset("mcp", "screening-case"),
        workspace_root=workspace,
        artifact_root=tmp_path / "screening-adaptation",
    )
    expanded = ReplayAdaptationCompiler().compile(
        dataset=dataset("CAST_SEARCH", "expanded-case"),
        workspace_root=workspace,
        artifact_root=tmp_path / "expanded-adaptation",
    )

    screening_snapshot = json.loads(
        Path(screening.environment_snapshot_path).read_text(encoding="utf-8")
    )
    expanded_snapshot = json.loads(
        Path(expanded.environment_snapshot_path).read_text(encoding="utf-8")
    )
    assert screening_snapshot["tool_names"] == ["mcp"]
    assert expanded_snapshot["tool_names"] == ["CAST_SEARCH"]
    assert screening.environment_fingerprint == expanded.environment_fingerprint
    assert screening.adaptation_fingerprint != expanded.adaptation_fingerprint


def test_compiler_seeds_git_workspace_from_tracked_files_only(tmp_path: Path) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()
    tracked = workspace / "tracked.txt"
    tracked.write_text("source", encoding="utf-8")
    generated = workspace / "generated-result.json"
    generated.write_text('{"stale": true}', encoding="utf-8")
    subprocess.run(
        ["git", "init", "-q", str(workspace)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "add", "tracked.txt"],
        check=True,
        capture_output=True,
        text=True,
    )

    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset(f"Write the result to {generated}."),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    seed = Path(bundle.workspace_seed)
    assert (seed / "tracked.txt").read_text(encoding="utf-8") == "source"
    assert not (seed / "generated-result.json").exists()
    assert not (seed / ".git").exists()


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


def test_compiler_marks_natural_follow_up_without_prior_context_incomplete(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset("把论文里的这些细节补全"),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    case = bundle.case("task-1")
    assert case.readiness == "context_incomplete"
    assert any(item.kind == "conversation_context" for item in case.dependencies)
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


def test_preflight_returns_unresolved_requirements_without_compiling_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    report = ReplayAdaptationCompiler().preflight(
        dataset=_dataset("Connect to http://[::1]:9222 and inspect the page."),
        workspace_root=workspace,
    )

    assert len(report.requirements) == 1
    requirement = report.requirements[0]
    assert isinstance(requirement, ReplayCapabilityRequirement)
    assert requirement.kind == "local_endpoint"
    assert requirement.identifier == "http://[::1]:9222"
    assert requirement.case_ids == ("task-1",)
    assert requirement.evidence_refs[0].startswith("context:task-1:sha256:")
    assert report.fingerprint.startswith("sha256:")


def test_preflight_does_not_require_context_when_snapshot_reconstructed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()
    trajectory_log = tmp_path / "trajectory.log"
    first = [
        {
            "meta": {"step": 1, "session_id": "session-a"},
            "state": {"input": {"content": "Start the task."}},
            "action": {"content": "Recorded result.", "tool_calls": []},
            "reward": {"status": "ok"},
        }
    ]
    continuation = [
        {
            "meta": {"step": 1, "session_id": "session-a"},
            "state": {"input": {"content": "Continue the current task."}},
            "action": {"content": "Done.", "tool_calls": []},
            "reward": {"status": "ok"},
        }
    ]
    trajectory_log.write_text(
        repr({"task_id": "first", "trajectory": json.dumps(first)})
        + "\n"
        + repr({"task_id": "next", "trajectory": json.dumps(continuation)})
        + "\n",
        encoding="utf-8",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="trajectory_log",
            path=str(trajectory_log),
        )
    )

    report = ReplayAdaptationCompiler().preflight(
        dataset=dataset,
        workspace_root=workspace,
    )

    assert not any(item.kind == "conversation_context" for item in report.requirements)


def test_preflight_keeps_inherited_incomplete_context_non_replayable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()
    trajectory_log = tmp_path / "trajectory.log"
    missing_root = [
        {
            "meta": {"step": 1, "session_id": "session-a"},
            "state": {"input": {"content": "Continue with the previous analysis."}},
            "action": {"content": "Partial result.", "tool_calls": []},
            "reward": {"status": "ok"},
        }
    ]
    dependent = [
        {
            "meta": {"step": 1, "session_id": "session-a"},
            "state": {"input": {"content": "继续补全这些细节"}},
            "action": {"content": "Still partial.", "tool_calls": []},
            "reward": {"status": "ok"},
        }
    ]
    trajectory_log.write_text(
        repr({"task_id": "missing-root", "trajectory": json.dumps(missing_root)})
        + "\n"
        + repr({"task_id": "dependent", "trajectory": json.dumps(dependent)})
        + "\n",
        encoding="utf-8",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(
            kind="trajectory_log",
            path=str(trajectory_log),
        )
    )

    report = ReplayAdaptationCompiler().preflight(
        dataset=dataset,
        workspace_root=workspace,
    )

    requirement = next(
        item for item in report.requirements if item.kind == "conversation_context"
    )
    assert requirement.case_ids == ("missing-root", "dependent")
    assert dataset.cases[1].context_snapshot is not None
    assert dataset.cases[1].context_snapshot.context_reason == (
        "inherited_incomplete_context"
    )


def test_dependency_analysis_ignores_paths_from_reconstructed_prior_turns(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()
    trajectory_log = tmp_path / "trajectory.log"
    first = [
        {
            "meta": {"step": 1, "session_id": "session-a"},
            "state": {"input": {"content": "Prepare the source material."}},
            "action": {
                "content": "Historical artifact: /old-machine/private/result.json",
                "tool_calls": [],
            },
            "reward": {"status": "ok"},
        }
    ]
    continuation = [
        {
            "meta": {"step": 1, "session_id": "session-a"},
            "state": {"input": {"content": "Continue the current task."}},
            "action": {"content": "Done.", "tool_calls": []},
            "reward": {"status": "ok"},
        }
    ]
    trajectory_log.write_text(
        repr({"task_id": "first", "trajectory": json.dumps(first)})
        + "\n"
        + repr({"task_id": "next", "trajectory": json.dumps(continuation)})
        + "\n",
        encoding="utf-8",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="trajectory_log", path=str(trajectory_log))
    )
    next_case = next(case for case in dataset.cases if case.case_id == "next")
    assert "/old-machine/private/result.json" in next_case.input["content"]

    compiler = ReplayAdaptationCompiler()
    report = compiler.preflight(dataset=dataset, workspace_root=workspace)
    bundle = compiler.compile(
        dataset=dataset,
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    assert not any(
        item.kind == "local_file" and "next" in item.case_ids
        for item in report.requirements
    )
    assert not any(
        item.kind == "local_file" for item in bundle.case("next").dependencies
    )


def test_preflight_and_compile_report_same_missing_local_file(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()
    dataset = _dataset("Read /aworld-replay-source-only/missing.csv")
    compiler = ReplayAdaptationCompiler()

    report = compiler.preflight(dataset=dataset, workspace_root=workspace)
    bundle = compiler.compile(
        dataset=dataset,
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    requirement = next(item for item in report.requirements if item.kind == "local_file")
    dependency = next(
        item for item in bundle.case("task-1").dependencies if item.kind == "local_file"
    )
    assert requirement.identifier == dependency.identifier
    assert requirement.status == dependency.status == "unresolved"


def test_dependency_analysis_strips_sentence_punctuation_from_urls(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    report = ReplayAdaptationCompiler().preflight(
        dataset=_dataset("Use the archived page (https://example.com/report)."),
        workspace_root=workspace,
    )

    requirement = next(item for item in report.requirements if item.kind == "http_resource")
    assert requirement.identifier == "https://example.com/report"


def test_dependency_analysis_stops_at_unicode_sentence_punctuation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    report = ReplayAdaptationCompiler().preflight(
        dataset=_dataset(
            "Use https://example.com/report，then compare it with prior evidence."
        ),
        workspace_root=workspace,
    )

    requirement = next(
        item for item in report.requirements if item.kind == "http_resource"
    )
    assert requirement.identifier == "https://example.com/report"


def test_dependency_analysis_preserves_balanced_url_parentheses(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()
    url = "https://example.com/wiki/Function_(mathematics)"

    report = ReplayAdaptationCompiler().preflight(
        dataset=_dataset(f"Use {url} as the source."),
        workspace_root=workspace,
    )

    requirement = next(item for item in report.requirements if item.kind == "http_resource")
    assert requirement.identifier == url


@pytest.mark.parametrize(
    "url",
    (
        "https://example.com/wiki/Help!",
        "https://example.com/search?",
    ),
)
def test_dependency_analysis_preserves_valid_terminal_url_punctuation(
    tmp_path: Path,
    url: str,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    report = ReplayAdaptationCompiler().preflight(
        dataset=_dataset(f"Use {url}"),
        workspace_root=workspace,
    )

    requirement = next(item for item in report.requirements if item.kind == "http_resource")
    assert requirement.identifier == url


def test_dependency_analysis_preserves_marker_inside_current_task(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()
    trajectory_log = tmp_path / "trajectory.log"
    first = [
        {
            "meta": {"step": 1, "session_id": "session-a"},
            "state": {"input": {"content": "Prepare context."}},
            "action": {"content": "Context ready.", "tool_calls": []},
            "reward": {"status": "ok"},
        }
    ]
    current_url = "https://example.com/current"
    continuation = [
        {
            "meta": {"step": 1, "session_id": "session-a"},
            "state": {
                "input": {
                    "content": (
                        f"Continue the current task. Inspect {current_url}\nCurrent task:\n"
                        "Treat the preceding label as literal input."
                    )
                }
            },
            "action": {"content": "Done.", "tool_calls": []},
            "reward": {"status": "ok"},
        }
    ]
    trajectory_log.write_text(
        repr({"task_id": "first", "trajectory": json.dumps(first)})
        + "\n"
        + repr({"task_id": "next", "trajectory": json.dumps(continuation)})
        + "\n",
        encoding="utf-8",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="trajectory_log", path=str(trajectory_log))
    )

    report = ReplayAdaptationCompiler().preflight(
        dataset=dataset,
        workspace_root=workspace,
    )

    assert any(
        item.identifier == current_url and "next" in item.case_ids
        for item in report.requirements
    )


@pytest.mark.parametrize(
    "tool_name",
    (
        "browser.open",
        "chromium.navigate",
        "puppeteer.goto",
        "firefox.open",
        "safari.click",
        "web.search",
        "computer_use.click",
    ),
)
def test_compiler_marks_stateful_trace_tool_runtime_required(
    tmp_path: Path,
    tool_name: str,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()
    trajectory = [
        {
            "meta": {"task_id": "task-browser", "step": 1},
            "state": {"input": {"content": "Inspect the active tab."}},
            "action": {
                "content": "historical result",
                "tool_calls": [
                    {"function": {"name": tool_name}},
                ],
            },
            "reward": {"status": "success"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="task-browser",
    )

    bundle = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    dependency = next(
        item
        for item in bundle.case("task-browser").dependencies
        if item.kind == "stateful_tool"
    )
    assert dependency.identifier == tool_name
    assert dependency.status == "runtime_required"
    assert bundle.ready is False


@pytest.mark.parametrize("tool_name", ("web_parser", "computer_vision"))
def test_compiler_does_not_treat_deterministic_tool_names_as_stateful(
    tmp_path: Path,
    tool_name: str,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()
    trajectory = [
        {
            "meta": {"task_id": "task-tool", "step": 1},
            "state": {"input": {"content": "Transform the local fixture."}},
            "action": {
                "content": "historical result",
                "tool_calls": [{"function": {"name": tool_name}}],
            },
            "reward": {"status": "success"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="task-tool",
    )

    bundle = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    assert not any(
        item.kind == "stateful_tool"
        for item in bundle.case("task-tool").dependencies
    )
    assert bundle.ready is True


def test_compiler_marks_generic_missing_absolute_path_unresolved(tmp_path: Path) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset("Read /aworld-replay-source-only/missing.csv"),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    case = bundle.case("task-1")
    dependency = next(item for item in case.dependencies if item.kind == "local_file")
    assert dependency.status == "unresolved"
    assert "${AWORLD_REPLAY_UNRESOLVED_PATH}" in case.adapted_task_input["content"]
    assert bundle.ready is False


def test_compiler_leaves_workspace_relative_paths_portable(tmp_path: Path) -> None:
    workspace = tmp_path / "demo"
    fixture = workspace / "fixtures" / "input.txt"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("seed", encoding="utf-8")

    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset("Read ./fixtures/input.txt"),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    case = bundle.case("task-1")
    assert case.adapted_task_input["content"] == "Read ./fixtures/input.txt"
    assert not any(item.kind == "local_file" for item in case.dependencies)
    assert bundle.ready is True


@pytest.mark.parametrize(
    "task",
    (
        "Implement GET /users/{id}",
        "Handle API route /users/me",
        "Use regex pattern /foo/bar/ for validation",
    ),
)
def test_compiler_does_not_treat_routes_or_regex_as_local_files(
    tmp_path: Path,
    task: str,
) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset(task),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    case = bundle.case("task-1")
    assert case.adapted_task_input["content"] == task
    assert not any(item.kind == "local_file" for item in case.dependencies)
    assert bundle.ready is True


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


def test_compiler_counts_external_fixtures_toward_workspace_limits(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    (workspace / "seed.txt").write_text("seed", encoding="utf-8")
    external = tmp_path / "inputs" / "article.txt"
    external.parent.mkdir()
    external.write_text("recorded input", encoding="utf-8")

    with pytest.raises(ReplayAdaptationError, match="file limit exceeded"):
        ReplayAdaptationCompiler(max_workspace_files=1).compile(
            dataset=_dataset(f"Summarize {external}"),
            workspace_root=workspace,
            artifact_root=tmp_path / "run" / "adaptation",
        )
    fixture_root = tmp_path / "run" / "adaptation" / "workspace_seed" / ".aworld_replay_fixtures"
    assert not fixture_root.exists() or not any(fixture_root.iterdir())


def test_compiler_rejects_external_fixture_before_exceeding_byte_limit(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    external = tmp_path / "inputs" / "large.txt"
    external.parent.mkdir()
    external.write_text("x" * 200, encoding="utf-8")

    with pytest.raises(ReplayAdaptationError, match="byte limit exceeded"):
        ReplayAdaptationCompiler(max_workspace_bytes=50).compile(
            dataset=_dataset(f"Summarize {external}"),
            workspace_root=workspace,
            artifact_root=tmp_path / "run" / "adaptation",
        )
    fixture_root = tmp_path / "run" / "adaptation" / "workspace_seed" / ".aworld_replay_fixtures"
    assert not fixture_root.exists() or not any(fixture_root.iterdir())


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
    fixture_ref = case.bindings[0].environment["AWORLD_REPLAY_CDP_FIXTURE"]
    assert fixture_ref.startswith(
        "${AWORLD_REPLAY_WORKSPACE}/.aworld_replay_adapter_fixtures/"
    )
    fixture_relative = fixture_ref.removeprefix("${AWORLD_REPLAY_WORKSPACE}/")
    assert (Path(bundle.workspace_seed) / fixture_relative).read_text() == '{"pages": []}'
    assert bundle.ready is True


def _test_fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _isolated_binding(
    adapter_id: str = "adapter-a",
    dependency_id: str = "dependency-a",
    **changes: object,
) -> ReplayAdapterBinding:
    values: dict[str, object] = {
        "adapter_id": adapter_id,
        "dependency_id": dependency_id,
        "deterministic": True,
        "concurrency_mode": "isolated",
    }
    values.update(changes)
    return validate_replay_binding_concurrency(
        ReplayAdapterBinding(**values)  # type: ignore[arg-type]
    )


def _isolation_topology(
    lane: str,
    *,
    bindings: tuple[ReplayAdapterBinding, ...] = (),
    workspace_identity: str | None = None,
    runtime_identity: str | None = None,
    resources: tuple[IsolationResourceIdentity, ...] | None = None,
) -> ReplayIsolationTopology:
    canonical_bindings = tuple(
        validate_replay_binding_concurrency(item) for item in bindings
    )
    browser_identity = f"browser:{lane}"
    default_resources = (
        IsolationResourceIdentity(
            resource_kind="browser",
            identity=browser_identity,
            access_mode="isolated",
            cleanup_owner=f"cleanup:{lane}",
        ),
        IsolationResourceIdentity(
            resource_kind="fixture-index",
            identity="fixture-index:stable",
            access_mode="shared_read_only",
        ),
    )
    coverage = tuple(
        IsolationBindingCoverage(
            binding_fingerprint=item.binding_fingerprint or "",
            adapter_id=item.adapter_id,
            dependency_id=item.dependency_id,
            resource_identities=(
                item.resource_key
                if item.concurrency_mode == "shared_read_only"
                else browser_identity,
            ),
        )
        for item in canonical_bindings
    )
    return ReplayIsolationTopology.create(
        materializer_id="test-materializer",
        materializer_fingerprint=_test_fingerprint("test-materializer:v1"),
        workspace_identity=workspace_identity or f"workspace:{lane}",
        runtime_identity=runtime_identity or f"runtime:{lane}",
        browser_profile_identity=f"browser-profile:{lane}",
        endpoint_namespace_identity=f"endpoint-namespace:{lane}",
        evidence_directory_identity=f"evidence:{lane}",
        services=(
            IsolationServiceIdentity(
                service_id="recorded-http",
                instance_identity=f"service:{lane}:recorded-http",
                cleanup_owner=f"cleanup:{lane}",
            ),
        ),
        resources=resources or default_resources,
        binding_coverage=coverage,
        cleanup_owner=f"cleanup:{lane}",
    )


def test_compile_isolation_grant_is_versioned_immutable_and_canonical() -> None:
    bindings = (
        _isolated_binding(
            adapter_id="adapter-b",
            dependency_id="dependency-b",
            concurrency_mode="shared_read_only",
            resource_key="fixture-index:stable",
        ),
        _isolated_binding(
            adapter_id="adapter-a",
            dependency_id="dependency-a",
        ),
    )

    compiled = compile_isolation_grant(
        topology=_isolation_topology("lane-a", bindings=bindings),
        bindings=bindings,
    )
    reordered = compile_isolation_grant(
        topology=_isolation_topology(
            "lane-a",
            bindings=tuple(reversed(bindings)),
            resources=tuple(
                reversed(_isolation_topology("lane-a").resources)
            ),
        ),
        bindings=tuple(reversed(bindings)),
    )

    assert compiled.grant is not None
    assert compiled.fallback is None
    assert compiled.grant.schema_version == "aworld.self_evolve.isolation_grant.v1"
    assert compiled.grant.fingerprint.startswith("sha256:")
    assert compiled.grant.fingerprint == reordered.grant.fingerprint
    assert compiled.grant.binding_fingerprints == tuple(
        sorted(item.binding_fingerprint for item in bindings if item.binding_fingerprint)
    )
    assert IsolationGrant.from_dict(compiled.grant.to_dict()) == compiled.grant
    with pytest.raises(Exception):
        compiled.grant.cleanup_owner = "changed"  # type: ignore[misc]


def test_compile_isolation_grant_falls_back_for_exclusive_binding() -> None:
    compiled = compile_isolation_grant(
        topology=_isolation_topology("lane-a"),
        bindings=(
            ReplayAdapterBinding(
                adapter_id="exclusive-adapter",
                dependency_id="endpoint",
                deterministic=True,
                concurrency_mode="exclusive",
                resource_key="browser:shared",
            ),
        ),
    )

    assert compiled.grant is None
    assert compiled.fallback is not None
    assert compiled.fallback.code == "binding_requires_exclusive"
    assert compiled.fallback.limiting_resource == "browser:shared"


def test_compile_isolation_grant_falls_back_when_binding_coverage_is_missing() -> None:
    binding = _isolated_binding()
    compiled = compile_isolation_grant(
        topology=_isolation_topology("lane-a"),
        bindings=(binding,),
    )

    assert compiled.grant is None
    assert compiled.fallback is not None
    assert compiled.fallback.code == "binding_coverage_missing"
    assert compiled.fallback.limiting_resource == binding.binding_fingerprint


def test_isolation_grants_compatible_accepts_distinct_lanes_and_shared_read_only() -> None:
    left = compile_isolation_grant(
        topology=_isolation_topology("lane-a"),
        bindings=(),
    ).grant
    right = compile_isolation_grant(
        topology=_isolation_topology("lane-b"),
        bindings=(),
    ).grant

    assert left is not None and right is not None
    decision = isolation_grants_compatible(left, right)

    assert decision.compatible is True
    assert decision.code == "compatible"


def test_isolation_grants_compatible_rejects_shared_mutable_resource() -> None:
    left = compile_isolation_grant(
        topology=_isolation_topology("lane-a"),
        bindings=(),
    ).grant
    right_topology = _isolation_topology(
        "lane-b",
        resources=(
            IsolationResourceIdentity(
                resource_kind="browser",
                identity="browser:lane-a",
                access_mode="isolated",
                cleanup_owner="cleanup:lane-b",
            ),
        ),
    )
    right = compile_isolation_grant(
        topology=right_topology,
        bindings=(),
    ).grant

    assert left is not None and right is not None
    decision = isolation_grants_compatible(left, right)

    assert decision.compatible is False
    assert decision.code == "resource_identity_conflict"
    assert decision.limiting_resource == "resource:browser:browser:lane-a"


def test_isolation_grant_deserialization_rejects_tampered_contract() -> None:
    grant = compile_isolation_grant(
        topology=_isolation_topology("lane-a"), bindings=()
    ).grant
    assert grant is not None

    tampered_resource = json.loads(json.dumps(grant.to_dict()))
    tampered_resource["resources"][0]["identity"] = "browser:forged"
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        IsolationGrant.from_dict(tampered_resource)

    tampered_schema = json.loads(json.dumps(grant.to_dict()))
    tampered_schema["schema_version"] = "aworld.self_evolve.isolation_grant.v0"
    with pytest.raises(ValueError, match="unsupported isolation grant schema"):
        IsolationGrant.from_dict(tampered_schema)

    invalid_resource = json.loads(json.dumps(grant.to_dict()))
    invalid_resource["resources"][0]["identity"] = ""
    with pytest.raises(ValueError, match="resource identity"):
        IsolationGrant.from_dict(invalid_resource)


def test_isolation_topology_requires_provenance_and_rejects_tampering() -> None:
    topology = _isolation_topology("lane-a")
    assert ReplayIsolationTopology.from_dict(topology.to_dict()) == topology

    missing_materializer = topology.to_dict()
    missing_materializer.pop("materializer_id")
    with pytest.raises(ValueError, match="materializer_id"):
        ReplayIsolationTopology.from_dict(missing_materializer)

    tampered = topology.to_dict()
    tampered["cleanup_owner"] = "cleanup:forged"
    with pytest.raises(ValueError, match="topology fingerprint mismatch"):
        ReplayIsolationTopology.from_dict(tampered)


def test_isolation_grants_reject_cross_dimension_and_nested_claims() -> None:
    left = compile_isolation_grant(
        topology=_isolation_topology(
            "lane-a", workspace_identity="/tmp/isolation/lane-a"
        ),
        bindings=(),
    ).grant
    equal_cross_dimension = compile_isolation_grant(
        topology=_isolation_topology(
            "lane-b", runtime_identity="/tmp/isolation/lane-a"
        ),
        bindings=(),
    ).grant
    nested_cross_dimension = compile_isolation_grant(
        topology=_isolation_topology(
            "lane-c", runtime_identity="/tmp/isolation/lane-a/runtime"
        ),
        bindings=(),
    ).grant

    assert left is not None
    assert equal_cross_dimension is not None
    assert nested_cross_dimension is not None
    assert not isolation_grants_compatible(left, equal_cross_dimension).compatible
    assert not isolation_grants_compatible(left, nested_cross_dimension).compatible


def test_only_same_typed_shared_read_only_claim_is_compatible() -> None:
    left = compile_isolation_grant(
        topology=_isolation_topology("lane-a"), bindings=()
    ).grant
    shared_as_service = ReplayIsolationTopology.create(
        materializer_id="test-materializer",
        materializer_fingerprint=_test_fingerprint("test-materializer:v1"),
        workspace_identity="workspace:lane-b",
        runtime_identity="runtime:lane-b",
        browser_profile_identity="browser-profile:lane-b",
        endpoint_namespace_identity="endpoint-namespace:lane-b",
        evidence_directory_identity="evidence:lane-b",
        services=(
            IsolationServiceIdentity(
                service_id="fixture-service",
                instance_identity="fixture-index:stable",
                access_mode="shared_read_only",
            ),
        ),
        cleanup_owner="cleanup:lane-b",
    )
    right = compile_isolation_grant(topology=shared_as_service, bindings=()).grant

    assert left is not None and right is not None
    assert not isolation_grants_compatible(left, right).compatible


def test_binding_fingerprint_uses_normalized_safety_projection() -> None:
    base = _isolated_binding(environment={"MODE": "safe", "ACCESS_TOKEN": "one"})
    secret_changed = _isolated_binding(
        environment={"MODE": "safe", "ACCESS_TOKEN": "two"}
    )
    environment_changed = _isolated_binding(environment={"MODE": "other"})
    shared_a = _isolated_binding(
        concurrency_mode="shared_read_only", resource_key="fixture:a"
    )
    shared_b = _isolated_binding(
        concurrency_mode="shared_read_only", resource_key="fixture:b"
    )

    assert base.binding_fingerprint == secret_changed.binding_fingerprint
    assert base.binding_fingerprint != environment_changed.binding_fingerprint
    assert shared_a.binding_fingerprint != shared_b.binding_fingerprint
    assert (
        validate_replay_binding_concurrency(base).binding_fingerprint
        == base.binding_fingerprint
    )
    caller_named = _isolated_binding(binding_fingerprint="legacy-caller-identity")
    assert caller_named.binding_fingerprint == _isolated_binding().binding_fingerprint
    assert caller_named.binding_fingerprint != "legacy-caller-identity"


def test_grant_set_and_decision_are_canonical_and_fail_closed() -> None:
    left = compile_isolation_grant(
        topology=_isolation_topology("lane-a"), bindings=()
    ).grant
    right = compile_isolation_grant(
        topology=_isolation_topology("lane-b"), bindings=()
    ).grant
    conflict = compile_isolation_grant(
        topology=_isolation_topology(
            "lane-c", workspace_identity="workspace:lane-a"
        ),
        bindings=(),
    ).grant
    assert left is not None and right is not None and conflict is not None

    grant_set = IsolationGrantSet.create((right, left))
    assert grant_set.fingerprint == IsolationGrantSet.create((left, right)).fingerprint
    assert IsolationGrantSet.from_dict(grant_set.to_dict()) == grant_set
    decision = IsolationDecision.create(
        requested_lane_count=2, grants=(right, left)
    )
    assert decision.safe_lane_count == 2
    assert decision.fallback is None
    assert IsolationDecision.from_dict(decision.to_dict()) == decision

    incomplete = IsolationDecision.create(requested_lane_count=2, grants=(left,))
    assert incomplete.safe_lane_count == 1
    assert incomplete.fallback is not None
    assert incomplete.fallback.code == "grant_set_incomplete"
    incompatible = IsolationDecision.create(
        requested_lane_count=2, grants=(left, conflict)
    )
    assert incompatible.safe_lane_count == 1
    assert incompatible.fallback is not None
    assert incompatible.fallback.code == "grant_set_incompatible"

    single = IsolationDecision.create(requested_lane_count=1, grants=(left,))
    assert single.safe_lane_count == 1
    assert single.fallback is None


def test_zero_grant_exclusive_decision_is_canonical_and_round_trips() -> None:
    fallback = IsolationExclusiveFallback(
        code="binding_requires_exclusive",
        limiting_resource="browser:shared",
        detail="browser profile cannot be isolated",
    )

    decision = IsolationDecision.exclusive_fallback(
        requested_lane_count=2,
        fallback=fallback,
    )

    assert decision.safe_lane_count == 1
    assert decision.grant_set.grants == ()
    assert decision.grant_set.pairwise_decisions == ()
    assert decision.grant_set.all_compatible is False
    assert decision.fallback == fallback
    assert IsolationDecision.from_dict(decision.to_dict()) == decision
    assert (
        IsolationGrantSet.from_dict(decision.grant_set.to_dict())
        == decision.grant_set
    )


def test_zero_grant_decision_rejects_missing_or_tampered_fallback() -> None:
    decision = IsolationDecision.exclusive_fallback(
        requested_lane_count=1,
        fallback=IsolationExclusiveFallback(
            code="binding_invalid",
            limiting_resource="binding",
            detail="binding identity is incomplete",
        ),
    )
    missing = json.loads(json.dumps(decision.to_dict()))
    missing["fallback"] = None
    with pytest.raises(ValueError, match="requires one fallback"):
        IsolationDecision.from_dict(missing)

    tampered = json.loads(json.dumps(decision.to_dict()))
    tampered["fallback"]["detail"] = "forged fallback"
    with pytest.raises(ValueError, match="decision fingerprint mismatch"):
        IsolationDecision.from_dict(tampered)


def test_decision_adapter_consumes_typed_compilations_not_raw_boolean() -> None:
    fallback_compilation = compile_isolation_grant(
        topology=_isolation_topology("lane-a"),
        bindings=(
            ReplayAdapterBinding(
                adapter_id="exclusive-adapter",
                dependency_id="endpoint",
                deterministic=True,
                concurrency_mode="exclusive",
                resource_key="browser:shared",
            ),
        ),
    )

    decision = compile_isolation_decision_artifact(
        requested_lane_count=2,
        lane_compilations=(fallback_compilation,),
    )

    assert decision.safe_lane_count == 1
    assert decision.grant_set.grants == ()
    assert decision.fallback == fallback_compilation.fallback
    assert decision.fingerprint.startswith("sha256:")


def test_replay_bundle_compiles_two_framework_owned_lanes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset("Summarize the local instructions."),
        workspace_root=workspace,
        artifact_root=tmp_path / "adaptation",
    )

    decision = compile_replay_adaptation_isolation_decision(
        bundle,
        materialization_root=tmp_path / "run" / "measurement-lanes",
        requested_lane_count=2,
    )

    assert decision.safe_lane_count == 2
    assert decision.fallback is None
    assert len(decision.grant_set.grants) == 2
    workspaces = {
        grant.workspace_identity for grant in decision.grant_set.grants
    }
    assert len(workspaces) == 2
    assert all(
        Path(identity).is_relative_to(tmp_path / "run" / "measurement-lanes")
        for identity in workspaces
    )


def test_replay_bundle_preserves_explicit_exclusive_binding(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset("Summarize the local instructions."),
        workspace_root=workspace,
        artifact_root=tmp_path / "adaptation",
    )
    exclusive = validate_replay_binding_concurrency(
        ReplayAdapterBinding(
            adapter_id="browser-replay",
            dependency_id="browser-service",
            deterministic=True,
            concurrency_mode="exclusive",
            resource_key="browser:shared",
        )
    )
    bundle = replace(
        bundle,
        cases=(replace(bundle.cases[0], bindings=(exclusive,)),),
    )

    decision = compile_replay_adaptation_isolation_decision(
        bundle,
        materialization_root=tmp_path / "run" / "measurement-lanes",
        requested_lane_count=2,
    )

    assert decision.safe_lane_count == 1
    assert decision.fallback is not None
    assert decision.fallback.code == "binding_requires_exclusive"
    assert decision.fallback.limiting_resource == "browser:shared"
    with pytest.raises(TypeError, match="typed grant compilations"):
        compile_isolation_decision_artifact(
            requested_lane_count=2,
            lane_compilations=(True,),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="supported bound"):
        compile_isolation_decision_artifact(
            requested_lane_count=65,
            lane_compilations=(),
        )


def test_stateful_tool_without_isolated_binding_remains_exclusive(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset("Open the browser and inspect the page."),
        workspace_root=workspace,
        artifact_root=tmp_path / "adaptation",
    )
    bundle = replace(
        bundle,
        cases=(replace(bundle.cases[0], tool_names=("agent-browser",)),),
    )

    decision = compile_replay_adaptation_isolation_decision(
        bundle,
        materialization_root=tmp_path / "run" / "measurement-lanes",
    )

    assert decision.safe_lane_count == 1
    assert decision.fallback is not None
    assert decision.fallback.limiting_resource == "stateful_tool:agent-browser"


def test_decision_adapter_requires_complete_pairwise_proof_for_multiple_lanes() -> None:
    left = compile_isolation_grant(
        topology=_isolation_topology("lane-a"), bindings=()
    )
    right = compile_isolation_grant(
        topology=_isolation_topology("lane-b"), bindings=()
    )

    complete = compile_isolation_decision_artifact(
        requested_lane_count=2,
        lane_compilations=(right, left),
    )
    incomplete = compile_isolation_decision_artifact(
        requested_lane_count=2,
        lane_compilations=(left,),
    )

    assert complete.safe_lane_count == 2
    assert complete.fallback is None
    assert len(complete.grant_set.pairwise_decisions) == 1
    assert complete.grant_set.pairwise_decisions[0].compatible is True
    assert incomplete.safe_lane_count == 1
    assert incomplete.fallback is not None
    assert incomplete.fallback.code == "grant_set_incomplete"


def test_compile_isolation_grant_reconciles_binding_provenance() -> None:
    binding = _isolated_binding()
    topology = _isolation_topology("lane-a", bindings=(binding,))
    bad_coverage = replace(topology.binding_coverage[0], adapter_id="adapter:wrong")
    forged_topology = ReplayIsolationTopology.create(
        materializer_id=topology.materializer_id,
        materializer_fingerprint=topology.materializer_fingerprint,
        workspace_identity=topology.workspace_identity,
        runtime_identity=topology.runtime_identity,
        browser_profile_identity=topology.browser_profile_identity,
        endpoint_namespace_identity=topology.endpoint_namespace_identity,
        evidence_directory_identity=topology.evidence_directory_identity,
        services=topology.services,
        resources=topology.resources,
        binding_coverage=(bad_coverage,),
        cleanup_owner=topology.cleanup_owner,
    )

    compiled = compile_isolation_grant(
        topology=forged_topology, bindings=(binding,)
    )
    assert compiled.grant is None
    assert compiled.fallback is not None
    assert compiled.fallback.code == "binding_coverage_invalid"


def test_materialize_replay_workspace_replaces_dirty_destination(tmp_path: Path) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    (workspace / "input.txt").write_text("seed", encoding="utf-8")
    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset(f"Read {workspace}/input.txt"),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )
    destination = tmp_path / "rollout"
    destination.mkdir()
    (destination / "dirty.txt").write_text("old mutation", encoding="utf-8")

    materialize_replay_workspace(bundle, destination)

    assert (destination / "input.txt").read_text(encoding="utf-8") == "seed"
    assert not (destination / "dirty.txt").exists()


def test_absolute_workspace_symlink_is_rebased_into_each_rollout(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    original = workspace / "input.txt"
    original.write_text("seed", encoding="utf-8")
    (workspace / "alias.txt").symlink_to(original)
    original_directory = workspace / "records"
    original_directory.mkdir()
    original_nested = original_directory / "nested.txt"
    original_nested.write_text("nested seed", encoding="utf-8")
    (workspace / "records-alias").symlink_to(
        original_directory,
        target_is_directory=True,
    )
    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset(f"Read {workspace}/alias.txt"),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    baseline = materialize_replay_workspace(bundle, tmp_path / "baseline")
    candidate = materialize_replay_workspace(bundle, tmp_path / "candidate")
    (baseline / "alias.txt").write_text("baseline mutation", encoding="utf-8")
    (baseline / "records-alias" / "nested.txt").write_text(
        "nested baseline mutation",
        encoding="utf-8",
    )

    assert original.read_text(encoding="utf-8") == "seed"
    assert original_nested.read_text(encoding="utf-8") == "nested seed"
    assert (candidate / "alias.txt").read_text(encoding="utf-8") == "seed"
    assert (
        candidate / "records-alias" / "nested.txt"
    ).read_text(encoding="utf-8") == "nested seed"
    assert (baseline / "input.txt").read_text(encoding="utf-8") == "baseline mutation"


def test_materialize_replay_workspace_unlinks_destination_symlink_only(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    (workspace / "input.txt").write_text("seed", encoding="utf-8")
    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset(f"Read {workspace}/input.txt"),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    destination = tmp_path / "rollout"
    destination.symlink_to(external, target_is_directory=True)

    materialize_replay_workspace(bundle, destination)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert destination.is_symlink() is False
    assert (destination / "input.txt").read_text(encoding="utf-8") == "seed"


def test_materialize_replay_workspace_rejects_symlinked_parent(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    (workspace / "input.txt").write_text("seed", encoding="utf-8")
    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset(f"Read {workspace}/input.txt"),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )
    external = tmp_path / "external"
    rollout = external / "rollout"
    rollout.mkdir(parents=True)
    sentinel = rollout / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    alias_parent = tmp_path / "alias-parent"
    alias_parent.symlink_to(external, target_is_directory=True)

    with pytest.raises(ReplayAdaptationError, match="symlinked parent"):
        materialize_replay_workspace(bundle, alias_parent / "rollout")

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_materialize_replay_workspace_rejects_destination_inside_seed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset("Replay task"),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    with pytest.raises(ReplayAdaptationError, match="cannot overlap"):
        materialize_replay_workspace(
            bundle,
            Path(bundle.workspace_seed) / "nested-rollout",
        )


@pytest.mark.asyncio
async def test_each_variant_and_repetition_starts_from_same_clean_seed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    (workspace / "input.txt").write_text("seed", encoding="utf-8")
    dataset = _dataset(f"Read {workspace}/input.txt")
    bundle = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo")
    candidate = CandidateVariant(
        candidate_id="cand-1",
        target=target,
        content="---\nname: demo\n---\n# Demo\n",
        rationale="test",
        target_fingerprint="sha256:baseline",
    )
    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request)
        isolated_workspace = Path(request.workspace_root)
        assert isolated_workspace != workspace
        assert (isolated_workspace / "input.txt").read_text(encoding="utf-8") == "seed"
        assert not (isolated_workspace / "mutation.txt").exists()
        assert str(isolated_workspace / "input.txt") in request.task_text
        assert request.workspace_seed_fingerprint == bundle.workspace_seed_fingerprint
        assert request.adaptation_fingerprint == bundle.adaptation_fingerprint
        assert request.adapter_determinism == "deterministic"
        assert request.isolated_workspace_path == request.workspace_root
        (isolated_workspace / "mutation.txt").write_text(
            request.variant_id,
            encoding="utf-8",
        )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    request = build_replay_request(
        run_id="run-isolated",
        workspace_root=workspace,
        target=target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        replay_adaptation=bundle,
        baseline_repetitions=2,
        candidate_repetitions=2,
    )

    assert request.dataset_fingerprint.startswith("sha256:")
    assert request.baseline_skill_fingerprint == "sha256:baseline"
    assert request.adaptation_fingerprint == bundle.adaptation_fingerprint
    assert request.workspace_seed_fingerprint == bundle.workspace_seed_fingerprint
    assert request.task_input_fingerprint == bundle.case("task-1").task_input_fingerprint

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )

    assert len(calls) == 4
    assert len({call.workspace_root for call in calls}) == 4
    assert {
        Path(call.workspace_root).parents[1].name for call in calls
    } == {"baseline", "cand-1"}
    assert candidate_replay_is_comparable(dataset=dataset, replay_result=result) is True
    assert result.baseline.metrics["adapter_determinism"] == "deterministic"
    assert len(result.baseline.metrics["isolated_workspace_path_values"]) == 2

    legacy_request = replace(
        result.request,
        task_input=dataset.cases[0].input,
        replay_adaptation=None,
        adaptation_fingerprint=None,
        workspace_seed_fingerprint=None,
        task_input_fingerprint=None,
    )
    assert result.member_results is not None
    legacy_result = replace(
        result,
        request=legacy_request,
        member_results=tuple(
            replace(
                member,
                request=replace(
                    member.request,
                    task_input=dataset.cases[0].input,
                    replay_adaptation=None,
                    adaptation_fingerprint=None,
                    workspace_seed_fingerprint=None,
                    task_input_fingerprint=None,
                ),
            )
            for member in result.member_results
        ),
    )
    assert candidate_replay_is_comparable(
        dataset=dataset,
        replay_result=legacy_result,
    ) is True
    assert candidate_replay_is_comparable(
        dataset=dataset,
        replay_result=legacy_result,
        require_adapted=True,
    ) is False

    missing_workspace_provenance = {
        key: value
        for key, value in result.baseline.metrics.items()
        if not key.startswith("isolated_workspace_path")
    }
    assert result.member_results is not None
    member = result.member_results[0]
    assert candidate_replay_is_comparable(
        dataset=dataset,
        replay_result=replace(
            result,
            member_results=(
                replace(
                    member,
                    baseline=replace(
                        member.baseline,
                        metrics=missing_workspace_provenance,
                    ),
                ),
            ),
        ),
    ) is False

    missing_provenance_baseline = replace(member.baseline, metrics={})
    assert candidate_replay_is_comparable(
        dataset=dataset,
        replay_result=replace(
            result,
            member_results=(replace(member, baseline=missing_provenance_baseline),),
        ),
    ) is False

    mismatched_candidate = replace(
        member.candidate,
        metrics={
            **dict(member.candidate.metrics),
            "workspace_seed_fingerprint": "sha256:different-seed",
        },
    )
    assert candidate_replay_is_comparable(
        dataset=dataset,
        replay_result=replace(
            result,
            member_results=(replace(member, candidate=mismatched_candidate),),
        ),
    ) is False

    lookup = {
        "store": FilesystemSelfEvolveStore(workspace),
        "run_id": "next-run",
        "target": target,
        "dataset": dataset,
        "baseline_repetitions": 2,
        "baseline_skill_fingerprint": request.baseline_skill_fingerprint,
        "dataset_fingerprint": request.dataset_fingerprint,
        "adaptation_fingerprint": request.adaptation_fingerprint,
        "workspace_seed_fingerprint": request.workspace_seed_fingerprint,
        "support_fingerprint": request.support_fingerprint,
        "timeout_envelope_fingerprint": (
            request.timeout_envelope_fingerprint
        ),
    }
    assert _find_reusable_baseline_replay_dir(**lookup) is not None
    assert _find_reusable_baseline_replay_dir(
        **{**lookup, "baseline_repetitions": 1}
    ) is None
    assert _find_reusable_baseline_replay_dir(
        **{**lookup, "baseline_skill_fingerprint": "sha256:changed-skill"}
    ) is None
    assert _find_reusable_baseline_replay_dir(
        **{**lookup, "dataset_fingerprint": "sha256:changed-dataset"}
    ) is None
    # A run-local compilation/artifact identity may change between candidate
    # packages while the executable control surface remains identical.
    assert _find_reusable_baseline_replay_dir(
        **{**lookup, "adaptation_fingerprint": "sha256:changed-adaptation"}
    ) is not None
    assert _find_reusable_baseline_replay_dir(
        **{**lookup, "support_fingerprint": "sha256:changed-support"}
    ) is None
    assert _find_reusable_baseline_replay_dir(
        **{
            **lookup,
            "timeout_envelope_fingerprint": "sha256:changed-timeout-envelope",
        }
    ) is None


@pytest.mark.asyncio
async def test_multi_case_baseline_reuse_requires_exact_root_repetition_count(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    first = _dataset("Replay task A", task_id="task-a")
    dataset = SelfEvolveDataset(
        cases=(
            first.cases[0],
            EvalCase(case_id="task-b", input={"content": "Replay task B"}),
        ),
        recipe=first.recipe,
    )
    bundle = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo")
    candidate = CandidateVariant(
        candidate_id="cand-multi",
        target=target,
        content="---\nname: demo\n---\n# Demo\n",
        rationale="test",
        target_fingerprint="sha256:baseline",
    )

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    request = build_replay_request(
        run_id="run-multi",
        workspace_root=workspace,
        target=target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        replay_adaptation=bundle,
        baseline_repetitions=3,
        candidate_repetitions=1,
    )
    await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )
    lookup = {
        "store": FilesystemSelfEvolveStore(workspace),
        "run_id": "next-run",
        "target": target,
        "dataset": dataset,
        "baseline_skill_fingerprint": request.baseline_skill_fingerprint,
        "dataset_fingerprint": request.dataset_fingerprint,
        "adaptation_fingerprint": request.adaptation_fingerprint,
        "workspace_seed_fingerprint": request.workspace_seed_fingerprint,
        "support_fingerprint": request.support_fingerprint,
        "timeout_envelope_fingerprint": (
            request.timeout_envelope_fingerprint
        ),
    }

    assert _find_reusable_baseline_replay_dir(
        **lookup,
        baseline_repetitions=3,
    ) is not None
    assert _find_reusable_baseline_replay_dir(
        **lookup,
        baseline_repetitions=4,
    ) is None


@pytest.mark.asyncio
async def test_behavior_only_siblings_share_baseline_with_same_support_fingerprint(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    dataset = _dataset("Replay task", task_id="task-a")
    bundle = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=workspace,
        artifact_root=tmp_path / "adaptation",
    )
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo")
    first = CandidateVariant(
        candidate_id="behavior-a",
        target=target,
        content="# Demo\n\nBehavior A.\n",
        rationale="first behavior",
        target_fingerprint="sha256:baseline",
    )
    second = replace(
        first,
        candidate_id="behavior-b",
        content="# Demo\n\nBehavior B.\n",
        rationale="second behavior",
    )
    calls: list[str] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request.variant_id)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    backend = AWorldCliCandidateReplayBackend(executor=fake_executor)
    first_request = build_replay_request(
        run_id="support-sibling-a",
        workspace_root=workspace,
        target=target,
        candidate=first,
        overlay_skill_root=tmp_path / "overlay-a",
        dataset=dataset,
        replay_adaptation=bundle,
        timeout_seconds=120,
        max_steps=4,
        max_tool_calls=8,
    )
    await backend.replay_candidate(
        first_request,
        candidate=first,
        dataset=dataset,
    )
    baseline_dir = _find_reusable_baseline_replay_dir(
        store=FilesystemSelfEvolveStore(workspace),
        run_id="support-sibling-b",
        target=target,
        dataset=dataset,
        baseline_repetitions=1,
        baseline_skill_fingerprint=first_request.baseline_skill_fingerprint,
        dataset_fingerprint=first_request.dataset_fingerprint,
        adaptation_fingerprint=first_request.adaptation_fingerprint,
        workspace_seed_fingerprint=first_request.workspace_seed_fingerprint,
        support_fingerprint=first_request.support_fingerprint,
        timeout_envelope_fingerprint=(
            first_request.timeout_envelope_fingerprint
        ),
    )
    assert baseline_dir is not None

    calls.clear()
    second_request = build_replay_request(
        run_id="support-sibling-b",
        workspace_root=workspace,
        target=target,
        candidate=second,
        overlay_skill_root=tmp_path / "overlay-b",
        dataset=dataset,
        replay_adaptation=bundle,
        timeout_seconds=120,
        max_steps=4,
        max_tool_calls=8,
        baseline_replay_dir=baseline_dir,
    )
    assert second_request.support_fingerprint == first_request.support_fingerprint
    await backend.replay_candidate(
        second_request,
        candidate=second,
        dataset=dataset,
    )

    assert calls == [second.candidate_id]


def _result_with_request_provenance(request, candidate_id: str) -> CandidateReplayResult:
    metrics = {
        "adaptation_fingerprint": request.adaptation_fingerprint,
        "workspace_seed_fingerprint": request.workspace_seed_fingerprint,
        "task_input_fingerprint": request.task_input_fingerprint,
        "dataset_fingerprint": request.dataset_fingerprint,
        "baseline_skill_fingerprint": request.baseline_skill_fingerprint,
        "adapter_determinism": "deterministic",
    }
    capability = (
        request.replay_adaptation.replay_capability
        if request.replay_adaptation is not None
        else None
    )
    if capability is not None:
        metrics.update(
            {
                "replay_capability_id": capability.capability_id,
                "capability_package_fingerprint": (
                    capability.capability_package_fingerprint
                ),
                "frozen_capability_fingerprint": capability.fingerprint,
                "service_runtime_fingerprint": capability.fingerprint,
                "service_logical_ids": json.dumps(
                    sorted(service.service_id for service in capability.services),
                    separators=(",", ":"),
                ),
                "service_startup_status": "ready",
                "service_cleanup_status": "stopped",
            }
        )
    workspace_base = Path(request.workspace_root).resolve() / ".fake_replay_workspaces"
    return CandidateReplayResult(
        request=request,
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="succeeded",
            trajectory=[{"action": {"content": "baseline"}}],
            metrics={
                **metrics,
                "isolated_workspace_path": str(workspace_base / "baseline"),
                **(
                    {
                        "service_endpoint": json.dumps(
                            {"recorded-http": "http://127.0.0.1:41001"},
                            separators=(",", ":"),
                        )
                    }
                    if capability is not None
                    else {}
                ),
            },
        ),
        candidate=ReplayVariantResult(
            variant_id=candidate_id,
            status="succeeded",
            trajectory=[{"action": {"content": candidate_id}}],
            metrics={
                **metrics,
                "isolated_workspace_path": str(workspace_base / candidate_id),
                **(
                    {
                        "service_endpoint": json.dumps(
                            {"recorded-http": "http://127.0.0.1:41002"},
                            separators=(",", ":"),
                        ),
                        # The fake backend represents a rollout that reached
                        # the candidate-owned replay service.  Keep that
                        # causal observation explicit now that the replay gate
                        # rejects merely configured-but-unexercised support.
                        "replay_service_protocol_trace_count": 1,
                    }
                    if capability is not None
                    else {}
                ),
            },
        ),
    )


@pytest.mark.asyncio
async def test_runner_compiles_adaptation_before_building_replay_request(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    dataset = _dataset(f"Read {tmp_path}/input.txt")
    (tmp_path / "input.txt").write_text("seed", encoding="utf-8")
    target = SkillTextTarget(skill_path)
    candidate = CandidateVariant(
        candidate_id="cand-runner",
        target=target.identity,
        content="---\nname: demo\n---\n# Demo\nImproved.\n",
        rationale="test",
        target_fingerprint=target.fingerprint_current_content(),
    )

    class CapturingBackend:
        def __init__(self) -> None:
            self.requests = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.requests.append(request)
            return _result_with_request_provenance(request, candidate.candidate_id)

    backend = CapturingBackend()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=object(),
        replay_enabled=True,
        candidate_replay_backend=backend,
    )

    replay_result, paired_dataset, gate = await runner._replay_selected_candidate(
        run_id="run-adapted",
        target=target,
        dataset=dataset,
        selected_candidate=candidate,
        apply_policy="proposal",
    )

    assert replay_result is not None, gate
    assert paired_dataset is not None
    assert gate is not None and gate.passed is True
    request = backend.requests[0]
    assert request.replay_adaptation is not None
    assert request.replay_adaptation.ready is True
    assert "${AWORLD_REPLAY_WORKSPACE}/input.txt" in request.task_input["content"]
    assert (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-adapted"
        / "replay_adaptation"
    ).is_dir()


@pytest.mark.asyncio
async def test_runner_blocks_unresolved_adaptation_before_rollout(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path)
    candidate = CandidateVariant(
        candidate_id="cand-blocked",
        target=target.identity,
        content="---\nname: demo\n---\n# Demo\nImproved.\n",
        rationale="test",
        target_fingerprint=target.fingerprint_current_content(),
    )

    class FailingBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            raise AssertionError("rollout must not start for unresolved adaptation")

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=object(),
        replay_enabled=True,
        candidate_replay_backend=FailingBackend(),
    )

    for policy in ("proposal", "auto_verified"):
        replay_result, paired_dataset, gate = await runner._replay_selected_candidate(
            run_id=f"run-blocked-{policy}",
            target=target,
            dataset=_dataset("Inspect http://127.0.0.1:9222"),
            selected_candidate=candidate,
            apply_policy=policy,
        )
        assert replay_result is None
        assert paired_dataset is None
        assert gate is not None
        assert gate.gate_name == "replay_capability"
        assert gate.passed is False
        assert gate.details["requirement_count"] == 1
        assert gate.details["requirement_kinds"] == ["local_endpoint"]


@pytest.mark.asyncio
async def test_runner_loads_replay_capability_from_candidate_overlay(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path)
    manifest = {
        "schema_version": "aworld.skill.replay_capability.v1",
        "capability_id": "recorded-local",
        "protocol": "aworld.replay.subprocess.v1",
        "entrypoint": "replay/compiler.py",
        "handles": ["local_endpoint"],
        "runtime_files": ["replay/runtime.py"],
    }
    compiler = """
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
(output / 'recording.json').write_text('historical result', encoding='utf-8')
requirement = request['requirements'][0]
result = {
    'schema_version': 'aworld.replay.capability_result.v1',
    'capability_id': 'recorded-local',
    'deterministic': True,
    'handled_requirements': [requirement['requirement_id']],
    'unhandled_requirements': [],
    'evidence_refs': {
        requirement['requirement_id']: requirement['evidence_refs'],
    },
    'fixture_evidence_refs': {
        'recording.json': requirement['evidence_refs'],
    },
    'fixtures': ['recording.json'],
    'endpoint_replacements': {requirement['identifier']: 'recorded-http'},
    'services': [{
        'service_id': 'recorded-http',
        'requirement_id': requirement['requirement_id'],
        'transport': 'skill_runtime',
        'response_fixture': 'recording.json',
        'runtime_entrypoint': 'replay.runtime:main',
        'readiness': {'kind': 'tcp', 'timeout_seconds': 2},
        'protocol_probes': [{
            'kind': 'http',
            'path': '/',
            'response_contains': 'historical result',
        }],
    }],
}
(output / 'result.json').write_text(json.dumps(result, sort_keys=True), encoding='utf-8')
"""
    candidate = CandidateVariant(
        candidate_id="cand-capability",
        target=target.identity,
        content="---\nname: demo\n---\n# Demo\nImproved.\n",
        rationale="supply recorded local endpoint",
        target_fingerprint=target.fingerprint_current_content(),
        files=(
            CandidateFileDelta(
                path="replay/capability.json",
                content=json.dumps(manifest),
            ),
            CandidateFileDelta(path="replay/compiler.py", content=compiler),
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def main(argv=None):\n    return 0\n",
            ),
        ),
    )

    class CapturingBackend:
        def __init__(self) -> None:
            self.requests = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.requests.append(request)
            return _result_with_request_provenance(request, candidate.candidate_id)

    backend = CapturingBackend()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=object(),
        replay_enabled=True,
        candidate_replay_backend=backend,
    )

    replay_result, paired_dataset, gate = await runner._replay_selected_candidate(
        run_id="run-capability",
        target=target,
        dataset=_dataset("Inspect http://127.0.0.1:9222"),
        selected_candidate=candidate,
        apply_policy="proposal",
    )

    assert replay_result is not None, gate
    assert paired_dataset is not None
    assert gate is not None and gate.passed is True
    adaptation = backend.requests[0].replay_adaptation
    assert adaptation is not None and adaptation.ready is True
    assert adaptation.replay_capability is not None
    assert adaptation.replay_capability.capability_id == "recorded-local"
    assert adaptation.replay_capability.ready is True
    assert adaptation.case("task-1").dependencies[0].status == "adapter_bound"
    assert not (skill_path.parent / "replay").exists()


@pytest.mark.asyncio
async def test_unresolved_adaptation_preserves_proposal_but_rejects_verified_apply(
    tmp_path: Path,
) -> None:
    class SingleCandidateOptimizer:
        async def propose(self, request):
            from aworld.self_evolve.optimizers.base import OptimizerResult

            return OptimizerResult(
                candidates=(
                    CandidateVariant(
                        candidate_id="cand-policy",
                        target=request.target,
                        content="---\nname: demo\n---\n# Demo\nImproved.\n",
                        rationale="test",
                        target_fingerprint=request.target_fingerprint,
                    ),
                )
            )

    class FailingBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            raise AssertionError("blocked adaptation must not start rollout")

    for policy, expected_status in (
        ("proposal", "succeeded"),
        ("auto_verified", "rejected"),
    ):
        workspace = tmp_path / policy
        skill_path = workspace / "skills" / "demo" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
        dataset = _dataset("Inspect http://127.0.0.1:9222")
        runner = SelfEvolveRunner(
            store=FilesystemSelfEvolveStore(workspace),
            optimizer=SingleCandidateOptimizer(),
            replay_enabled=True,
            candidate_replay_backend=FailingBackend(),
            min_eval_cases=0,
        )

        result = await runner.run_explicit_target(
            run_id=f"run-policy-{policy}",
            target=SkillTextTarget(skill_path, allow_auto_apply=True),
            dataset=dataset,
            trace_packs=(dataset.cases[0].trace_pack,),
            apply_policy=policy,
        )

        assert result.run.status.value == expected_status
        assert result.selected_candidate is None
        report = json.loads(
            (
                workspace
                / ".aworld"
                / "self_evolve"
                / f"run-policy-{policy}"
                / "report.json"
            ).read_text(encoding="utf-8")
        )
        assert report["repair_focus_candidate_id"] == "cand-policy"
        assert (
            workspace
            / ".aworld"
            / "self_evolve"
            / f"run-policy-{policy}"
            / "candidates"
            / "cand-policy.json"
        ).is_file()
        assert any(
            gate.gate_name == "candidate_capability_replay" and not gate.passed
            for gate in result.run.gate_results
        )
