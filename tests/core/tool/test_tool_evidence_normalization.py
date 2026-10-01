from aworld.core.common import ActionModel, ActionResult
from aworld.core.tool.evidence import (
    enforce_pipeline_failure_semantics,
    normalize_action_result_evidence,
    tool_evidence_normalization_enabled,
)


def test_shell_pipeline_is_protected_by_default(monkeypatch):
    monkeypatch.delenv("AWORLD_TOOL_EVIDENCE_NORMALIZATION", raising=False)
    action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        params={"command": "python check.py | tail -1"},
    )

    enforce_pipeline_failure_semantics([action])

    assert tool_evidence_normalization_enabled() is True
    assert action.params["command"].startswith("set -o pipefail; ")


def test_pipeline_rewrite_can_be_disabled_for_canary(monkeypatch):
    monkeypatch.setenv("AWORLD_TOOL_EVIDENCE_NORMALIZATION", "false")
    action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        params={"command": "false | true"},
    )

    enforce_pipeline_failure_semantics([action])

    assert tool_evidence_normalization_enabled() is False
    assert action.params["command"] == "false | true"


def test_failed_pipeline_component_overrides_transport_success(monkeypatch):
    monkeypatch.delenv("AWORLD_TOOL_EVIDENCE_NORMALIZATION", raising=False)
    result = ActionResult(
        success=True,
        content={"stdout": "PASS", "pipeline_status": [3, 0]},
    )

    code = normalize_action_result_evidence(result)

    assert code == "pipeline_component_failed"
    assert result.success is False
    assert result.error == "semantic_failure:pipeline_component_failed"
    assert result.metadata["semantic_failure"]["code"] == code


def test_python_traceback_with_exit_zero_is_semantic_failure(monkeypatch):
    monkeypatch.delenv("AWORLD_TOOL_EVIDENCE_NORMALIZATION", raising=False)
    result = ActionResult(
        success=True,
        content="wrapper completed",
        metadata={
            "stderr": (
                "Traceback (most recent call last):\n"
                '  File "check.py", line 1, in <module>\n'
                "AssertionError: wrong value\n"
            )
        },
    )

    assert (
        normalize_action_result_evidence(
            result,
            action=ActionModel(tool_name="terminal", action_name="execute"),
        )
        == "python_traceback"
    )
    assert result.success is False


def test_structured_tool_error_overrides_success(monkeypatch):
    monkeypatch.delenv("AWORLD_TOOL_EVIDENCE_NORMALIZATION", raising=False)
    result = ActionResult(
        success=True,
        content="runner wrapper returned",
        metadata={"return_code": 7},
    )

    assert normalize_action_result_evidence(result) == "nonzero_exit"
    assert result.success is False


def test_benign_stderr_is_not_failure(monkeypatch):
    monkeypatch.delenv("AWORLD_TOOL_EVIDENCE_NORMALIZATION", raising=False)
    result = ActionResult(
        success=True,
        content="done",
        metadata={"stderr": "warning: cache directory was not writable"},
    )

    assert normalize_action_result_evidence(result) is None
    assert result.success is True
    assert result.error is None


def test_normalization_opt_out_preserves_transport_status(monkeypatch):
    monkeypatch.setenv("AWORLD_TOOL_EVIDENCE_NORMALIZATION", "0")
    result = ActionResult(
        success=True,
        content="Traceback (most recent call last):\nValueError: boom",
    )

    assert normalize_action_result_evidence(result) is None
    assert result.success is True


def test_traceback_text_from_non_shell_tool_is_not_reclassified(monkeypatch):
    monkeypatch.delenv("AWORLD_TOOL_EVIDENCE_NORMALIZATION", raising=False)
    result = ActionResult(
        success=True,
        content="Traceback (most recent call last):\nValueError: quoted documentation",
    )

    assert (
        normalize_action_result_evidence(
            result,
            action=ActionModel(tool_name="http", action_name="fetch"),
        )
        is None
    )
    assert result.success is True


def test_shell_failure_words_in_stdout_are_not_reclassified(monkeypatch):
    monkeypatch.delenv("AWORLD_TOOL_EVIDENCE_NORMALIZATION", raising=False)
    result = ActionResult(success=True, content="example: command not found")

    assert normalize_action_result_evidence(
        result,
        action=ActionModel(tool_name="terminal", action_name="execute"),
    ) is None
    assert result.success is True


def test_explicit_dash_executor_is_not_given_pipefail(monkeypatch):
    monkeypatch.delenv("AWORLD_TOOL_EVIDENCE_NORMALIZATION", raising=False)
    action = ActionModel(
        tool_name="terminal",
        action_name="execute",
        params={"command": "false | true", "shell": "/bin/sh"},
    )

    enforce_pipeline_failure_semantics([action])

    assert action.params["command"] == "false | true"


def test_explicit_bash_executor_gets_pipefail(monkeypatch):
    monkeypatch.delenv("AWORLD_TOOL_EVIDENCE_NORMALIZATION", raising=False)
    action = ActionModel(
        tool_name="shell",
        action_name="execute",
        params={"command": "false | true", "shell": "/bin/bash"},
    )

    enforce_pipeline_failure_semantics([action])

    assert action.params["command"].startswith("set -o pipefail; ")
