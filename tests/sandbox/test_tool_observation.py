from pathlib import Path
from types import SimpleNamespace

from aworld.core.common import ActionResult
from aworld.sandbox.tool_observation import (
    SandboxToolObservationRuntime,
    actions_are_provably_read_only,
    canonical_tool_identity,
    classify_tool_effect,
)
from aworld.sandbox.terminal_receipt import (
    TERMINAL_EXECUTION_RECEIPT_KEY,
    TerminalExecutionPlan,
    build_terminal_execution_receipt,
    plan_terminal_execution,
)


def _context():
    return SimpleNamespace(task_id="task", task_epoch=1, session_id="session")


def _file_epoch(path: Path) -> dict[str, object]:
    absolute = path.absolute()
    link_stat = absolute.lstat()
    resolved = absolute.resolve()
    target_stat = resolved.stat()
    return {
        "path": str(absolute),
        "resolved_path": str(resolved),
        "link_inode": link_stat.st_ino,
        "link_mtime_ns": link_stat.st_mtime_ns,
        "mode": target_stat.st_mode,
        "size": target_stat.st_size,
        "mtime_ns": target_stat.st_mtime_ns,
        "ctime_ns": target_stat.st_ctime_ns,
        "inode": target_stat.st_ino,
    }


def test_internal_mcp_dispatcher_has_one_canonical_capability_identity() -> None:
    action = {
        "tool_name": "mcp",
        "action_name": "terminal__run_code",
        "params": {"code": "pwd"},
    }

    assert canonical_tool_identity(action) == ("terminal", "run_code")
    effect = classify_tool_effect(action)
    assert effect.identity == "terminal.run_code"
    assert effect.effect == "read_only"


def test_docker_provider_capabilities_share_sandbox_effect_semantics() -> None:
    read_action = {
        "tool_name": "docker",
        "action_name": "read_file",
        "params": {"path": "/app/a.txt"},
    }
    write_action = {
        "tool_name": "docker",
        "action_name": "write_file",
        "params": {"path": "/app/a.txt", "content": "updated"},
    }
    terminal_read = {
        "tool_name": "docker",
        "action_name": "run_code",
        "params": {"code": "cat /app/a.txt"},
    }

    assert classify_tool_effect(read_action).effect == "read_only"
    assert classify_tool_effect(read_action).cacheable is False
    assert classify_tool_effect(write_action).effect == "mutating"
    assert classify_tool_effect(terminal_read).effect == "read_only"
    assert actions_are_provably_read_only([read_action, terminal_read]) is True
    assert actions_are_provably_read_only([write_action]) is False


def test_operation_hash_includes_redacted_env_content_fingerprint() -> None:
    def effect(secret: str):
        return classify_tool_effect(
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {
                    "code": "pwd",
                    "env_content": {"credential": secret, "task_epoch": 1},
                },
            }
        )

    first = effect("alpha-secret")
    repeated = effect("alpha-secret")
    changed = effect("beta-secret")

    assert first.operation_hash == repeated.operation_hash
    assert first.operation_hash != changed.operation_hash
    assert "alpha-secret" not in first.operation_hash


def test_operation_hash_separates_shell_and_python_language_contracts() -> None:
    shell = classify_tool_effect(
        {
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "print(1)"},
        }
    )
    python = classify_tool_effect(
        {
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "print(1)", "language": "python"},
        }
    )
    explicit_shell = classify_tool_effect(
        {
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "print(1)", "language": "shell"},
        }
    )

    assert shell.effect == "unknown"
    assert python.effect == "read_only"
    assert shell.operation_hash == explicit_shell.operation_hash
    assert shell.operation_hash != python.operation_hash


def test_shell_classifier_accepts_static_read_only_composition() -> None:
    for code in (
        "cat /app/a.py | head",
        "cat /app/a.py; wc -l /app/a.py",
        "cat /app/a.py\nwc -l /app/a.py",
    ):
        effect = classify_tool_effect(
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {"code": code},
            }
        )
        assert effect.effect == "read_only"
        assert effect.cacheable is False


def test_shell_classifier_fails_open_for_dynamic_or_ambiguous_code() -> None:
    for code in (
        "cat $TARGET",
        "if depth > 3:\n    print(depth)",
    ):
        effect = classify_tool_effect(
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {"code": code},
            }
        )
        assert effect.effect == "unknown"
        assert effect.cacheable is False


def test_shell_classifier_accepts_provably_read_only_inline_python() -> None:
    for code in (
        "python -c 'print(1)'",
        "cd /app && python3 -c \"from pathlib import Path; print(Path('a').read_text())\"",
        "cd /app && python -c \"\nimport cv2, numpy as np\ncap = cv2.VideoCapture('example.mp4')\nprint(np.array([cap.get(1)]).max())\n\"",
        "cd /app && python -c \"print(1 > 0)\" 2>&1 | head -20",
        "cd /app && python -c \"print(1)\" 2>/dev/null",
    ):
        effect = classify_tool_effect(
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {"code": code},
            }
        )
        assert effect.effect == "read_only"
        assert effect.cacheable is False


def test_shell_classifier_recognizes_inline_python_file_mutation() -> None:
    for code in (
        "python -c \"open('/app/a.py', 'w').write('x')\"",
        "python -c \"from pathlib import Path; Path('/app/a.py').write_text('x')\"",
        "python -c \"import os; os.remove('/app/a.py')\"",
        "python -c \"import numpy as np; np.save('/app/a.npy', np.array([1]))\"",
    ):
        effect = classify_tool_effect(
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {"code": code},
            }
        )
        assert effect.effect == "mutating"
        assert effect.cacheable is False


def test_shell_classifier_types_quoted_python_heredoc_read_and_write() -> None:
    read_source = """python3 <<'PY'\nfrom pathlib import Path\nprint(Path('/app/input.txt').read_text())\nPY\n"""
    write_source = """python <<'PY'\nfrom pathlib import Path\nPath('/app/result.txt').write_text('done')\nPY\n"""

    read_plan = plan_terminal_execution(read_source)
    write_plan = plan_terminal_execution(write_source)

    assert read_plan.effect == "read_only"
    assert read_plan.read_paths == ("/app/input.txt",)
    assert read_plan.nested_languages == ("python",)
    assert write_plan.effect == "mutating"
    assert write_plan.write_paths == ("/app/result.txt",)
    assert write_plan.nested_languages == ("python",)

    receipt = build_terminal_execution_receipt(
        code=write_source,
        plan=write_plan,
        executed=True,
        exit_code=0,
        timed_out=False,
    )
    assert receipt["nested_language_evidence"] == [
        {
            "language": "python",
            "source_sha256": write_plan.nested_source_sha256[0],
        }
    ]
    assert write_source not in str(receipt)


def test_shell_classifier_keeps_dynamic_python_heredoc_unknown() -> None:
    plan = plan_terminal_execution(
        """python <<PY\nfrom pathlib import Path\nprint(Path('$TARGET').read_text())\nPY\n"""
    )

    assert plan.effect == "unknown"
    assert plan.read_paths == ()
    assert plan.nested_languages == ("python",)


def test_shell_classifier_does_not_treat_fd_redirection_as_file_mutation() -> None:
    effect = classify_tool_effect(
        {
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "python inspect.py 2>&1 | head -20"},
        }
    )

    assert effect.effect == "unknown"
    assert effect.cacheable is False


def test_known_mutation_advances_generation_but_unknown_does_not_claim_progress() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    mutation = {
        "tool_name": "filesystem",
        "action_name": "write_file",
        "params": {"path": "target", "content": "changed"},
    }
    unknown = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": "python build.py"},
    }

    mutated = runtime.record(
        mutation,
        ActionResult(success=True, content="ok"),
        context=context,
    )
    uncertain = runtime.record(
        unknown,
        ActionResult(success=True, content="ok"),
        context=context,
    )

    assert mutated.metadata["sandbox_observation"]["workspace_mutated"] is True
    assert mutated.metadata["sandbox_observation"]["workspace_generation"] == 1
    assert uncertain.metadata["sandbox_observation"]["effect"] == "unknown"
    assert uncertain.metadata["sandbox_observation"]["workspace_mutated"] is None
    assert uncertain.metadata["sandbox_observation"]["workspace_generation"] == 2


def test_authoritative_terminal_receipt_overrides_raw_code_guess_and_seeds_cache() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    code = "if depth > 3:\n    print(depth)"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code, "language": "python"},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=TerminalExecutionPlan("python", "read_only", True, True),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
    )

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="4\n",
            parameter={"code": code, "language": "python"},
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )
    repeated = runtime.lookup(action, context=context)

    sandbox_receipt = observed.metadata["sandbox_observation"]
    assert sandbox_receipt["effect"] == "read_only"
    assert sandbox_receipt["workspace_mutated"] is False
    assert sandbox_receipt["workspace_generation"] == 0
    assert repeated is not None
    assert repeated.metadata["sandbox_observation"]["cache_hit"] is True


def test_explicit_file_epoch_revalidates_cache_across_unknown_generation(
    tmp_path: Path,
) -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    source = tmp_path / "input.txt"
    source.write_text("stable", encoding="utf-8")
    code = f"cat {source}"
    read_action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code},
    }
    read_receipt = build_terminal_execution_receipt(
        code=code,
        plan=TerminalExecutionPlan(
            "shell",
            "read_only",
            True,
            True,
            read_paths=(str(source),),
        ),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
        read_path_epochs=[_file_epoch(source)],
    )
    runtime.record(
        read_action,
        ActionResult(
            success=True,
            content="stable\n",
            parameter={"code": code},
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: read_receipt},
        ),
        context=context,
    )
    runtime.record(
        {
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "opaque-program"},
        },
        ActionResult(success=True, content="done"),
        context=context,
    )

    repeated = runtime.lookup(read_action, context=context)

    assert repeated is not None
    receipt = repeated.metadata["sandbox_observation"]
    assert receipt["cache_validation"] == "epoch_revalidated"
    assert receipt["source_workspace_generation"] == 0
    assert receipt["workspace_generation"] == 1


def test_cross_generation_epoch_mismatch_reexecutes_read(
    tmp_path: Path,
) -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    source = tmp_path / "input.txt"
    source.write_text("before", encoding="utf-8")
    code = f"cat {source}"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=TerminalExecutionPlan(
            "shell",
            "read_only",
            True,
            True,
            read_paths=(str(source),),
        ),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
        read_path_epochs=[_file_epoch(source)],
    )
    runtime.record(
        action,
        ActionResult(
            success=True,
            content="before\n",
            parameter={"code": code},
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )
    runtime.record(
        {
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "opaque-program"},
        },
        ActionResult(success=True, content="done"),
        context=context,
    )
    source.write_text("after", encoding="utf-8")

    assert runtime.lookup(action, context=context) is None
    assert runtime.current_generation(context) == 2


def test_implicit_read_dependency_does_not_cross_generation() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    code = "pwd"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=TerminalExecutionPlan("shell", "read_only", True, True),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
    )
    runtime.record(
        action,
        ActionResult(
            success=True,
            content="/app\n",
            parameter={"code": code},
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )
    runtime.record(
        {
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "opaque-program"},
        },
        ActionResult(success=True, content="done"),
        context=context,
    )

    assert runtime.lookup(action, context=context) is None


def test_invalid_terminal_receipt_fails_open_and_does_not_seed_cache() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    code = "cat /app/a.py"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code},
    }
    receipt = build_terminal_execution_receipt(
        code="different command",
        plan=TerminalExecutionPlan("shell", "read_only", True, True),
        executed=True,
        exit_code=0,
        timed_out=False,
    )

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="ok",
            parameter={"code": code},
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )

    sandbox_receipt = observed.metadata["sandbox_observation"]
    assert sandbox_receipt["effect"] == "unknown"
    assert sandbox_receipt["workspace_generation"] == 1
    assert runtime.lookup(action, context=context) is None


def test_terminal_receipt_language_must_match_requested_execution_mode() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    code = "print(1)"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code, "language": "python"},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=TerminalExecutionPlan("python", "read_only", True, True),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
        requested_language="shell",
    )

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="1\n",
            parameter={"code": code, "language": "python"},
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )

    sandbox_receipt = observed.metadata["sandbox_observation"]
    assert sandbox_receipt["effect"] == "unknown"
    assert sandbox_receipt["workspace_generation"] == 1
    assert runtime.lookup(action, context=context) is None


def test_missing_terminal_receipt_fails_open_and_invalidates_replay() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": "cat /app/a.py"},
    }

    observed = runtime.record(
        action,
        ActionResult(success=True, content="contents"),
        context=context,
    )

    sandbox_receipt = observed.metadata["sandbox_observation"]
    assert sandbox_receipt["effect"] == "unknown"
    assert sandbox_receipt["workspace_generation"] == 1
    assert runtime.lookup(action, context=context) is None


def test_future_terminal_receipt_version_fails_open() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    code = "custom-inspector --status"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=TerminalExecutionPlan("shell", "read_only", True, True),
        executed=True,
        exit_code=0,
        timed_out=False,
    )
    receipt["parser_version"] = 999

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="ok",
            parameter={"code": code},
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )

    assert observed.metadata["sandbox_observation"]["effect"] == "unknown"
    assert observed.metadata["sandbox_observation"]["workspace_generation"] == 1
    assert runtime.lookup(action, context=context) is None


def test_terminal_may_mutate_receipt_invalidates_without_claiming_actual_change() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    code = "printf x > output.txt"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=TerminalExecutionPlan(
            "shell", "mutating", False, True, write_paths=("output.txt",)
        ),
        executed=True,
        exit_code=0,
        timed_out=False,
    )

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="",
            parameter={"code": code},
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )

    sandbox_receipt = observed.metadata["sandbox_observation"]
    assert sandbox_receipt["effect"] == "mutating"
    assert sandbox_receipt["workspace_mutated"] is None
    assert sandbox_receipt["workspace_generation"] == 1


def test_terminal_observed_mutation_is_positive_workspace_evidence() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    code = "printf x > output.txt"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=TerminalExecutionPlan(
            "shell", "mutating", False, True, write_paths=("output.txt",)
        ),
        executed=True,
        exit_code=0,
        timed_out=False,
        mutation_observed=True,
    )

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="",
            parameter={"code": code},
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )

    sandbox_receipt = observed.metadata["sandbox_observation"]
    assert sandbox_receipt["effect"] == "mutating"
    assert sandbox_receipt["workspace_mutated"] is True
    assert sandbox_receipt["workspace_generation"] == 1


def test_untrusted_provider_cannot_supply_terminal_execution_receipt() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    code = "custom-inspector --status"
    action = {
        "tool_name": "untrusted-provider",
        "action_name": "run_code",
        "params": {"code": code},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=TerminalExecutionPlan("shell", "read_only", True, True),
        executed=True,
        exit_code=0,
        timed_out=False,
    )

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="ok",
            parameter={"code": code},
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )

    assert observed.metadata["sandbox_observation"]["effect"] == "unknown"
    assert observed.metadata["sandbox_observation"]["workspace_generation"] == 1
    assert runtime.lookup(action, context=context) is None


def test_background_execution_makes_scope_replay_volatile() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    background_code = "writer > output.log &"
    background_action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": background_code},
    }
    background_receipt = build_terminal_execution_receipt(
        code=background_code,
        plan=TerminalExecutionPlan(
            "shell",
            "unknown",
            False,
            True,
            background=True,
        ),
        executed=True,
        exit_code=0,
        timed_out=False,
        capture_complete=False,
    )
    runtime.record(
        background_action,
        ActionResult(
            success=True,
            content="started",
            parameter={"code": background_code},
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: background_receipt},
        ),
        context=context,
    )

    read_code = "cat output.log"
    read_action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": read_code},
    }
    read_receipt = build_terminal_execution_receipt(
        code=read_code,
        plan=TerminalExecutionPlan(
            "shell", "read_only", True, True, read_paths=("output.log",)
        ),
        executed=True,
        exit_code=0,
        timed_out=False,
    )
    observed = runtime.record(
        read_action,
        ActionResult(
            success=True,
            content="first\n",
            parameter={"code": read_code},
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: read_receipt},
        ),
        context=context,
    )

    assert observed.metadata["sandbox_observation"]["scope_volatile"] is True
    assert runtime.lookup(read_action, context=context) is None


def test_cache_rehydrates_content_after_context_checkpoint_then_renews_reference() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    context.context_lifecycle_state = SimpleNamespace(checkpoint_revision=3)
    code = "print('durable evidence')"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "tool_call_id": "call-original",
        "params": {"code": code, "language": "python"},
    }
    terminal_receipt = build_terminal_execution_receipt(
        code=code,
        plan=TerminalExecutionPlan("python", "read_only", True, True),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
    )
    original = runtime.record(
        action,
        ActionResult(
            success=True,
            content="durable evidence\n",
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: terminal_receipt},
        ),
        context=context,
    )

    retained = runtime.lookup(action, context=context)
    assert retained is not None
    retained_receipt = retained.metadata["sandbox_observation"]
    assert retained_receipt["cache_state"] == "retained_reference"
    assert retained_receipt["content_rehydrated"] is False
    assert '"type": "unchanged"' in retained.content

    context.context_lifecycle_state = SimpleNamespace(checkpoint_revision=4)
    action["tool_call_id"] = "call-after-checkpoint"
    rehydrated = runtime.lookup(action, context=context)
    assert rehydrated is not None
    rehydrated_receipt = rehydrated.metadata["sandbox_observation"]
    assert rehydrated.content == "durable evidence\n"
    assert rehydrated.tool_call_id == "call-after-checkpoint"
    assert rehydrated_receipt["cache_state"] == "rehydrated"
    assert rehydrated_receipt["content_rehydrated"] is True
    assert rehydrated_receipt["observation_id"] == original.metadata[
        "sandbox_observation"
    ]["observation_id"]
    assert rehydrated_receipt["content_sha256"] == original.metadata[
        "sandbox_observation"
    ]["content_sha256"]

    renewed = runtime.lookup(action, context=context)
    assert renewed is not None
    assert renewed.metadata["sandbox_observation"]["cache_state"] == (
        "retained_reference"
    )
    assert '"type": "unchanged"' in renewed.content


def test_oversized_result_is_not_eligible_for_exact_replay() -> None:
    runtime = SandboxToolObservationRuntime(max_replay_content_bytes=32)
    context = _context()
    context.context_lifecycle_state = SimpleNamespace(checkpoint_revision=0)
    code = "print('x' * 128)"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code, "language": "python"},
    }
    terminal_receipt = build_terminal_execution_receipt(
        code=code,
        plan=TerminalExecutionPlan("python", "read_only", True, True),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
    )

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="x" * 128,
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: terminal_receipt},
        ),
        context=context,
    )

    receipt = observed.metadata["sandbox_observation"]
    assert receipt["exact_replay_cached"] is False
    assert receipt["cache_state"] == "not_stored"
    assert receipt["cache_bypass_reason"] == "content_too_large"
    assert runtime.lookup(action, context=context) is None


def test_uncopyable_result_is_not_retained_for_exact_replay() -> None:
    class _UncopyableContent:
        def __deepcopy__(self, _memo):
            raise TypeError("provider object cannot be copied")

    runtime = SandboxToolObservationRuntime()
    context = _context()
    code = "print('opaque provider value')"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code, "language": "python"},
    }
    terminal_receipt = build_terminal_execution_receipt(
        code=code,
        plan=TerminalExecutionPlan("python", "read_only", True, True),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
    )

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content=_UncopyableContent(),
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: terminal_receipt},
        ),
        context=context,
    )

    receipt = observed.metadata["sandbox_observation"]
    assert receipt["exact_replay_cached"] is False
    assert receipt["cache_bypass_reason"] == "content_not_copyable"
    assert runtime.lookup(action, context=context) is None
