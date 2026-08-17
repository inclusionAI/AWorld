from __future__ import annotations

import json

import pytest

from aworld.core.common import ActionModel, ActionResult, Observation
from aworld.core.tool.base import ToolExecutionDenied, _enforce_runtime_tool_call_budget
from aworld.core.tool.base import _enforce_replay_evidence_runtime_policy
from aworld.core.tool.replay_policy import record_replay_runtime_tool_result


class _Context:
    pass


class _Message:
    def __init__(self, context: object) -> None:
        self.context = context


def _actions(count: int) -> list[ActionModel]:
    return [
        ActionModel(tool_name="tool", action_name="run", tool_call_id=f"call-{index}")
        for index in range(count)
    ]


def test_runtime_tool_call_budget_is_disabled_without_environment_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_TOOL_CALL_LIMIT", raising=False)
    message = _Message(_Context())

    _enforce_runtime_tool_call_budget("tool", _actions(100), message)


def test_runtime_tool_call_budget_counts_actions_across_tool_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TOOL_CALL_LIMIT", "3")
    context = _Context()
    message = _Message(context)

    _enforce_runtime_tool_call_budget("first-tool", _actions(2), message)
    _enforce_runtime_tool_call_budget("second-tool", _actions(1), message)

    with pytest.raises(ToolExecutionDenied, match="runtime tool-call budget exhausted"):
        _enforce_runtime_tool_call_budget("third-tool", _actions(1), message)

    assert context._aworld_runtime_tool_call_count == 3


def test_runtime_tool_call_budget_ignores_invalid_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TOOL_CALL_LIMIT", "not-an-integer")

    _enforce_runtime_tool_call_budget("tool", _actions(2), _Message(_Context()))


def _replay_action(
    command: str,
    *,
    resource_ownership_token: str | None = None,
    isolation_identity: str | None = None,
    resource_identity: str | None = None,
) -> list[ActionModel]:
    params = {"command": command}
    if resource_ownership_token is not None:
        params["resource_ownership_token"] = resource_ownership_token
    if isolation_identity is not None:
        params["isolation_identity"] = isolation_identity
    if resource_identity is not None:
        params["resource_identity"] = resource_identity
    return [
        ActionModel(
            tool_name="bash",
            action_name="run",
            tool_call_id="call-replay",
            params=params,
        )
    ]


def _enable_replay_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    monkeypatch.setenv("AWORLD_REPLAY_EVIDENCE_POLICY", "1")
    monkeypatch.setenv("AWORLD_REPLAY_EVIDENCE_POLICY_MODE", "legacy")
    monkeypatch.setenv(
        "AWORLD_REPLAY_ISOLATION_IDENTITY", "isolation.legacy-lane"
    )
    monkeypatch.setenv(
        "AWORLD_REPLAY_RESOURCE_IDENTITY", "resource.legacy-browser"
    )
    monkeypatch.setenv(
        "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR", str(artifact_dir)
    )
    monkeypatch.setenv(
        "AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST",
        str(artifact_dir / "evidence_manifest.jsonl"),
    )


def test_replay_runtime_policy_rejects_collection_after_artifact_quota(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_replay_policy(monkeypatch, tmp_path)
    artifact_dir = tmp_path / "artifacts"
    nested = artifact_dir / "nested"
    nested.mkdir()
    (nested / "evidence.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AWORLD_REPLAY_ARTIFACT_FILE_LIMIT", "1")

    with pytest.raises(ToolExecutionDenied, match="artifact_file_limit_exhausted"):
        _enforce_replay_evidence_runtime_policy(
            "bash",
            _replay_action("agent-browser screenshot extra.png"),
            _Message(_Context()),
        )

    violation = (tmp_path / "artifacts" / "framework_evidence_policy.jsonl").read_text(
        encoding="utf-8"
    )
    assert json.loads(violation)["code"] == "artifact_file_limit_exhausted"
    assert "screenshot" not in violation


def test_replay_runtime_policy_allows_provably_read_only_analysis_at_quota(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_replay_policy(monkeypatch, tmp_path)
    artifact_dir = tmp_path / "artifacts"
    evidence = artifact_dir / "evidence.txt"
    evidence.write_text("bounded evidence", encoding="utf-8")
    monkeypatch.setenv("AWORLD_REPLAY_ARTIFACT_FILE_LIMIT", "1")

    action = ActionModel(
        tool_name="CAST_SEARCH",
        action_name="read_file",
        tool_call_id="call-read-existing-evidence",
        params={"file_path": str(evidence)},
    )

    assert (
        _enforce_replay_evidence_runtime_policy(
            "CAST_SEARCH",
            [action],
            _Message(_Context()),
        )
        is None
    )


def test_replay_runtime_policy_keeps_unknown_or_output_actions_closed_at_quota(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_replay_policy(monkeypatch, tmp_path)
    artifact_dir = tmp_path / "artifacts"
    (artifact_dir / "evidence.txt").write_text("bounded evidence", encoding="utf-8")
    monkeypatch.setenv("AWORLD_REPLAY_ARTIFACT_FILE_LIMIT", "1")

    for action in (
        ActionModel(
            tool_name="custom",
            action_name="transform",
            tool_call_id="call-unknown",
            params={"file_path": str(artifact_dir / "evidence.txt")},
        ),
        ActionModel(
            tool_name="custom",
            action_name="read_file",
            tool_call_id="call-read-with-output",
            params={
                "file_path": str(artifact_dir / "evidence.txt"),
                "output_path": str(artifact_dir / "copy.txt"),
            },
        ),
    ):
        with pytest.raises(ToolExecutionDenied, match="artifact_file_limit_exhausted"):
            _enforce_replay_evidence_runtime_policy(
                action.tool_name,
                [action],
                _Message(_Context()),
            )


def test_replay_runtime_policy_ignores_seeded_workspace_outside_evidence_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    run_root = tmp_path / "run"
    evidence_root = run_root / "evidence"
    workspace_root = run_root / "workspace"
    evidence_root.mkdir(parents=True)
    workspace_root.mkdir(parents=True)
    for index in range(32):
        (workspace_root / f"seed-{index}.txt").write_bytes(b"x" * 100_000)
    monkeypatch.setenv("AWORLD_REPLAY_EVIDENCE_POLICY", "1")
    monkeypatch.setenv("AWORLD_REPLAY_EVIDENCE_POLICY_MODE", "legacy")
    monkeypatch.setenv(
        "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR", str(evidence_root)
    )
    monkeypatch.setenv(
        "AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST",
        str(evidence_root / "evidence_manifest.jsonl"),
    )
    monkeypatch.setenv("AWORLD_REPLAY_ARTIFACT_FILE_LIMIT", "8")
    monkeypatch.setenv("AWORLD_REPLAY_ARTIFACT_BYTE_LIMIT", "2000000")

    _enforce_replay_evidence_runtime_policy(
        "bash",
        _replay_action("agent-browser screenshot evidence.png"),
        _Message(_Context()),
    )

    state = json.loads(
        (evidence_root / "framework_evidence_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["artifact_file_count"] == 0
    assert state["artifact_bytes"] == 0
    assert not (evidence_root / "framework_evidence_policy.jsonl").exists()


def test_replay_runtime_policy_rejects_collection_after_evidence_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_replay_policy(monkeypatch, tmp_path)
    artifact_dir = tmp_path / "artifacts"
    evidence = artifact_dir / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    (artifact_dir / "evidence_manifest.jsonl").write_text(
        '{"source_id":"one","extraction_method":"json_fields",'
        '"artifact_path":"evidence.json","fields":["title"]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ToolExecutionDenied, match="tool_call_after_evidence_ready"):
        _enforce_replay_evidence_runtime_policy(
            "bash",
            _replay_action("agent-browser screenshot extra.png"),
            _Message(_Context()),
        )

    state = json.loads(
        (artifact_dir / "framework_evidence_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["phase"] == "evidence_ready"


def test_replay_runtime_policy_allows_one_browser_cleanup_after_evidence_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_replay_policy(monkeypatch, tmp_path)
    artifact_dir = tmp_path / "artifacts"
    evidence = artifact_dir / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    (artifact_dir / "evidence_manifest.jsonl").write_text(
        '{"source_id":"one","extraction_method":"json_fields",'
        '"artifact_path":"evidence.json","fields":["title"]}\n',
        encoding="utf-8",
    )
    message = _Message(_Context())
    ownership_token = "owned-browser-runtime"
    monkeypatch.setenv(
        "AWORLD_REPLAY_RESOURCE_OWNERSHIP_TOKEN", ownership_token
    )

    _enforce_replay_evidence_runtime_policy(
        "bash",
        _replay_action(
            "agent-browser close 2>&1",
            resource_ownership_token=ownership_token,
            isolation_identity="isolation.legacy-lane",
            resource_identity="resource.legacy-browser",
        ),
        message,
    )

    state = json.loads(
        (artifact_dir / "framework_evidence_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["phase"] == "finalizing"
    assert state["finalization_action_count"] == 1
    with pytest.raises(
        ToolExecutionDenied,
        match="tool_call_after_evidence_ready",
    ):
        _enforce_replay_evidence_runtime_policy(
            "bash",
            _replay_action(
                "agent-browser close",
                resource_ownership_token=ownership_token,
                isolation_identity="isolation.legacy-lane",
                resource_identity="resource.legacy-browser",
            ),
            _Message(_Context()),
        )


def test_replay_runtime_policy_requires_cleanup_ownership_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_replay_policy(monkeypatch, tmp_path)
    artifact_dir = tmp_path / "artifacts"
    (artifact_dir / "evidence.json").write_text("{}", encoding="utf-8")
    (artifact_dir / "evidence_manifest.jsonl").write_text(
        '{"source_id":"one","extraction_method":"json_fields",'
        '"artifact_path":"evidence.json","fields":["title"]}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "AWORLD_REPLAY_RESOURCE_OWNERSHIP_TOKEN", "owned-browser-runtime"
    )

    with pytest.raises(ToolExecutionDenied, match="tool_call_after_evidence_ready"):
        _enforce_replay_evidence_runtime_policy(
            "bash",
            _replay_action("agent-browser close"),
            _Message(_Context()),
        )


@pytest.mark.parametrize(
    "command",
    (
        "agent-browser close 2>&1 | head -5",
        "agent-browser close 2>cleanup.log",
    ),
)
def test_replay_runtime_policy_rejects_unsafe_browser_cleanup_suffixes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    command: str,
) -> None:
    _enable_replay_policy(monkeypatch, tmp_path)
    artifact_dir = tmp_path / "artifacts"
    (artifact_dir / "evidence.json").write_text("{}", encoding="utf-8")
    (artifact_dir / "evidence_manifest.jsonl").write_text(
        '{"source_id":"one","extraction_method":"json_fields",'
        '"artifact_path":"evidence.json","fields":["title"]}\n',
        encoding="utf-8",
    )

    with pytest.raises(
        ToolExecutionDenied,
        match="tool_call_after_evidence_ready",
    ):
        _enforce_replay_evidence_runtime_policy(
            "bash",
            _replay_action(command),
            _Message(_Context()),
        )


def test_replay_runtime_policy_enforces_declared_loopback_endpoints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_replay_policy(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "AWORLD_REPLAY_ENDPOINT_BROWSER",
        "http://127.0.0.1:4100",
    )

    _enforce_replay_evidence_runtime_policy(
        "browser",
        _replay_action("open ws://127.0.0.1:4100/devtools/session"),
        _Message(_Context()),
    )
    with pytest.raises(ToolExecutionDenied, match="undeclared_loopback_endpoint"):
        _enforce_replay_evidence_runtime_policy(
            "browser",
            _replay_action("open http://127.0.0.1:9222/json"),
            _Message(_Context()),
        )


def test_replay_runtime_policy_requires_typed_control_plane_authorization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_replay_policy(monkeypatch, tmp_path)

    with pytest.raises(
        ToolExecutionDenied,
        match="unauthorized_control_plane_action",
    ):
        _enforce_replay_evidence_runtime_policy(
            "bash",
            _replay_action("pkill browser-driver"),
            _Message(_Context()),
        )

    monkeypatch.setenv("AWORLD_REPLAY_ALLOWED_CONTROL_ACTIONS", "pkill")
    _enforce_replay_evidence_runtime_policy(
        "bash",
        _replay_action("pkill browser-driver"),
        _Message(_Context()),
    )


def test_replay_runtime_policy_preserves_isolated_runtime_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_replay_policy(monkeypatch, tmp_path)

    with pytest.raises(ToolExecutionDenied, match="protected_runtime_root_override"):
        _enforce_replay_evidence_runtime_policy(
            "bash",
            _replay_action("export HOME=/tmp/host-profile; run-browser"),
            _Message(_Context()),
        )

    structured = [
        ActionModel(
            tool_name="process",
            action_name="run",
            tool_call_id="call-structured-env",
            params={
                "command": "run-browser",
                "environment": {"XDG_CONFIG_HOME": "/tmp/host-profile"},
            },
        )
    ]
    with pytest.raises(ToolExecutionDenied, match="protected_runtime_root_override"):
        _enforce_replay_evidence_runtime_policy(
            "process",
            structured,
            _Message(_Context()),
        )


def test_replay_runtime_policy_blocks_host_discovery_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_replay_policy(monkeypatch, tmp_path)

    with pytest.raises(ToolExecutionDenied, match="host_discovery_forbidden"):
        _enforce_replay_evidence_runtime_policy(
            "bash",
            _replay_action("lsof -nP -iTCP -sTCP:LISTEN"),
            _Message(_Context()),
        )
    monkeypatch.setenv("AWORLD_REPLAY_ALLOWED_CONTROL_ACTIONS", "lsof")
    _enforce_replay_evidence_runtime_policy(
        "bash",
        _replay_action("lsof -nP -iTCP -sTCP:LISTEN"),
        _Message(_Context()),
    )


def test_replay_runtime_policy_breaks_consecutive_failed_action_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_replay_policy(monkeypatch, tmp_path)
    monkeypatch.setenv("AWORLD_REPLAY_MAX_CONSECUTIVE_FAILED_ACTIONS", "2")
    context = _Context()
    message = _Message(context)
    repeated = _replay_action("agent-browser snapshot")
    failed_result = (
        Observation(
            action_result=[
                ActionResult(
                    is_done=True,
                    success=False,
                    error="bounded failure",
                )
            ]
        ),
        0.0,
        False,
        False,
        {},
    )

    for _ in range(2):
        _enforce_replay_evidence_runtime_policy("bash", repeated, message)
        record_replay_runtime_tool_result(repeated, failed_result, message)

    with pytest.raises(ToolExecutionDenied, match="repeated_failed_action_limit"):
        _enforce_replay_evidence_runtime_policy("bash", repeated, message)

    _enforce_replay_evidence_runtime_policy(
        "bash",
        _replay_action("agent-browser inspect-errors"),
        message,
    )
