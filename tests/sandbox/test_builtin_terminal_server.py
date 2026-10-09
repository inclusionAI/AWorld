import asyncio
import base64
import json
import os
from pathlib import Path
import shlex
import sys
import time
from types import SimpleNamespace

import pytest

from aworld.core.common import ActionResult
from aworld.sandbox.tool_servers.terminal.src import terminal as terminal_module
from aworld.sandbox.tool_servers.terminal.src.terminal import (
    CommandResult,
    _BoundedStreamCapture,
    _HARD_MAX_TOTAL_CAPTURE_BYTES,
    _background_drain_tasks,
    _bounded_inline_stream,
    _check_command_safety,
    _execute_command_async,
    _format_command_output,
    _get_total_capture_limit_bytes,
    _has_background_operator,
    _resolve_command_timeout,
    _terminal_execution_plan,
    _terminal_receipt_plan,
    read_output_artifact,
    run_code,
)
from aworld.sandbox.terminal_receipt import (
    _RG_VALUE_OPTIONS,
    _rg_actual_literal_flags,
    terminal_command_sha256,
)
from aworld.sandbox.task_budget import FrameworkTaskBudget
from aworld.sandbox.tool_observation import SandboxToolObservationRuntime


def _result(*, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(
        command="demo",
        success=True,
        stdout=stdout,
        stderr=stderr,
        return_code=0,
        duration="0:00:00.001000",
        timestamp="2026-09-15T12:00:00",
    )


def test_internal_execution_authority_is_not_projected_to_task_commands() -> None:
    environment = terminal_module._resolve_environment(None)

    assert terminal_module.TERMINAL_EXECUTION_AUTHORITY_ENV not in environment


@pytest.mark.parametrize(
    ("command", "effect"),
    (
        ("cat a; wc -l b", "read_only"),
        ("cat a | head -1", "read_only"),
        ("cat a\nwc -l b", "read_only"),
        ("cat a >/dev/null", "read_only"),
        ("cat a 2>&1 | head", "read_only"),
        ("cat < input.txt", "read_only"),
        ("cat a > output.txt", "mutating"),
        ("echo x >> output.txt", "mutating"),
        ('echo x > "$OUT"', "mutating"),
        ("cat <> shared.txt", "mutating"),
        ("python -c 'print(1 > 0)'", "read_only"),
        ("python -c \"open('x', 'w').write('x')\"", "mutating"),
        ("python -c 'import sys; sys.stdout.write(\"x\")'", "unknown"),
        ("python script.py", "unknown"),
        ("if depth > 3:\n    print(depth)", "unknown"),
        ("sort -o out.txt input.txt", "unknown"),
        ("uniq input.txt output.txt", "unknown"),
        ("file -C -m magic", "unknown"),
        ("sed -n '1w out.txt' input.txt", "unknown"),
        ("sed -e 'e touch out.txt' input.txt", "unknown"),
        ("sort --compress-program='sh -c touch out.txt' input.txt", "unknown"),
        ('echo "$VALUE"', "unknown"),
        ("printf '%s' \"$VALUE\"", "unknown"),
        ("echo $(touch out.txt)", "mutating"),
        ("PATH=/tmp cat input.txt", "unknown"),
        ("env PATH=/tmp cat input.txt", "unknown"),
        ("sudo cat input.txt", "unknown"),
        ("command cat input.txt", "unknown"),
        (". ./mutate.sh", "unknown"),
        ("time cat input.txt", "unknown"),
    ),
)
def test_terminal_execution_plan_preserves_shell_composition_effect(
    command: str,
    effect: str,
) -> None:
    plan = _terminal_execution_plan(command)

    assert plan.language == "shell"
    assert plan.effect == effect
    assert plan.cacheable is (effect == "read_only")


@pytest.mark.asyncio
async def test_dot_source_is_unknown_and_invalidates_sandbox_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(terminal_module, "workspace", tmp_path)
    script = tmp_path / "mutate.sh"
    marker = tmp_path / "sourced.txt"
    script.write_text("printf sourced > sourced.txt\n", encoding="utf-8")
    command = ". ./mutate.sh"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "tool_call_id": "call-dot-source",
        "params": {"code": command, "cwd": str(tmp_path)},
    }
    context = SimpleNamespace(
        task_id="task",
        task_epoch=1,
        session_id="session",
        agent_info=SimpleNamespace(current_agent_id="agent"),
        context_lifecycle_state=SimpleNamespace(
            session_id="session",
            session_epoch=0,
            task_epoch=1,
            branch_id="main",
            checkpoint_revision=0,
        ),
    )

    response = await run_code(None, command, timeout=10, cwd=str(tmp_path))
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]
    runtime = SandboxToolObservationRuntime()
    before = runtime.current_generation(context)
    runtime.record(
        action,
        ActionResult(
            success=True,
            tool_call_id="call-dot-source",
            content=payload["message"],
            parameter=action["params"],
            metadata=payload["metadata"],
        ),
        context=context,
    )

    assert marker.read_text(encoding="utf-8") == "sourced"
    assert receipt["effect"] == "unknown"
    assert receipt["cacheable"] is False
    assert runtime.lookup(action, context=context) is None
    assert runtime.current_generation(context) == before + 1


@pytest.mark.parametrize(
    "command",
    (
        "cat /proc/u?time",
        "rg pattern /*",
        "python -c \"p='/proc/uptime'; print(open(p).read())\"",
        "cat " + " ".join(f"file-{index}" for index in range(17)),
    ),
)
def test_terminal_execution_plan_marks_unresolved_read_set(command: str) -> None:
    plan = _terminal_execution_plan(command)

    assert plan.effect == "read_only"
    assert plan.read_set_complete is False


@pytest.mark.parametrize(
    ("command", "expected_path"),
    (
        ("test -f /app/input.txt", "/app/input.txt"),
        ("readlink -f /app/input.txt", "/app/input.txt"),
        ("realpath -m /app/input.txt", "/app/input.txt"),
        (
            "python -I -c \"import numpy as np; print(np.load('/app/x.npy'))\"",
            "/app/x.npy",
        ),
        (
            "python -I -c \"import toml; print(toml.load('/app/x.toml'))\"",
            "/app/x.toml",
        ),
    ),
)
def test_terminal_execution_plan_captures_read_operand(
    command: str,
    expected_path: str,
) -> None:
    plan = _terminal_execution_plan(command)

    assert plan.effect == "read_only"
    assert plan.read_set_complete is True
    assert expected_path in plan.read_paths


@pytest.mark.parametrize(
    ("command", "expected_paths", "expected_kind"),
    (
        ("rg -n -g '*.py' needle src/main.py", ("src/main.py",), "query"),
        ("rg --glob=*.py --regexp needle -- src/main.py", ("src/main.py",), "query"),
        ("grep -n -E -A 2 -e needle -- src/main.py", ("src/main.py",), "query"),
        ("head -n 25 src/main.py", ("src/main.py",), "line_range"),
        ("tail --lines=25 src/main.py", ("src/main.py",), "tail_lines"),
        ("sed -n 10,20p src/main.py", ("src/main.py",), "line_range"),
    ),
)
def test_read_only_option_parser_emits_paths_and_typed_coverage(
    command: str,
    expected_paths: tuple[str, ...],
    expected_kind: str,
) -> None:
    plan = _terminal_execution_plan(command)

    assert plan.effect == "read_only"
    assert plan.read_set_complete is True
    assert plan.read_paths == expected_paths
    assert tuple(item.kind for item in plan.read_ranges) == (expected_kind,)


@pytest.mark.parametrize(
    "command",
    (
        "rg --pre 'sh -c mutate' needle src",
        "rg --hostname-bin ./hostname needle src",
        "find . -type f -exec sh -c 'touch changed' ';'",
        "find . -delete",
        "find . -fprint output.txt",
        "git -c core.pager='sh -c touch changed' status",
        "git diff --ext-diff",
        "git show --textconv HEAD:file",
        "git diff --output=patch.txt",
        "git grep --open-files-in-pager='sh -c touch changed' needle",
        "git cat-file --filters HEAD:file",
        "git --help",
        "git status --help",
        "tail -f application.log",
    ),
)
def test_read_only_option_parser_rejects_executing_or_unbounded_forms(
    command: str,
) -> None:
    plan = _terminal_execution_plan(command)

    assert plan.effect == "unknown"
    assert plan.cacheable is False


@pytest.mark.parametrize(
    "command",
    (
        "find src -maxdepth 3 -type f -name '*.py' -print",
        "git --no-pager status --short",
        "git log --oneline -20",
        "git ls-files --cached",
    ),
)
def test_recursive_and_repository_queries_are_read_only_but_not_replay_complete(
    command: str,
) -> None:
    plan = _terminal_execution_plan(command)

    assert plan.effect == "read_only"
    assert plan.read_set_complete is False
    assert plan.cacheable is True


def test_callback_sensitive_reads_require_callback_free_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("needle\n", encoding="utf-8")
    (tmp_path / "f").write_text("needle\n", encoding="utf-8")
    ripgrep_config = tmp_path / "ripgrep.conf"
    ripgrep_config.write_text(
        "--pre=sh -c 'touch /tmp/rg-pre-mutated'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(terminal_module, "workspace", tmp_path)
    environment = dict(terminal_module.os.environ)

    configured_rg, configured_source = _terminal_receipt_plan(
        command="rg needle input.txt",
        potential_plan=_terminal_execution_plan("rg needle input.txt"),
        working_directory=tmp_path,
        environment={**environment, "RIPGREP_CONFIG_PATH": str(ripgrep_config)},
        environment_overrides=None,
    )
    no_config_rg, no_config_source = _terminal_receipt_plan(
        command="rg --no-config needle input.txt",
        potential_plan=_terminal_execution_plan("rg --no-config needle input.txt"),
        working_directory=tmp_path,
        environment={**environment, "RIPGREP_CONFIG_PATH": str(ripgrep_config)},
        environment_overrides=None,
    )
    git_plan, git_source = _terminal_receipt_plan(
        command="git --no-pager status --short",
        potential_plan=_terminal_execution_plan("git --no-pager status --short"),
        working_directory=tmp_path,
        environment=environment,
        environment_overrides=None,
    )

    assert configured_rg.effect == "unknown"
    assert configured_source == "untrusted_execution_context"
    assert no_config_rg.effect == "read_only"
    assert no_config_source == "trusted_command_contract"
    assert git_plan.effect == "unknown"
    assert git_source == "untrusted_execution_context"

    for command in (
        "rg -e --no-config f",
        "rg -g --no-config needle f",
        "rg --glob --no-config needle f",
    ):
        consumed_value_plan, consumed_value_source = _terminal_receipt_plan(
            command=command,
            potential_plan=_terminal_execution_plan(command),
            working_directory=tmp_path,
            environment={
                **environment,
                "RIPGREP_CONFIG_PATH": str(ripgrep_config),
            },
            environment_overrides=None,
        )
        assert consumed_value_plan.effect == "unknown"
        assert consumed_value_source == "untrusted_execution_context"


def test_callback_bypass_tokens_must_be_actual_top_level_options() -> None:
    rg_pathspec = _terminal_execution_plan("rg needle -- --no-config")
    rg_pattern_value = _terminal_execution_plan("rg -e --no-config f")
    rg_short_glob_value = _terminal_execution_plan("rg -g --no-config needle f")
    rg_long_glob_value = _terminal_execution_plan("rg --glob --no-config needle f")
    rg_real_flag = _terminal_execution_plan("rg --no-config needle -- --no-config")
    git_pathspec = _terminal_execution_plan("git status -- --version")
    git_version = _terminal_execution_plan("git --no-pager --version")
    mutating_config = _terminal_execution_plan(
        "git -c alias.status='!touch /tmp/pwned' status"
    )
    external_diff = _terminal_execution_plan("git diff --ext-diff")

    assert rg_pathspec.callback_kinds == ("ripgrep_config",)
    assert rg_pattern_value.callback_kinds == ("ripgrep_config",)
    assert rg_short_glob_value.callback_kinds == ("ripgrep_config",)
    assert rg_long_glob_value.callback_kinds == ("ripgrep_config",)
    assert rg_real_flag.callback_kinds == ()
    assert git_pathspec.callback_kinds == ("git_config",)
    assert git_version.callback_kinds == ()
    assert mutating_config.effect == "unknown"
    assert mutating_config.callback_kinds == ("git_config",)
    assert external_diff.effect == "unknown"
    assert external_diff.callback_kinds == ("git_config",)


def test_rg_no_config_consumed_by_any_value_option_is_not_a_flag() -> None:
    for option in _RG_VALUE_OPTIONS:
        assert "--no-config" not in _rg_actual_literal_flags(
            (option, "--no-config", "needle", "f")
        )

    assert "--no-config" in _rg_actual_literal_flags(("--no-config", "needle", "f"))


@pytest.mark.parametrize(
    "command",
    ("head -n -5 input.txt", "head -c +5 input.txt", "tail -n +5 input.txt"),
)
def test_relative_head_tail_counts_never_claim_contiguous_overlap(
    command: str,
) -> None:
    plan = _terminal_execution_plan(command)

    assert plan.effect == "read_only"
    assert plan.read_ranges[0].kind == "query"


@pytest.mark.parametrize(
    ("command", "reusable"),
    (
        ("cat input.txt", True),
        ("head -n 2 input.txt", True),
        ("sed -n 2,4p input.txt", True),
        ("cat input.txt | head -n 2", False),
        ("cat input.txt input.txt", False),
        ("cat input.txt >/dev/null", False),
        ("cat < input.txt", True),
        ("head -n 2 < input.txt", False),
        ("head -z -n 2 input.txt", False),
        ("tail --zero-terminated -n 2 input.txt", False),
        ("sed -n 2,4p input.txt input.txt", False),
        ("wc -l input.txt", False),
        ("cat -n input.txt", False),
    ),
)
def test_terminal_read_projection_reuse_requires_content_preserving_stdout(
    command: str,
    reusable: bool,
) -> None:
    plan = _terminal_execution_plan(command)

    assert plan.effect == "read_only"
    assert plan.read_projection_reusable is reusable


@pytest.mark.asyncio
async def test_run_code_emits_compact_terminal_execution_receipt() -> None:
    command = "printf alpha; printf beta | wc -c"

    response = await run_code(None, command, timeout=10)
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is True
    assert receipt == {
        "schema_version": "aworld.terminal-execution-receipt/v2",
        "parser_version": 10,
        "language_contract_version": 2,
        "command_sha256": terminal_command_sha256(command),
        "requested_language": "shell",
        "effective_language": "shell",
        "language": "shell",
        "parsed": True,
        "potential_effect": "read_only",
        "effect": "read_only",
        "effect_source": "trusted_command_contract",
        "cacheable": True,
        "read_paths": [],
        "read_ranges": [],
        "read_projection_reusable": False,
        "read_representation": "terminal.run-code.structured.exact/v1",
        "write_paths": [],
        "write_set_complete": True,
        "read_set_complete": True,
        "read_path_epochs": [],
        "workspace_generation_delta": 0,
        "mutation_observed": None,
        "scope_volatile": False,
        "executed": True,
        "cache_hit": False,
        "observation_id": None,
        "observation_content_sha256": None,
        "source_checkpoint_revision": None,
        "execution_context_sha256": (
            terminal_module._TERMINAL_EXECUTION_CONTEXT_SHA256
        ),
        "timed_out": False,
        "exit_code": 0,
    }
    assert command not in json.dumps(receipt)


@pytest.mark.asyncio
async def test_run_code_executes_explicit_raw_python_without_shell_inference() -> None:
    source = "values = [1, 2, 3]\nprint(sum(values))"

    response = await run_code(
        None,
        source,
        timeout=10,
        language="python",
    )
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is True
    assert payload["message"]["stdout"] == "6\n"
    assert receipt["requested_language"] == "python"
    assert receipt["effective_language"] == "python"
    assert receipt["language_contract_version"] == 2
    assert receipt["effect"] == "read_only"


@pytest.mark.asyncio
async def test_run_code_emits_authoritative_nested_python_heredoc_receipt(
    tmp_path: Path,
) -> None:
    command = """python3 -I - <<'PY'\nfrom pathlib import Path\nPath('result.txt').write_text('done')\nPY\n"""

    response = await run_code(None, command, timeout=10, cwd=str(tmp_path))
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is True
    assert receipt["effect"] == "mutating"
    assert receipt["write_paths"] == ["result.txt"]
    assert receipt["mutation_observed"] is True
    assert receipt["nested_language_evidence"][0]["language"] == "python"
    assert command not in json.dumps(receipt)
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "done"


@pytest.mark.asyncio
async def test_run_code_keeps_static_python_heredoc_read_authoritative(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(terminal_module, "workspace", tmp_path)
    (tmp_path / "input.txt").write_text("stable", encoding="utf-8")
    command = """python3 -I - <<'PY'\nfrom pathlib import Path\nprint(Path('input.txt').read_text())\nPY\n"""

    response = await run_code(None, command, timeout=10, cwd=str(tmp_path))
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is True
    assert receipt["effect"] == "read_only"
    assert receipt["effect_source"] == "trusted_command_contract"
    assert receipt["read_paths"] == ["input.txt"]
    assert receipt["nested_language_evidence"][0]["language"] == "python"


@pytest.mark.asyncio
async def test_leading_cd_cache_tracks_command_scoped_file_epoch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(terminal_module, "workspace", tmp_path)
    subdirectory = tmp_path / "sub"
    subdirectory.mkdir()
    (tmp_path / "result.txt").write_text("root", encoding="utf-8")
    nested = subdirectory / "result.txt"
    nested.write_text("nested-before", encoding="utf-8")
    command = "cd sub && cat result.txt"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "tool_call_id": "call-leading-cd",
        "params": {"code": command, "cwd": str(tmp_path)},
    }

    response = await run_code(None, command, timeout=10, cwd=str(tmp_path))
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["message"]["stdout"].strip() == "nested-before"
    assert receipt["effect"] == "read_only"
    assert receipt["cacheable"] is True
    assert receipt["read_path_epochs"][0]["path"] == str(nested)
    assert receipt["read_path_epochs"][0]["path"] != str(tmp_path / "result.txt")

    runtime = SandboxToolObservationRuntime()
    context = SimpleNamespace(
        task_id="task",
        task_epoch=1,
        session_id="session",
        agent_info=SimpleNamespace(current_agent_id="agent"),
        context_lifecycle_state=SimpleNamespace(
            session_id="session",
            session_epoch=0,
            task_epoch=1,
            branch_id="main",
            checkpoint_revision=0,
        ),
    )
    runtime.record(
        action,
        ActionResult(
            success=True,
            tool_call_id="call-leading-cd",
            content=payload["message"],
            parameter=action["params"],
            metadata=payload["metadata"],
        ),
        context=context,
    )
    assert runtime.lookup(action, context=context) is not None

    nested.write_text("nested-after", encoding="utf-8")

    assert runtime.lookup(action, context=context) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "env"),
    [
        ('cd "$TARGET" && cat result.txt', {"TARGET": "sub"}),
        ("cat result.txt; cd sub; cat result.txt", None),
    ],
)
async def test_dynamic_or_nonleading_cd_is_not_cacheable(
    tmp_path: Path,
    monkeypatch,
    command: str,
    env: dict[str, str] | None,
) -> None:
    monkeypatch.setattr(terminal_module, "workspace", tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "result.txt").write_text("root", encoding="utf-8")
    (tmp_path / "sub" / "result.txt").write_text("nested", encoding="utf-8")

    response = await run_code(
        None,
        command,
        timeout=10,
        cwd=str(tmp_path),
        env=env,
    )
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is True
    assert receipt["effect"] == "unknown"
    assert receipt["cacheable"] is False
    assert receipt["read_path_epochs"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "cd sub && (cd nested && cat result.txt)",
        "cd sub && { cd nested; cat result.txt; }",
        "cd sub && read_nested() { cd nested && cat result.txt; }; read_nested",
    ],
)
async def test_nested_group_cd_is_unknown_and_non_cacheable(
    tmp_path: Path,
    monkeypatch,
    command: str,
) -> None:
    monkeypatch.setattr(terminal_module, "workspace", tmp_path)
    nested = tmp_path / "sub" / "nested"
    nested.mkdir(parents=True)
    (tmp_path / "sub" / "result.txt").write_text("decoy", encoding="utf-8")
    (nested / "result.txt").write_text("nested", encoding="utf-8")

    response = await run_code(None, command, timeout=10, cwd=str(tmp_path))
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is True
    assert payload["message"]["stdout"].strip() == "nested"
    assert receipt["effect"] == "unknown"
    assert receipt["cacheable"] is False
    assert receipt["read_path_epochs"] == []


@pytest.mark.asyncio
async def test_nested_cd_never_replays_epoch_from_outer_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(terminal_module, "workspace", tmp_path)
    nested = tmp_path / "sub" / "nested"
    nested.mkdir(parents=True)
    decoy = tmp_path / "sub" / "result.txt"
    decoy.write_text("decoy-stable", encoding="utf-8")
    actual = nested / "result.txt"
    actual.write_text("nested-before", encoding="utf-8")
    command = "cd sub && (cd nested && cat result.txt)"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "tool_call_id": "call-nested-cd",
        "params": {"code": command, "cwd": str(tmp_path)},
    }
    runtime = SandboxToolObservationRuntime()
    context = SimpleNamespace(
        task_id="task",
        task_epoch=1,
        session_id="session",
        agent_info=SimpleNamespace(current_agent_id="agent"),
        context_lifecycle_state=SimpleNamespace(
            session_id="session",
            session_epoch=0,
            task_epoch=1,
            branch_id="main",
            checkpoint_revision=0,
        ),
    )

    first = await run_code(None, command, timeout=10, cwd=str(tmp_path))
    first_payload = json.loads(first.text)
    runtime.record(
        action,
        ActionResult(
            success=True,
            tool_call_id="call-nested-cd",
            content=first_payload["message"],
            parameter=action["params"],
            metadata=first_payload["metadata"],
        ),
        context=context,
    )
    assert first_payload["message"]["stdout"].strip() == "nested-before"
    assert runtime.lookup(action, context=context) is None

    actual.write_text("nested-after", encoding="utf-8")
    second = await run_code(None, command, timeout=10, cwd=str(tmp_path))
    second_payload = json.loads(second.text)

    assert decoy.read_text(encoding="utf-8") == "decoy-stable"
    assert second_payload["message"]["stdout"].strip() == "nested-after"
    assert runtime.lookup(action, context=context) is None


@pytest.mark.asyncio
async def test_negated_nested_cd_is_unknown_before_cache_planning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(terminal_module, "workspace", tmp_path)
    nested = tmp_path / "sub" / "nested"
    nested.mkdir(parents=True)
    (tmp_path / "sub" / "result.txt").write_text("decoy", encoding="utf-8")
    (nested / "result.txt").write_text("nested", encoding="utf-8")
    command = "cd sub && ! cd nested; cat result.txt"

    plan = _terminal_execution_plan(command)
    response = await run_code(None, command, timeout=10, cwd=str(tmp_path))
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert plan.effect == "unknown"
    assert plan.cacheable is False
    assert plan.command_cwd_safe is False
    assert payload["message"]["stdout"].strip() == "nested"
    assert receipt["effect"] == "unknown"
    assert receipt["cacheable"] is False
    assert receipt["read_path_epochs"] == []


@pytest.mark.asyncio
async def test_run_code_keeps_shell_default_for_bare_python() -> None:
    source = "import json\nprint(json.dumps({'ok': True}))"

    response = await run_code(None, source, timeout=10)
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is False
    assert receipt["requested_language"] == "shell"
    assert receipt["effective_language"] == "shell"
    assert receipt["effect"] == "unknown"


@pytest.mark.asyncio
async def test_run_code_explicit_python_reports_literal_file_mutation(
    tmp_path: Path,
) -> None:
    response = await run_code(
        None,
        "from pathlib import Path\nPath('result.txt').write_text('changed')",
        timeout=10,
        cwd=str(tmp_path),
        language="python",
    )
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is True
    assert receipt["effective_language"] == "python"
    assert receipt["effect"] == "mutating"
    assert receipt["write_paths"] == ["result.txt"]
    assert receipt["mutation_observed"] is True
    assert (tmp_path / "result.txt").read_text() == "changed"


@pytest.mark.asyncio
async def test_run_code_explicit_python_emits_stable_file_read_epochs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("evidence", encoding="utf-8")
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.terminal.src.terminal.workspace",
        tmp_path,
    )

    response = await run_code(
        None,
        "from pathlib import Path\nprint(Path('input.txt').read_text())",
        timeout=10,
        cwd=str(tmp_path),
        language="python",
    )
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is True
    assert payload["message"]["stdout"] == "evidence\n"
    assert receipt["effect"] == "read_only"
    assert receipt["effect_source"] == "trusted_command_contract"
    assert receipt["read_paths"] == ["input.txt"]
    assert len(receipt["read_path_epochs"]) == 1
    assert receipt["cacheable"] is True


@pytest.mark.asyncio
async def test_rejected_command_receipt_says_execution_did_not_start() -> None:
    command = "rm -rf /"

    response = await run_code(None, command, timeout=10)
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is False
    assert payload["metadata"]["safety_check_passed"] is False
    assert receipt["effect"] == "mutating"
    assert receipt["executed"] is False
    assert receipt["workspace_generation_delta"] == 0
    assert receipt["exit_code"] is None


@pytest.mark.asyncio
async def test_run_code_reports_observed_change_for_literal_write_target(
    tmp_path: Path,
) -> None:
    response = await run_code(
        None,
        "printf changed > result.txt",
        timeout=10,
        cwd=str(tmp_path),
    )
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is True
    assert receipt["effect"] == "mutating"
    assert receipt["write_paths"] == ["result.txt"]
    assert receipt["workspace_generation_delta"] == 1
    assert receipt["mutation_observed"] is True
    assert (tmp_path / "result.txt").read_text() == "changed"


@pytest.mark.asyncio
async def test_run_code_observes_known_write_subset_for_unknown_effect(
    tmp_path: Path,
) -> None:
    command = """python3 - <<'PY'
import urllib.request
with open('analysis.txt', 'w', encoding='utf-8') as handle:
    handle.write('new intermediate evidence')
PY
"""

    response = await run_code(None, command, timeout=10, cwd=str(tmp_path))
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is True
    assert receipt["effect"] == "unknown"
    assert receipt["write_paths"] == ["analysis.txt"]
    assert receipt["write_set_complete"] is False
    assert receipt["mutation_observed"] is True
    assert (tmp_path / "analysis.txt").read_text() == "new intermediate evidence"


@pytest.mark.asyncio
async def test_overlapping_known_write_snapshots_have_causal_attribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.txt"

    async def execute(command, *_args, **_kwargs):
        if command.startswith("printf first"):
            await asyncio.sleep(0.05)
            output.write_text("first", encoding="utf-8")
        else:
            # Without a path lease this call snapshots the missing file before
            # the first call writes it, then incorrectly claims that change.
            await asyncio.sleep(0.1)
        return _result()

    monkeypatch.setattr(terminal_module, "_execute_command_async", execute)
    first, second = await asyncio.gather(
        run_code(None, "printf first > result.txt", cwd=str(tmp_path), timeout=10),
        run_code(None, "printf second > result.txt", cwd=str(tmp_path), timeout=10),
    )
    first_receipt = json.loads(first.text)["metadata"]["terminal_execution_receipt"]
    second_receipt = json.loads(second.text)["metadata"]["terminal_execution_receipt"]

    assert first_receipt["mutation_observed"] is True
    assert second_receipt["mutation_observed"] is False


@pytest.mark.asyncio
async def test_disjoint_known_write_paths_execute_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    maximum_active = 0
    both_started = asyncio.Event()

    async def execute(command, *_args, **_kwargs):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        if active == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1)
        target = "first.txt" if "first" in command else "second.txt"
        (tmp_path / target).write_text(target, encoding="utf-8")
        active -= 1
        return _result()

    monkeypatch.setattr(terminal_module, "_execute_command_async", execute)
    await asyncio.gather(
        run_code(None, "printf first > first.txt", cwd=str(tmp_path), timeout=10),
        run_code(None, "printf second > second.txt", cwd=str(tmp_path), timeout=10),
    )

    assert maximum_active == 2


@pytest.mark.asyncio
async def test_run_code_does_not_cache_reads_outside_workspace(
    tmp_path: Path,
) -> None:
    response = await run_code(
        None,
        "cat /etc/hosts",
        timeout=10,
        cwd=str(tmp_path),
    )
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is True
    assert receipt["potential_effect"] == "read_only"
    assert receipt["effect"] == "unknown"
    assert receipt["effect_source"] == "untrusted_execution_context"
    assert receipt["cacheable"] is False
    assert receipt["workspace_generation_delta"] == 1


@pytest.mark.asyncio
async def test_run_code_does_not_trust_per_call_path_override(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("value", encoding="utf-8")
    response = await run_code(
        None,
        "cat input.txt",
        timeout=10,
        cwd=str(tmp_path),
        env={"PATH": "/bin:/usr/bin"},
    )
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is True
    assert receipt["potential_effect"] == "read_only"
    assert receipt["effect"] == "unknown"
    assert receipt["cacheable"] is False


@pytest.mark.asyncio
async def test_run_code_does_not_cache_when_input_changes_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("before", encoding="utf-8")
    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.terminal.src.terminal.workspace",
        tmp_path,
    )

    async def changing_read(*_args, **_kwargs):
        source.write_text("after", encoding="utf-8")
        return _result(stdout="before")

    monkeypatch.setattr(
        "aworld.sandbox.tool_servers.terminal.src.terminal._execute_command_async",
        changing_read,
    )

    response = await run_code(
        None,
        "cat input.txt",
        timeout=10,
        cwd=str(tmp_path),
    )
    receipt = json.loads(response.text)["metadata"]["terminal_execution_receipt"]

    assert receipt["effect"] == "read_only"
    assert receipt["read_paths"] == ["input.txt"]
    assert receipt["read_path_epochs"] == []
    assert receipt["cacheable"] is False


@pytest.mark.asyncio
async def test_run_code_anchors_explicit_cwd_to_configured_workspace() -> None:
    response = await run_code(
        None,
        "cat etc/hosts",
        timeout=10,
        cwd="/",
    )
    payload = json.loads(response.text)
    receipt = payload["metadata"]["terminal_execution_receipt"]

    assert payload["success"] is True
    assert receipt["potential_effect"] == "read_only"
    assert receipt["effect"] == "unknown"
    assert receipt["cacheable"] is False


def test_bounded_inline_stream_preserves_head_and_tail() -> None:
    value = "a" * 100 + "z" * 100

    bounded = _bounded_inline_stream(value, max_chars=40)

    assert bounded.startswith("a" * 20)
    assert bounded.endswith("z" * 20)
    assert "160 chars omitted" in bounded


def test_format_command_output_bounds_each_stream() -> None:
    formatted = _format_command_output(
        _result(stdout="x" * 20_000, stderr="y" * 20_000),
        output_format="json",
    )

    assert formatted.count("terminal output truncated") == 2
    assert len(formatted) < 34_000


def test_capture_limit_is_configurable_but_clamped_to_hard_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_CAPTURE_MAX_BYTES", "999999999999")

    assert _get_total_capture_limit_bytes() == _HARD_MAX_TOTAL_CAPTURE_BYTES


@pytest.mark.parametrize(
    "command",
    (
        "rm -rf /app/build",
        "sudo rm -rf /tmp/aworld-build",
        "printf '%s' 'rm -rf /'",
        "dd if=/dev/zero of=fixture.bin bs=1 count=4",
    ),
)
def test_safety_policy_allows_scoped_cleanup_and_non_device_dd(command: str) -> None:
    assert _check_command_safety(command) == (True, None)


@pytest.mark.parametrize(
    "command",
    (
        "rm -rf /",
        "rm -rf -- /*",
        "sudo rm -r $HOME",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda bs=1M",
    ),
)
def test_safety_policy_blocks_broad_or_device_destructive_commands(
    command: str,
) -> None:
    allowed, reason = _check_command_safety(command)

    assert allowed is False
    assert reason


def test_command_timeout_is_clamped_to_trial_deadline_with_completion_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", "1120")
    monkeypatch.setenv("AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS", "30")

    decision = _resolve_command_timeout(300, now_epoch=1000)

    assert decision.requested_seconds == 300
    assert decision.effective_seconds == 90
    assert decision.remaining_task_seconds == 120
    assert decision.limited_by == "task_deadline"


def test_command_timeout_lease_preserves_finalization_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", "4600")
    monkeypatch.setenv("AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS", "60")

    decision = _resolve_command_timeout(3600, now_epoch=1000)

    assert decision.requested_seconds == 3600
    assert decision.remaining_task_seconds == 3600
    assert decision.effective_seconds == 3540
    assert decision.limited_by == "task_deadline"


def test_command_timeout_lease_never_lengthens_a_short_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", "4600")
    monkeypatch.setenv("AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS", "60")

    decision = _resolve_command_timeout(20, now_epoch=1000)

    assert decision.effective_seconds == 20
    assert decision.limited_by is None


def test_framework_task_budget_precedes_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", "9999")
    monkeypatch.setenv("AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS", "1")

    decision = _resolve_command_timeout(
        300,
        now_epoch=1000,
        task_deadline_epoch_seconds=1120,
        completion_reserve_seconds=30,
        task_budget_stage="convergence",
    )

    assert decision.remaining_task_seconds == 120
    assert decision.effective_seconds == 22.5
    assert decision.limited_by == "task_lease"


def test_normal_execution_can_use_all_time_remaining_after_reserve() -> None:
    decision = _resolve_command_timeout(
        300,
        now_epoch=1000,
        task_deadline_epoch_seconds=1120,
        completion_reserve_seconds=30,
        task_budget_stage="execute",
    )

    assert decision.effective_seconds == 90
    assert decision.limited_by == "task_deadline"


@pytest.mark.parametrize(
    "stage",
    [
        "convergence",
        "deadline",
        "candidate_due",
        "validation_due",
        "delivery_only",
    ],
)
def test_typed_convergence_stages_apply_fractional_tool_lease(stage) -> None:
    decision = _resolve_command_timeout(
        300,
        now_epoch=1000,
        task_deadline_epoch_seconds=1120,
        completion_reserve_seconds=20,
        task_budget_stage=stage,
    )

    assert decision.effective_seconds == 25
    assert decision.limited_by == "task_lease"


def test_valid_explicit_fraction_is_an_opt_in_deployment_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_TASK_LEASE_FRACTION", "0.5")

    decision = _resolve_command_timeout(
        300,
        now_epoch=1000,
        task_deadline_epoch_seconds=1120,
        completion_reserve_seconds=20,
        task_budget_stage="execute",
    )

    assert decision.effective_seconds == 50
    assert decision.limited_by == "task_lease"


def test_invalid_fraction_does_not_enable_pre_convergence_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_TASK_LEASE_FRACTION", "invalid")

    decision = _resolve_command_timeout(
        300,
        now_epoch=1000,
        task_deadline_epoch_seconds=1120,
        completion_reserve_seconds=20,
        task_budget_stage="execute",
    )

    assert decision.effective_seconds == 100
    assert decision.limited_by == "task_deadline"


def test_no_budget_sentinel_does_not_fall_back_to_spoofable_process_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", "1001")

    decision = _resolve_command_timeout(
        300,
        now_epoch=1000,
        task_budget={
            "authority": "aworld_task",
            "schema_version": "aworld.task-budget/v1",
            "bounded": False,
            "stage": "execute",
        },
    )

    assert decision.remaining_task_seconds is None
    assert decision.effective_seconds == 300


def test_budget_snapshot_never_extends_after_wall_clock_rollback() -> None:
    budget = FrameworkTaskBudget(
        bounded=True,
        deadline_epoch_seconds=1040,
        remaining_seconds=40,
        completion_reserve_seconds=0,
        captured_at_epoch_seconds=1000,
    )

    assert budget.remaining_at(1010) == 30
    assert budget.remaining_at(900) == 40


def test_unspecified_task_reserve_honors_explicit_terminal_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS", "60")
    budget = FrameworkTaskBudget(
        bounded=True,
        deadline_epoch_seconds=1120,
        remaining_seconds=120,
        completion_reserve_seconds=None,
        captured_at_epoch_seconds=1000,
    )

    decision = _resolve_command_timeout(
        300,
        now_epoch=1000,
        task_budget=budget.to_hidden_dict(),
    )

    assert decision.effective_seconds == 60
    assert decision.limited_by == "task_deadline"


def test_unspecified_task_reserve_preserves_default_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS", raising=False)
    budget = FrameworkTaskBudget(
        bounded=True,
        deadline_epoch_seconds=1120,
        remaining_seconds=120,
        completion_reserve_seconds=None,
        captured_at_epoch_seconds=1000,
    )

    decision = _resolve_command_timeout(
        300,
        now_epoch=1000,
        task_budget=budget.to_hidden_dict(),
    )

    assert decision.effective_seconds == 105
    assert decision.limited_by == "task_deadline"


def test_terminal_timeout_override_preserves_original_caller_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERMINAL_TIMEOUT", "120")

    decision = _resolve_command_timeout(300)

    assert decision.requested_seconds == 300
    assert decision.policy_seconds == 120
    assert decision.policy_override == "terminal_timeout"
    assert decision.effective_seconds == 120
    assert decision.limited_by == "terminal_timeout"


@pytest.mark.asyncio
async def test_run_code_receipt_separates_caller_timeout_from_policy_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERMINAL_TIMEOUT", "2")

    response = await run_code(None, "true", timeout=300, output_format="text")
    payload = json.loads(response.text)

    assert payload["success"] is True
    assert payload["metadata"]["requested_timeout_seconds"] == 300
    assert payload["metadata"]["timeout_policy_seconds"] == 2
    assert payload["metadata"]["timeout_policy_override"] == "terminal_timeout"
    assert payload["metadata"]["timeout_seconds"] == 2
    assert payload["metadata"]["timeout_limited_by"] == "terminal_timeout"


def test_command_timeout_reports_exhausted_completion_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TASK_DEADLINE_EPOCH_SECONDS", "1020")
    monkeypatch.setenv("AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS", "30")

    decision = _resolve_command_timeout(60, now_epoch=1000)

    assert decision.effective_seconds == 0
    assert decision.limited_by == "task_deadline_exhausted"


def test_stream_capture_retention_stays_bounded_for_large_output() -> None:
    capture = _BoundedStreamCapture("stdout", 4_096)
    capture.feed(b"HEAD")
    chunk = b"x" * (64 * 1024)
    for _ in range(512):
        capture.feed(chunk)
        assert capture.retained_bytes <= 4_096
    capture.feed(b"TAIL")

    rendered = capture.render()

    assert capture.total_bytes > 32 * 1024 * 1024
    assert capture.retained_bytes == 4_096
    assert len(rendered.encode()) < 5_000
    assert rendered.startswith("HEAD")
    assert rendered.endswith("TAIL")
    assert "terminal stdout truncated" in rendered
    assert "complete stream was drained without retention" in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "background_suffix",
    ["", " # nohup marker"],
    ids=["foreground", "background-classified"],
)
async def test_execute_large_stdout_and_stderr_uses_bounded_head_tail_capture(
    monkeypatch: pytest.MonkeyPatch,
    background_suffix: str,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_CAPTURE_MAX_BYTES", "4096")
    child_code = (
        "import sys; "
        "sys.stdout.write('STDOUT_HEAD' + ('x' * 2000000) + 'STDOUT_TAIL'); "
        "sys.stderr.write('STDERR_HEAD' + ('y' * 2000000) + 'STDERR_TAIL')"
    )
    command = (
        f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)}{background_suffix}"
    )

    result = await _execute_command_async(command, timeout=10)

    assert result.success is True
    assert result.output_truncated is True
    assert result.capture_limit_bytes == 4_096
    assert result.stdout_total_bytes == 2_000_022
    assert result.stderr_total_bytes == 2_000_022
    assert len(result.stdout.encode()) < 3_000
    assert len(result.stderr.encode()) < 3_000
    assert result.stdout.startswith("STDOUT_HEAD")
    assert result.stdout.endswith("STDOUT_TAIL")
    assert result.stderr.startswith("STDERR_HEAD")
    assert result.stderr.endswith("STDERR_TAIL")
    assert "terminal stdout truncated" in result.stdout
    assert "terminal stderr truncated" in result.stderr


@pytest.mark.asyncio
async def test_run_code_surfaces_truncation_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_CAPTURE_MAX_BYTES", "4096")
    child_code = "import sys; sys.stdout.write('HEAD' + ('x' * 100000) + 'TAIL')"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)}"

    response = await run_code(None, command, timeout=10, output_format="text")
    payload = json.loads(response.text)

    assert payload["success"] is True
    assert payload["metadata"]["output_truncated"] is True
    assert payload["metadata"]["stdout_total_bytes"] == 100_008
    assert payload["metadata"]["stdout_omitted_bytes"] > 0
    assert payload["metadata"]["capture_limit_bytes"] == 4_096
    assert payload["metadata"]["capture_strategy"] == "bounded_head_tail_drain"
    assert payload["metadata"]["output_data"] is None
    assert "Output Capture: TRUNCATED" in payload["message"]
    assert response.model_extra["metadata"] == {}


@pytest.mark.asyncio
async def test_run_code_serializes_command_output_once_without_artifact_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unique_output = "terminal-output-copy-canary-592821"
    monkeypatch.setenv("AWORLD_TERMINAL_TEST_CANARY", unique_output)
    child_code = (
        "import os; print(os.environ['AWORLD_TERMINAL_TEST_CANARY'], flush=True)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)}"

    response = await run_code(None, command, timeout=10, output_format="text")
    payload = json.loads(response.text)

    assert response.text.count(unique_output) == 1
    assert payload["metadata"]["output_data"] is None
    assert response.model_extra["metadata"] == {}


@pytest.mark.asyncio
async def test_run_code_supports_explicit_cwd_env_and_compact_structured_output(
    tmp_path: Path,
) -> None:
    child_code = (
        "import os, pathlib; "
        "print(pathlib.Path.cwd()); print(os.environ['AWORLD_TEST_SCOPE'])"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)}"

    response = await run_code(
        None,
        command,
        timeout=10,
        cwd=str(tmp_path),
        env={"AWORLD_TEST_SCOPE": "scoped-value"},
    )
    payload = json.loads(response.text)

    assert payload["success"] is True
    assert payload["message"] == {
        "stdout": f"{tmp_path.resolve()}\nscoped-value\n",
        "stderr": "",
    }
    assert payload["metadata"]["working_directory"] == str(tmp_path.resolve())
    assert payload["metadata"]["environment_keys"] == ["AWORLD_TEST_SCOPE"]
    assert "scoped-value" not in json.dumps(payload["metadata"])


@pytest.mark.asyncio
async def test_run_code_injects_authoritative_task_scope_per_call() -> None:
    child_code = (
        "import os; print(os.environ['AWORLD_TASK_ID']); "
        "print(os.environ['AWORLD_SESSION_ID']); "
        "print(os.environ['AWORLD_TASK_EPOCH'])"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)}"

    response = await run_code(
        None,
        command,
        timeout=10,
        env={"AWORLD_TASK_ID": "spoofed"},
        env_content={
            "task_id": "task-real",
            "session_id": "session-real",
            "task_epoch": 3,
        },
    )
    payload = json.loads(response.text)

    assert payload["success"] is True
    assert payload["message"]["stdout"] == "task-real\nsession-real\n3\n"
    assert set(payload["metadata"]["environment_keys"]) == {
        "AWORLD_TASK_EPOCH",
        "AWORLD_TASK_ID",
        "AWORLD_SESSION_ID",
    }


@pytest.mark.asyncio
async def test_truncated_output_is_retrievable_from_checksummed_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_CAPTURE_MAX_BYTES", "4096")
    monkeypatch.setenv("AWORLD_TERMINAL_ARTIFACT_MAX_BYTES", "200000")
    monkeypatch.setenv("AWORLD_TERMINAL_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    original = b"HEAD" + (b"x" * 100_000) + b"TAIL"
    child_code = "import sys; sys.stdout.buffer.write(" + repr(original) + ")"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)}"

    response = await run_code(None, command, timeout=10)
    payload = json.loads(response.text)
    policy = payload["metadata"]["output_policy"]["stdout"]

    assert policy["artifact_complete"] is True
    assert policy["raw_bytes"] == len(original)
    assert policy["stream_total_bytes"] == len(original)
    assert policy["artifact_ref"].startswith("aworld-terminal-output://sha256/")
    artifact_response = await read_output_artifact(
        None,
        policy["artifact_ref"],
        offset=0,
        limit=len(original),
        output="base64",
    )
    artifact_payload = json.loads(artifact_response.text)

    assert base64.b64decode(artifact_payload["content"]) == original
    assert artifact_payload["complete"] is True
    assert artifact_payload["content_sha256"] == policy["content_sha256"]


@pytest.mark.asyncio
async def test_output_artifact_has_explicit_finite_hard_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_CAPTURE_MAX_BYTES", "2048")
    monkeypatch.setenv("AWORLD_TERMINAL_ARTIFACT_MAX_BYTES", "8192")
    monkeypatch.setenv("AWORLD_TERMINAL_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    child_code = "import sys; sys.stdout.write('x' * 20000)"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)}"

    response = await run_code(None, command, timeout=10)
    payload = json.loads(response.text)
    policy = payload["metadata"]["output_policy"]["stdout"]

    assert policy["artifact_complete"] is False
    assert policy["raw_bytes"] == 8192
    assert policy["stream_total_bytes"] == 20000
    assert policy["artifact_ref"]


@pytest.mark.asyncio
async def test_output_artifact_reader_rejects_content_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWORLD_TERMINAL_CAPTURE_MAX_BYTES", "2048")
    artifact_dir = tmp_path / "artifacts"
    monkeypatch.setenv("AWORLD_TERMINAL_ARTIFACT_DIR", str(artifact_dir))
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        "print('x' * 10000, end='')"
    )
    response = await run_code(None, command, timeout=10)
    payload = json.loads(response.text)
    policy = payload["metadata"]["output_policy"]["stdout"]
    artifact_path = artifact_dir / f"{policy['content_sha256']}.bin"
    artifact_path.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="does not match"):
        await read_output_artifact(None, policy["artifact_ref"])


@pytest.mark.asyncio
async def test_timeout_kills_process_group_reaps_and_preserves_partial_output(
    tmp_path: Path,
) -> None:
    escaped_child_marker = tmp_path / "escaped-child.txt"
    grandchild_code = (
        "import pathlib, time; time.sleep(0.8); "
        f"pathlib.Path({str(escaped_child_marker)!r}).write_text('survived')"
    )
    child_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild_code!r}]); "
        "print('started', flush=True); time.sleep(60)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)}"
    started_at = time.monotonic()

    result = await _execute_command_async(command, timeout=0.2)

    assert time.monotonic() - started_at < 4
    assert result.success is False
    assert result.timed_out is True
    assert result.return_code == -1
    assert result.capture_complete is True
    assert result.stdout == "started\n"
    assert "timed out after 0.2 seconds" in result.stderr

    await asyncio.sleep(1)
    assert not escaped_child_marker.exists()


@pytest.mark.asyncio
async def test_background_inherited_pipes_switch_to_detached_drain() -> None:
    grandchild_code = (
        "import sys, time; print('early', flush=True); "
        "time.sleep(0.4); print('late', flush=True)"
    )
    child_code = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild_code!r}])"
    )
    # The marker selects the existing long-running/background compatibility
    # path while the child process creates the inherited-pipe condition.
    command = (
        f"{shlex.quote(sys.executable)} -c {shlex.quote(child_code)} # nohup marker"
    )
    started_at = time.monotonic()

    result = await _execute_command_async(command, timeout=5)

    assert time.monotonic() - started_at < 1
    assert result.success is True
    assert result.capture_complete is False
    assert result.background_output_detached is True
    assert _background_drain_tasks

    await asyncio.sleep(0.6)
    assert not _background_drain_tasks


@pytest.mark.asyncio
async def test_background_operator_without_spaces_returns_promptly() -> None:
    started_at = time.monotonic()

    result = await _execute_command_async("sleep 0.5&", timeout=0.2)

    assert time.monotonic() - started_at < 0.4
    assert result.success is True
    assert result.timed_out is False
    assert result.background_output_detached is True


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
async def test_public_contract_execution_kills_redirected_background_writer(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "late-write.txt"
    command = (
        f"(sleep 0.4; printf late > {shlex.quote(str(marker))}) "
        ">/dev/null 2>&1 &"
    )

    result = await _execute_command_async(
        command,
        timeout=2,
        cwd=tmp_path,
        quiesce_process_group=True,
    )

    assert result.background_process_requested is True
    assert result.process_group_quiesced is True
    await asyncio.sleep(0.6)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_process_group_permission_error_never_confirms_quiescence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CompletedProcess:
        pid = 424242
        returncode = 0

        async def wait(self):
            return 0

    monkeypatch.setitem(terminal_module.platform_info, "system", "Linux")

    def deny_killpg(*_args):
        raise PermissionError("not authoritative")

    monkeypatch.setattr(terminal_module.os, "killpg", deny_killpg)

    assert await terminal_module._terminate_process(CompletedProcess()) is False


@pytest.mark.parametrize(
    ("command", "expected"),
    (
        ("sleep 1&", True),
        ("sleep 1 & echo ready", True),
        ("echo '&'", False),
        (r"echo \&", False),
        ("echo ready && echo done", False),
        ("echo ready 2>&1", False),
        ("echo ready &>output.log", False),
    ),
)
def test_background_operator_is_shell_aware(command: str, expected: bool) -> None:
    assert _has_background_operator(command) is expected
