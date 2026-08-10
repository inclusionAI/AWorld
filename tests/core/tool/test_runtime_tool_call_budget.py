from __future__ import annotations

import pytest

from aworld.core.common import ActionModel
from aworld.core.tool.base import ToolExecutionDenied, _enforce_runtime_tool_call_budget
from aworld.core.tool.base import _enforce_replay_evidence_runtime_policy


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


def _replay_action(command: str) -> list[ActionModel]:
    return [
        ActionModel(
            tool_name="bash",
            action_name="run",
            tool_call_id="call-replay",
            params={"command": command},
        )
    ]


def _enable_replay_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    monkeypatch.setenv("AWORLD_REPLAY_EVIDENCE_POLICY", "1")
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
    assert '"code": "artifact_file_limit_exhausted"' in violation
    assert "screenshot" not in violation


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

    state = (artifact_dir / "framework_evidence_state.json").read_text(
        encoding="utf-8"
    )
    assert '"phase": "evidence_ready"' in state
