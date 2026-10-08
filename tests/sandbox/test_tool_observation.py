import hashlib
import json
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

import pytest

from aworld.core.common import ActionResult
from aworld.sandbox.tool_observation import (
    READ_OBSERVATION_RECEIPT_KEY,
    READ_OBSERVATION_SCHEMA,
    SandboxToolObservationRuntime,
    actions_are_provably_read_only,
    build_planned_action_semantic_receipt,
    canonical_tool_identity,
    classify_tool_effect,
    semantic_target_sha256,
)
from aworld.sandbox.terminal_receipt import (
    TERMINAL_EXECUTION_RECEIPT_KEY,
    TerminalExecutionPlan,
    build_terminal_execution_receipt,
    plan_terminal_execution,
    terminal_execution_context_sha256,
)


def _lifecycle(
    checkpoint_revision: int = 0,
    *,
    session_epoch: int = 0,
    branch_id: str = "main",
):
    return SimpleNamespace(
        session_id="session",
        session_epoch=session_epoch,
        task_epoch=1,
        branch_id=branch_id,
        checkpoint_revision=checkpoint_revision,
    )


def _context():
    return SimpleNamespace(
        task_id="task",
        task_epoch=1,
        session_id="session",
        agent_info=SimpleNamespace(current_agent_id="agent"),
        context_lifecycle_state=_lifecycle(),
    )


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

    plan = plan_terminal_execution("cat /app/a.py; wc -l /app/a.py")
    assert sorted(plan.executable_tokens) == ["cat", "wc"]
    assert plan.executable_set_complete is True


def test_terminal_plan_marks_bounded_executable_catalog_incomplete() -> None:
    plan = plan_terminal_execution("; ".join("pwd" for _ in range(17)))

    assert plan.effect == "read_only"
    assert len(plan.executable_tokens) == 16
    assert plan.executable_set_complete is False


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


def test_callback_sensitive_shell_reads_remain_semantically_unknown() -> None:
    for code in (
        "rg needle /app/input.txt",
        "git --no-pager status --short",
    ):
        effect = classify_tool_effect(
            {
                "tool_name": "terminal",
                "action_name": "run_code",
                "params": {"code": code},
            }
        )
        assert effect.effect == "unknown"

    callback_free = classify_tool_effect(
        {
            "tool_name": "terminal",
            "action_name": "run_code",
            "params": {"code": "rg --no-config needle /app/input.txt"},
        }
    )
    assert callback_free.effect == "read_only"


def test_shell_classifier_accepts_provably_read_only_inline_python() -> None:
    for code in (
        "python -c 'print(1)'",
        "cd /app && python3 -c \"from pathlib import Path; print(Path('a').read_text())\"",
        "cd /app && python -c \"\nimport cv2, numpy as np\ncap = cv2.VideoCapture('example.mp4')\nprint(np.array([cap.get(1)]).max())\n\"",
        'cd /app && python -c "print(1 > 0)" 2>&1 | head -20',
        'cd /app && python -c "print(1)" 2>/dev/null',
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


@pytest.mark.parametrize(
    ("code", "read_paths", "write_paths", "write_set_complete"),
    (
        ("cp source.txt result.txt", ("source.txt",), ("result.txt",), False),
        ("cp -T source.txt result.txt", ("source.txt",), ("result.txt",), True),
        ("install source.txt result.txt", ("source.txt",), ("result.txt",), False),
        (
            "install -T -m 644 source.txt result.txt",
            ("source.txt",),
            ("result.txt",),
            True,
        ),
        ("ln source.txt result.txt", ("source.txt",), ("result.txt",), False),
        ("ln -T source.txt result.txt", ("source.txt",), ("result.txt",), True),
        ("mv source.txt result.txt", (), ("source.txt", "result.txt"), False),
        ("rm first.txt second.txt", (), ("first.txt", "second.txt"), True),
        (
            "sed -i 's/before/after/' source.txt",
            ("source.txt",),
            ("source.txt",),
            True,
        ),
    ),
)
def test_terminal_plan_models_literal_mutation_operands_by_command_semantics(
    code: str,
    read_paths: tuple[str, ...],
    write_paths: tuple[str, ...],
    write_set_complete: bool,
) -> None:
    plan = plan_terminal_execution(code)

    assert plan.effect == "mutating"
    assert plan.read_paths == read_paths
    assert plan.write_paths == write_paths
    assert plan.write_set_complete is write_set_complete


@pytest.mark.parametrize(
    "code",
    (
        "touch $TARGET",
        "touch {declared,helper}.txt",
        "touch ~/result.txt",
        'rm "$TARGET"',
        "rm build/*.o",
        'mv source.txt "$DESTINATION"',
        'cp "$SOURCE" result.txt',
        "install source-*.txt result.txt",
        'ln source.txt "$DESTINATION"',
        "cp --target-directory=result source.txt",
        "cp first.txt second.txt result",
        "install first.txt second.txt result",
        "ln first.txt second.txt result",
        "mv first.txt second.txt result",
        "sed -i.bak 's/before/after/' source.txt",
        'sed -i "s/before/after/" "$TARGET"',
    ),
)
def test_terminal_plan_fails_closed_for_ambiguous_mutation_operands(
    code: str,
) -> None:
    plan = plan_terminal_execution(code)

    assert plan.effect == "mutating"
    assert plan.write_set_complete is False


def test_terminal_plan_bounds_write_paths_and_marks_truncation_incomplete() -> None:
    overlong_path = "x" * 513
    overlong = plan_terminal_execution(f"touch {overlong_path}")
    too_many = plan_terminal_execution(
        "rm " + " ".join(f"output-{index}.txt" for index in range(17))
    )

    assert overlong.write_paths == ("x" * 512,)
    assert overlong.write_set_complete is False
    assert len(too_many.write_paths) == 16
    assert too_many.write_paths[0] == "output-0.txt"
    assert too_many.write_paths[-1] == "output-15.txt"
    assert too_many.write_set_complete is False


@pytest.mark.parametrize(
    "code",
    (
        "printf changed > declared.txt; make",
        "rm declared.txt; curl https://example.invalid",
        "touch declared.txt; bash script.sh",
        "printf changed > declared.txt | opaque-filter",
    ),
)
def test_unknown_shell_component_cannot_hide_behind_known_mutation(code: str) -> None:
    plan = plan_terminal_execution(code)

    assert plan.effect == "unknown"
    assert plan.write_set_complete is False


@pytest.mark.parametrize(
    ("source", "read_paths", "write_paths", "write_set_complete"),
    (
        (
            "import os; os.rename('/app/a', '/app/b')",
            (),
            ("/app/a", "/app/b"),
            False,
        ),
        (
            "import os; os.replace('/app/a', '/app/b')",
            (),
            ("/app/a", "/app/b"),
            False,
        ),
        (
            "import shutil; shutil.move('/app/a', '/app/b')",
            (),
            ("/app/a", "/app/b"),
            False,
        ),
        (
            "from pathlib import Path; Path('/app/a').rename('/app/b')",
            (),
            ("/app/a", "/app/b"),
            False,
        ),
        (
            "from pathlib import Path; Path('/app/a').replace('/app/b')",
            (),
            ("/app/a", "/app/b"),
            False,
        ),
        (
            "import shutil; shutil.copy('/app/source', '/app/result')",
            ("/app/source",),
            ("/app/result",),
            False,
        ),
        (
            "import shutil; shutil.copy2('/app/source', '/app/result')",
            ("/app/source",),
            ("/app/result",),
            False,
        ),
        (
            "import shutil; shutil.copyfile('/app/source', '/app/result')",
            ("/app/source",),
            ("/app/result",),
            True,
        ),
        (
            "import shutil; shutil.copytree('/app/source', '/app/result')",
            ("/app/source",),
            ("/app/result",),
            False,
        ),
    ),
)
def test_python_mutation_plan_models_move_and_copy_endpoints(
    source: str,
    read_paths: tuple[str, ...],
    write_paths: tuple[str, ...],
    write_set_complete: bool,
) -> None:
    plan = plan_terminal_execution(source, language="python")

    assert plan.effect == "mutating"
    assert plan.read_paths == read_paths
    assert plan.write_paths == write_paths
    assert plan.write_set_complete is write_set_complete


@pytest.mark.parametrize(
    ("source", "write_path"),
    (
        (
            "import json\n"
            "open('/app/out.json', 'w').write(json.dumps({'ok': True}))\n",
            "/app/out.json",
        ),
        (
            "import json\n"
            "with open('/app/out.json', 'w') as output:\n"
            "    json.dump({'ok': True}, output)\n",
            "/app/out.json",
        ),
        (
            "import csv\n"
            "with open('/app/out.csv', 'w') as output:\n"
            "    csv.writer(output).writerow(['x', 'y'])\n",
            "/app/out.csv",
        ),
        (
            "import csv\n"
            "with open('/app/out.csv', 'w') as output:\n"
            "    writer = csv.writer(output)\n"
            "    writer.writerow(['x', 'y'])\n",
            "/app/out.csv",
        ),
        (
            "from pathlib import Path\n"
            "with Path('/app/out.txt').open('w') as output:\n"
            "    output.write('done')\n",
            "/app/out.txt",
        ),
        (
            "from pathlib import Path\n"
            "target = Path('/app', 'out.txt')\n"
            "with target.open('w') as output:\n"
            "    output.write('done')\n",
            "/app/out.txt",
        ),
        (
            "from pathlib import Path\n"
            "Path('/ignored', '/app', 'out.txt').write_text('done')\n",
            "/app/out.txt",
        ),
        (
            "from pathlib import Path\n"
            "Path('/app/out.txt').open('w').write('done')\n",
            "/app/out.txt",
        ),
    ),
)
def test_python_known_writer_stacks_keep_declared_output_complete(
    source: str, write_path: str
) -> None:
    plan = plan_terminal_execution(source, language="python")

    assert plan.effect == "mutating"
    assert plan.read_paths == ()
    assert plan.write_paths == (write_path,)
    assert plan.write_set_complete is True


@pytest.mark.parametrize(
    ("source", "write_set_complete"),
    (
        ("import os; os.mkdir('/app/declared')", True),
        (
            "import os; os.makedirs('/app/declared/nested', exist_ok=True)",
            False,
        ),
        (
            "from pathlib import Path; Path('/app/declared').mkdir()",
            True,
        ),
        (
            "from pathlib import Path; "
            "Path('/app/declared/nested').mkdir(parents=True)",
            False,
        ),
        (
            "from pathlib import Path; "
            "Path('/app/declared/nested').mkdir(parents=CREATE_PARENTS)",
            False,
        ),
    ),
)
def test_python_directory_creation_reports_implicit_parent_writes(
    source: str, write_set_complete: bool
) -> None:
    plan = plan_terminal_execution(source, language="python")

    assert plan.effect == "mutating"
    assert plan.write_set_complete is write_set_complete


def test_unproven_python_attribute_open_is_not_read_only() -> None:
    plan = plan_terminal_execution("client.open('w')", language="python")

    assert plan.effect == "unknown"
    assert plan.write_paths == ()
    assert plan.write_set_complete is False


@pytest.mark.parametrize(
    ("source", "write_paths", "write_set_complete"),
    (
        (
            "import numpy as np; np.save('/app/out', np.array([1]))",
            ("/app/out.npy",),
            True,
        ),
        (
            "import numpy as np; np.save('/app/out.npy', np.array([1]))",
            ("/app/out.npy",),
            True,
        ),
        (
            "from pathlib import Path\n"
            "import numpy as np\n"
            "np.save(Path('/app', 'out'), np.array([1]))\n",
            ("/app/out.npy",),
            True,
        ),
        (
            "import numpy as np\n"
            "with open('/app/out.bin', 'wb') as output:\n"
            "    np.save(output, np.array([1]))\n",
            ("/app/out.bin",),
            True,
        ),
        (
            "import numpy as np; np.save(destination, np.array([1]))",
            (),
            False,
        ),
    ),
)
def test_numpy_save_reports_runtime_filename_semantics(
    source: str,
    write_paths: tuple[str, ...],
    write_set_complete: bool,
) -> None:
    plan = plan_terminal_execution(source, language="python")

    assert plan.effect == "mutating"
    assert plan.write_paths == write_paths
    assert plan.write_set_complete is write_set_complete


@pytest.mark.parametrize(
    "source",
    (
        "from pathlib import Path; "
        "Path('/app', OUTPUT_NAME).write_text('done')",
        "from pathlib import Path\n"
        "target = Path('/app', OUTPUT_NAME)\n"
        "target.open('w')\n",
        "from pathlib import Path\n"
        "import numpy as np\n"
        "np.save(Path('/app', OUTPUT_NAME), np.array([1]))\n",
    ),
)
def test_dynamic_path_constructor_segment_fails_closed(source: str) -> None:
    plan = plan_terminal_execution(source, language="python")

    assert plan.effect in {"mutating", "unknown"}
    assert plan.write_paths == ()
    assert plan.write_set_complete is False


def test_generic_save_receiver_cannot_claim_complete_write_set() -> None:
    plan = plan_terminal_execution(
        "artifact.save('/app/out')", language="python"
    )

    assert plan.effect == "unknown"
    assert plan.write_paths == ("/app/out",)
    assert plan.write_set_complete is False


@pytest.mark.parametrize(
    "source",
    (
        "rm -rf /app/tree",
        "cp -r -T /app/source /app/result",
        "python -c \"import shutil; shutil.rmtree('/app/tree')\"",
    ),
)
def test_recursive_mutation_never_claims_exact_write_set(source: str) -> None:
    plan = plan_terminal_execution(source)

    assert plan.effect == "mutating"
    assert plan.write_set_complete is False


@pytest.mark.parametrize(
    "unknown_component",
    (
        "__import__('os').system('true')",
        "exec('value = 1')",
        "unknown_callback()",
        "import subprocess",
    ),
)
def test_unknown_python_component_cannot_hide_behind_known_mutation(
    unknown_component: str,
) -> None:
    source = (
        "from pathlib import Path\n"
        "Path('/app/out.txt').write_text('done')\n"
        f"{unknown_component}\n"
    )

    plan = plan_terminal_execution(source, language="python")

    assert plan.effect == "unknown"
    assert plan.write_paths == ("/app/out.txt",)
    assert plan.write_set_complete is False


def test_quoted_python_heredoc_propagates_unknown_effect_component() -> None:
    source = """python <<'PY'
from pathlib import Path
Path('/app/out.txt').write_text('done')
unknown_callback()
PY
"""

    plan = plan_terminal_execution(source)

    assert plan.effect == "unknown"
    assert plan.write_paths == ("/app/out.txt",)
    assert plan.write_set_complete is False


def test_python_gcode_transform_keeps_complete_declared_output() -> None:
    source = """
rows = open('/app/text.gcode').read().splitlines()
revised = []
for index, row in enumerate(rows):
    line = row.strip()
    if not line:
        continue
    values = []
    for token in line.split():
        bounded = max(0.0, min(float(index), 999.0))
        values.append(str(round(bounded, 3)))
    revised.append(''.join(values).rstrip())
payload = '\\n'.join(revised).lstrip()
print(int(len(payload)))
with open('/app/out.txt', 'w') as output:
    output.write(payload)
    output.flush()
"""

    plan = plan_terminal_execution(source, language="python")

    assert plan.effect == "mutating"
    assert plan.read_paths == ("/app/text.gcode",)
    assert plan.write_paths == ("/app/out.txt",)
    assert plan.read_set_complete is True
    assert plan.write_set_complete is True


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
        execution_context_sha256="sha256:" + "e" * 64,
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


def test_semantic_targets_resolve_explicit_cwd_and_command_local_cd() -> None:
    context = _context()
    context.workspace_path = "/app"
    explicit_cwd = build_planned_action_semantic_receipt(
        context=context,
        tool_name="terminal__run_code",
        arguments={"code": "cat result.txt", "cwd": "/app"},
        delivery_intent="continue_exploration",
    )
    local_cd = build_planned_action_semantic_receipt(
        context=context,
        tool_name="terminal__run_code",
        arguments={"code": "cd /app && cat result.txt"},
        delivery_intent="continue_exploration",
    )
    workspace_relative = build_planned_action_semantic_receipt(
        context=context,
        tool_name="terminal__run_code",
        arguments={"code": "cat result.txt"},
        delivery_intent="continue_exploration",
    )
    absolute = build_planned_action_semantic_receipt(
        context=context,
        tool_name="terminal__run_code",
        arguments={"code": "cat /app/result.txt"},
        delivery_intent="continue_exploration",
    )

    expected = semantic_target_sha256("/app/result.txt")
    assert explicit_cwd.target_ids == (expected,)
    assert local_cd.target_ids == (expected,)
    assert workspace_relative.target_ids == absolute.target_ids == (expected,)
    assert semantic_target_sha256("/app") not in local_cd.target_ids


def test_relative_declared_target_uses_trusted_workspace_without_basename_alias(
    tmp_path,
) -> None:
    context = _context()
    context.workspace_path = str(tmp_path)
    context.context_info = {
        "public_deliverable_contract": {
            "schema_version": "aworld.public-deliverables/v1",
            "authority": "public_task_advisory",
            "source": "public_task_text",
            "artifacts": [
                {
                    "deliverable_id": "result",
                    "path": "result.json",
                    "display_path": "result.json",
                    "kind": "file",
                    "authority": "public_task_advisory",
                }
            ],
        }
    }
    workspace_write = build_planned_action_semantic_receipt(
        context=context,
        tool_name="terminal__run_code",
        arguments={"code": "printf '{}' > result.json"},
        delivery_intent="produce_candidate",
    )
    sibling_write = build_planned_action_semantic_receipt(
        context=context,
        tool_name="terminal__run_code",
        arguments={
            "code": "printf '{}' > result.json",
            "cwd": str(tmp_path / "sibling"),
        },
        delivery_intent="produce_candidate",
    )

    assert workspace_write.target_ids == (
        semantic_target_sha256(str(tmp_path / "result.json")),
    )
    assert workspace_write.declared_deliverable_targeted is True
    assert sibling_write.declared_deliverable_targeted is False


def test_filesystem_copy_targets_only_mutated_destination(tmp_path) -> None:
    context = _context()
    context.workspace_path = str(tmp_path)
    destination = tmp_path / "result.json"
    context.context_info = {
        "public_deliverable_contract": {
            "schema_version": "aworld.public-deliverables/v1",
            "authority": "public_task_advisory",
            "source": "public_task_text",
            "artifacts": [
                {
                    "deliverable_id": "result",
                    "path": str(destination),
                    "display_path": "result.json",
                    "kind": "file",
                    "authority": "public_task_advisory",
                }
            ],
        }
    }

    receipt = build_planned_action_semantic_receipt(
        context=context,
        tool_name="filesystem__copy_file",
        arguments={
            "source": "/tmp/read-only-input.json",
            "destination": str(destination),
        },
        delivery_intent="produce_candidate",
    )

    assert receipt.effect == "mutating"
    assert receipt.target_ids == (semantic_target_sha256(str(destination)),)
    assert receipt.declared_deliverable_targeted is True


def test_filesystem_move_targets_source_and_destination(tmp_path) -> None:
    context = _context()
    context.workspace_path = str(tmp_path)
    source = tmp_path / "scratch.json"
    destination = tmp_path / "result.json"

    receipt = build_planned_action_semantic_receipt(
        context=context,
        tool_name="filesystem__move_file",
        arguments={"source": str(source), "destination": str(destination)},
        delivery_intent="produce_candidate",
    )

    assert receipt.effect == "mutating"
    assert receipt.target_ids == (
        semantic_target_sha256(str(source)),
        semantic_target_sha256(str(destination)),
    )


def test_registered_validation_semantics_require_canonical_cwd(tmp_path) -> None:
    context = _context()
    context.workspace_path = str(tmp_path)
    context.completion_contract = SimpleNamespace(
        required_artifacts=(),
        validation_commands=(
            SimpleNamespace(
                command_id="cwd-check",
                argv=("sh", "-c", "cat result.json"),
                cwd="checks",
            ),
        ),
    )
    correct = build_planned_action_semantic_receipt(
        context=context,
        tool_name="terminal__run_code",
        arguments={"code": "cat result.json", "cwd": str(tmp_path / "checks")},
        delivery_intent="validate_candidate",
    )
    wrong = build_planned_action_semantic_receipt(
        context=context,
        tool_name="terminal__run_code",
        arguments={"code": "cat result.json", "cwd": str(tmp_path / "other")},
        delivery_intent="validate_candidate",
    )

    assert correct.effect == "validation"
    assert correct.validation_kind is not None
    assert wrong.effect == "read_only"
    assert wrong.validation_kind is None


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


def test_known_mutation_advances_generation_but_unknown_does_not_claim_progress() -> (
    None
):
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


def test_authoritative_terminal_receipt_overrides_raw_code_guess_and_seeds_cache() -> (
    None
):
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
        execution_context_sha256=terminal_execution_context_sha256(sys.executable),
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


def test_terminal_execution_context_does_not_seed_outer_sandbox_replay() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    code = "print('provider-bound')"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code, "language": "python"},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=plan_terminal_execution(code, language="python"),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
        execution_context_sha256="sha256:" + "e" * 64,
    )

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="provider-bound\n",
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )

    assert observed.metadata["sandbox_observation"]["effect"] == "read_only"
    assert observed.metadata["sandbox_observation"]["exact_replay_cached"] is False
    assert runtime.lookup(action, context=context) is None


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
            read_projection_reusable=True,
        ),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
        read_path_epochs=[_file_epoch(source)],
        representation="terminal.run-code.structured.full/v1",
        source_checkpoint_revision=0,
        execution_context_sha256=terminal_execution_context_sha256(sys.executable),
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
            read_projection_reusable=True,
        ),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
        read_path_epochs=[_file_epoch(source)],
        representation="terminal.run-code.structured.full/v1",
        source_checkpoint_revision=0,
        execution_context_sha256=terminal_execution_context_sha256(sys.executable),
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
        execution_context_sha256=terminal_execution_context_sha256(sys.executable),
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


def test_malformed_terminal_read_coverage_cannot_seed_replay(tmp_path: Path) -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    source = tmp_path / "input.txt"
    source.write_text("stable", encoding="utf-8")
    code = f"head -n 1 {source}"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=plan_terminal_execution(code),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
        read_path_epochs=[_file_epoch(source)],
    )
    receipt["read_ranges"] = [{"kind": "line_range", "start": 2, "end": 1}]

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="stable",
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )

    assert observed.metadata["sandbox_observation"]["effect"] == "unknown"
    assert runtime.lookup(action, context=context) is None


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("effect", "unknown"),
        ("potential_effect", "read_only"),
        ("write_paths", []),
        ("write_set_complete", False),
    ),
)
def test_terminal_receipt_cannot_rewrite_shared_mutation_plan(
    field: str,
    value: object,
) -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    code = "printf updated > /app/result.txt"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=plan_terminal_execution(code),
        executed=True,
        exit_code=0,
        timed_out=False,
    )
    receipt[field] = value

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="updated",
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )

    assert observed.metadata["sandbox_observation"]["effect"] == "unknown"
    assert "action_semantic_receipt" not in observed.metadata["sandbox_observation"]


def test_terminal_receipt_cannot_replace_dispatched_code_with_result_echo() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    dispatched = "printf declared > /app/result.txt; printf helper > /tmp/helper.txt"
    substituted = "printf declared > /app/result.txt"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": dispatched},
    }
    receipt = build_terminal_execution_receipt(
        code=substituted,
        plan=plan_terminal_execution(substituted),
        executed=True,
        exit_code=0,
        timed_out=False,
    )

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="declared",
            parameter={"code": substituted},
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )

    sandbox = observed.metadata["sandbox_observation"]
    assert sandbox["effect"] == "unknown"
    assert "action_semantic_receipt" not in sandbox


def test_terminal_receipt_binds_remote_python_executable_authority() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    provider_python = "/remote/venv/bin/python"
    python_source = "open('/app/result.txt', 'w').write('updated')"
    code = f"{shlex.quote(provider_python)} -c {shlex.quote(python_source)}"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code},
    }
    provider_plan = plan_terminal_execution(
        code,
        trusted_executable_paths=(provider_python,),
    )
    assert provider_plan.effect == "mutating"
    assert provider_plan.write_paths == ("/app/result.txt",)
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=provider_plan,
        executed=True,
        exit_code=0,
        timed_out=False,
        execution_context_sha256="sha256:" + "e" * 64,
    )

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="updated",
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )

    semantic = observed.metadata["sandbox_observation"][
        "action_semantic_receipt"
    ]
    assert semantic["effect"] == "mutating"
    assert semantic["target_ids"] == [semantic_target_sha256("/app/result.txt")]

    unattested = dict(receipt)
    unattested.pop("execution_context_sha256")
    rejected = SandboxToolObservationRuntime().record(
        action,
        ActionResult(
            success=True,
            content="updated",
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: unattested},
        ),
        context=_context(),
    )
    rejected_sandbox = rejected.metadata["sandbox_observation"]
    assert rejected_sandbox["effect"] == "unknown"
    assert "action_semantic_receipt" not in rejected_sandbox


@pytest.mark.parametrize(
    ("python_source", "write_path"),
    (
        (
            "open(\"/app/it's.txt\", 'w').write('nested \\\"quote\\\"')",
            "/app/it's.txt",
        ),
        (
            "open('/app/$literal.txt', 'w').write('not expanded')",
            "/app/$literal.txt",
        ),
    ),
)
def test_shell_quoted_python_source_decodes_literal_quote_splices(
    python_source: str, write_path: str
) -> None:
    code = f"{shlex.quote(sys.executable)} -c {shlex.quote(python_source)}"

    plan = plan_terminal_execution(
        code,
        trusted_executable_paths=(sys.executable,),
    )

    assert plan.effect == "mutating"
    assert plan.write_paths == (write_path,)
    assert plan.write_set_complete is True


@pytest.mark.parametrize(
    "python_argument",
    (
        '"$PYTHON_SOURCE"',
        '"open(\'/app/$TARGET.txt\', \'w\').write(\'expanded\')"',
    ),
)
def test_shell_python_source_with_runtime_expansion_stays_unknown(
    python_argument: str,
) -> None:
    code = f"{shlex.quote(sys.executable)} -c {python_argument}"

    plan = plan_terminal_execution(
        code,
        trusted_executable_paths=(sys.executable,),
    )

    assert plan.effect == "unknown"
    assert plan.write_paths == ()
    assert plan.write_set_complete is False


def test_provider_authoritative_container_epoch_is_not_replayed_on_host() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    code = "cat /workspace/input.txt"
    action = {
        "tool_name": "docker",
        "action_name": "run_code",
        "params": {"code": code},
    }
    plan = plan_terminal_execution(code)
    remote_epoch = {
        "path": "/workspace/input.txt",
        "resolved_path": "/workspace/input.txt",
        "link_inode": 1,
        "link_mtime_ns": 2,
        "mode": 0o100644,
        "size": 6,
        "mtime_ns": 3,
        "ctime_ns": 4,
        "inode": 5,
        "authority": "docker:sha256:" + "a" * 64,
        "fingerprint": "sha256:" + "b" * 64,
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=plan,
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_docker_command_contract",
        read_path_epochs=[remote_epoch],
        representation="docker.run-code.text.full/v2",
        source_checkpoint_revision=0,
        execution_context_sha256="sha256:" + "e" * 64,
    )

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="remote",
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )

    assert observed.metadata["sandbox_observation"]["effect"] == "read_only"
    assert observed.metadata["sandbox_observation"]["workspace_generation"] == 0
    assert runtime.lookup(action, context=context) is None
    assert runtime.current_generation(context) == 0

    mismatched_receipt = {**receipt, "source_checkpoint_revision": 1}
    mismatched = SandboxToolObservationRuntime().record(
        action,
        ActionResult(
            success=True,
            content="remote",
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: mismatched_receipt},
        ),
        context=context,
    )
    assert mismatched.metadata["sandbox_observation"]["effect"] == "unknown"
    assert "action_semantic_receipt" not in mismatched.metadata["sandbox_observation"]


def test_docker_terminal_receipt_rejects_old_analyzer_or_missing_context() -> None:
    context = _context()
    code = "cat /workspace/input.txt"
    action = {
        "tool_name": "docker",
        "action_name": "run_code",
        "params": {"code": code},
    }
    epoch = {
        "path": "/workspace/input.txt",
        "resolved_path": "/workspace/input.txt",
        "link_inode": 1,
        "link_mtime_ns": 2,
        "mode": 0o100644,
        "size": 6,
        "mtime_ns": 3,
        "ctime_ns": 4,
        "inode": 5,
        "authority": "docker:sha256:" + "a" * 64,
        "fingerprint": "sha256:" + "b" * 64,
    }
    valid = build_terminal_execution_receipt(
        code=code,
        plan=plan_terminal_execution(code),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_docker_command_contract",
        read_path_epochs=[epoch],
        representation="docker.run-code.text.full/v2",
        source_checkpoint_revision=0,
        execution_context_sha256="sha256:" + "e" * 64,
    )
    old_version = {**valid, "parser_version": 6}
    missing_context = dict(valid)
    missing_context.pop("execution_context_sha256")
    malformed_context = {**valid, "execution_context_sha256": "forged"}

    for receipt in (old_version, missing_context, malformed_context):
        observed = SandboxToolObservationRuntime().record(
            action,
            ActionResult(
                success=True,
                content="remote",
                parameter=action["params"],
                metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
            ),
            context=context,
        )

        sandbox_receipt = observed.metadata["sandbox_observation"]
        assert sandbox_receipt["effect"] == "unknown"
        assert sandbox_receipt["workspace_generation"] == 1
        assert "action_semantic_receipt" not in sandbox_receipt


def test_provider_cache_hit_is_replay_not_fresh_execution_evidence() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    code = "cat /workspace/input.txt"
    action = {
        "tool_name": "docker",
        "action_name": "run_code",
        "params": {"code": code},
    }
    remote_epoch = {
        "path": "/workspace/input.txt",
        "resolved_path": "/workspace/input.txt",
        "link_inode": 1,
        "link_mtime_ns": 2,
        "mode": 0o100644,
        "size": 6,
        "mtime_ns": 3,
        "ctime_ns": 4,
        "inode": 5,
        "authority": "docker:sha256:" + "a" * 64,
        "fingerprint": "sha256:" + "b" * 64,
    }
    observation_id = "sha256:" + "c" * 64
    content_sha256 = "sha256:" + "d" * 64
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=plan_terminal_execution(code),
        executed=False,
        cache_hit=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_docker_command_contract",
        read_path_epochs=[remote_epoch],
        observation_id=observation_id,
        content_sha256=content_sha256,
        representation="docker.run-code.text.full/v2",
        source_checkpoint_revision=0,
        execution_context_sha256="sha256:" + "e" * 64,
    )

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content='{"type":"unchanged"}',
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )
    sandbox_receipt = observed.metadata["sandbox_observation"]

    assert sandbox_receipt["cache_hit"] is True
    assert sandbox_receipt["executed"] is False
    assert sandbox_receipt["cache_state"] == "provider_replay"
    assert sandbox_receipt["observation_id"] == observation_id
    assert sandbox_receipt["content_sha256"] == content_sha256
    assert "action_semantic_receipt" not in sandbox_receipt

    stale_receipt = {**receipt, "source_checkpoint_revision": 1}
    stale = SandboxToolObservationRuntime().record(
        action,
        ActionResult(
            success=True,
            content='{"type":"unchanged"}',
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: stale_receipt},
        ),
        context=context,
    )
    stale_sandbox_receipt = stale.metadata["sandbox_observation"]
    assert stale.success is False
    assert stale.error == "provider_replay_rejected"
    assert stale_sandbox_receipt["cache_state"] == "provider_replay_rejected"
    assert stale_sandbox_receipt["executed"] is False
    assert "action_semantic_receipt" not in stale_sandbox_receipt


@pytest.mark.parametrize(
    ("corrupt_field", "corrupt_value"),
    (
        ("observation_id", "forged"),
        ("cache_hit", "yes"),
        ("read_representation", "docker.run-code.text.line_range/v1"),
    ),
)
def test_malformed_provider_replay_claim_still_fails_execution_evidence_closed(
    corrupt_field,
    corrupt_value,
) -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    code = "cat /workspace/input.txt"
    action = {
        "tool_name": "docker",
        "action_name": "run_code",
        "params": {"code": code},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=plan_terminal_execution(code),
        executed=False,
        cache_hit=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_docker_command_contract",
        read_path_epochs=[
            {
                "path": "/workspace/input.txt",
                "resolved_path": "/workspace/input.txt",
                "link_inode": 1,
                "link_mtime_ns": 2,
                "mode": 0o100644,
                "size": 6,
                "mtime_ns": 3,
                "ctime_ns": 4,
                "inode": 5,
                "authority": "docker:sha256:" + "a" * 64,
                "fingerprint": "sha256:" + "b" * 64,
            }
        ],
        observation_id="sha256:" + "c" * 64,
        content_sha256="sha256:" + "d" * 64,
        representation="docker.run-code.text.full/v2",
        source_checkpoint_revision=0,
        execution_context_sha256="sha256:" + "e" * 64,
    )
    receipt[corrupt_field] = corrupt_value

    observed = runtime.record(
        action,
        ActionResult(
            success=True,
            content='{"type":"unchanged"}',
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=context,
    )
    sandbox_receipt = observed.metadata["sandbox_observation"]

    assert sandbox_receipt["cache_hit"] is True
    assert sandbox_receipt["executed"] is False
    assert sandbox_receipt["cache_state"] == "provider_replay_rejected"
    assert observed.success is False
    assert observed.error == "provider_replay_rejected"
    assert "action_semantic_receipt" not in sandbox_receipt


def test_cache_requires_complete_scope_but_zero_task_epoch_is_valid() -> None:
    runtime = SandboxToolObservationRuntime()
    code = "printf stable"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=plan_terminal_execution(code),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
        execution_context_sha256=terminal_execution_context_sha256(sys.executable),
    )
    zero_epoch = SimpleNamespace(
        task_id="task",
        task_epoch=0,
        session_id="session",
        agent_info=SimpleNamespace(current_agent_id="agent"),
        context_lifecycle_state=_lifecycle(),
    )
    missing_session = SimpleNamespace(
        task_id="task",
        task_epoch=0,
        session_id="",
        agent_info=SimpleNamespace(current_agent_id="agent"),
        context_lifecycle_state=SimpleNamespace(
            session_id="",
            session_epoch=0,
            task_epoch=0,
            branch_id="main",
            checkpoint_revision=0,
        ),
    )

    runtime.record(
        action,
        ActionResult(
            success=True,
            content="stable",
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=zero_epoch,
    )
    assert runtime.lookup(action, context=zero_epoch) is not None

    other_runtime = SandboxToolObservationRuntime()
    other_runtime.record(
        action,
        ActionResult(
            success=True,
            content="stable",
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=missing_session,
    )
    assert other_runtime.lookup(action, context=missing_session) is None


def test_sandbox_scope_separates_agent_branch_and_session_epoch() -> None:
    runtime = SandboxToolObservationRuntime()
    code = "printf stable"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
        plan=plan_terminal_execution(code),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
        execution_context_sha256=terminal_execution_context_sha256(sys.executable),
    )
    source = _context()
    runtime.record(
        action,
        ActionResult(
            success=True,
            content="stable",
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
        ),
        context=source,
    )
    other_agent = _context()
    other_agent.agent_info.current_agent_id = "agent-b"
    other_branch = _context()
    other_branch.context_lifecycle_state = _lifecycle(branch_id="rewind-1")
    resumed = _context()
    resumed.context_lifecycle_state = _lifecycle(session_epoch=1)

    assert runtime.lookup(action, context=source) is not None
    assert runtime.lookup(action, context=other_agent) is None
    assert runtime.lookup(action, context=other_branch) is None
    assert runtime.lookup(action, context=resumed) is None


def test_scope_lru_evicts_generation_volatile_and_related_cache_state() -> None:
    runtime = SandboxToolObservationRuntime(max_cache_entries=2)
    code = "opaque-reader &"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code},
    }
    receipt = build_terminal_execution_receipt(
        code=code,
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
    scopes = []
    for index in range(3):
        context = _context()
        context.task_id = f"task-{index}"
        runtime.record(
            action,
            ActionResult(
                success=True,
                content="started",
                parameter=action["params"],
                metadata={TERMINAL_EXECUTION_RECEIPT_KEY: receipt},
            ),
            context=context,
        )
        scopes.append(next(reversed(runtime._scope_lru)))

    retained_scopes = set(runtime._scope_lru)
    assert len(retained_scopes) == 2
    assert scopes[0] not in retained_scopes
    assert set(runtime._generation).issubset(retained_scopes)
    assert runtime._volatile_scopes.issubset(retained_scopes)
    assert all(key[0] in retained_scopes for key in runtime._cache)
    assert all(key[0] in retained_scopes for key in runtime._authoritative_effects)
    assert all(key[0] in retained_scopes for key in runtime._retained_read_facts)


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


def test_terminal_may_mutate_receipt_invalidates_without_claiming_actual_change() -> (
    None
):
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
        plan=plan_terminal_execution(background_code),
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


def test_cache_misses_after_checkpoint_and_renews_after_fresh_execution() -> None:
    runtime = SandboxToolObservationRuntime()
    context = _context()
    context.context_lifecycle_state = _lifecycle(3)
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
        execution_context_sha256=terminal_execution_context_sha256(sys.executable),
    )
    runtime.record(
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
    assert retained_receipt["executed"] is False
    assert retained_receipt["content_rehydrated"] is False
    assert '"type": "unchanged"' in retained.content

    context.context_lifecycle_state = _lifecycle(4)
    action["tool_call_id"] = "call-after-checkpoint"
    assert runtime.lookup(action, context=context) is None

    refreshed = runtime.record(
        action,
        ActionResult(
            success=True,
            content="durable evidence\n",
            parameter=action["params"],
            metadata={TERMINAL_EXECUTION_RECEIPT_KEY: terminal_receipt},
        ),
        context=context,
    )
    assert refreshed.metadata["sandbox_observation"]["cache_hit"] is False

    renewed = runtime.lookup(action, context=context)
    assert renewed is not None
    assert renewed.metadata["sandbox_observation"]["cache_state"] == (
        "retained_reference"
    )
    assert '"type": "unchanged"' in renewed.content


def test_oversized_file_result_retains_only_compact_epoch_bound_facts(
    tmp_path: Path,
) -> None:
    runtime = SandboxToolObservationRuntime(max_replay_content_bytes=32)
    context = _context()
    context.context_lifecycle_state = _lifecycle(0)
    source = tmp_path / "large.txt"
    source.write_text("x" * 128, encoding="utf-8")
    code = f"cat {source}"
    action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": code},
    }
    terminal_receipt = build_terminal_execution_receipt(
        code=code,
        plan=TerminalExecutionPlan(
            "shell",
            "read_only",
            True,
            True,
            read_paths=(str(source),),
            read_projection_reusable=True,
        ),
        executed=True,
        exit_code=0,
        timed_out=False,
        effect_source="trusted_command_contract",
        read_path_epochs=[_file_epoch(source)],
        representation="terminal.run-code.structured.full/v1",
        source_checkpoint_revision=0,
        execution_context_sha256=terminal_execution_context_sha256(sys.executable),
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
    retained = runtime.lookup(action, context=context)
    assert retained is not None
    retained_receipt = retained.metadata["sandbox_observation"]
    assert retained_receipt["cache_state"] == "retained_facts"
    assert retained_receipt["exact_replay_cached"] is False
    assert len(retained.content.encode("utf-8")) < 1024

    other_scope = SimpleNamespace(
        task_id="other-task",
        task_epoch=context.task_epoch,
        session_id=context.session_id,
    )
    assert runtime.lookup(action, context=other_scope) is None

    source.write_text("changed", encoding="utf-8")
    assert runtime.lookup(action, context=context) is None


def test_retained_fact_requires_same_model_representation_and_checkpoint(
    tmp_path: Path,
) -> None:
    runtime = SandboxToolObservationRuntime(max_replay_content_bytes=32)
    context = _context()
    source = tmp_path / "input.txt"
    source.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    epoch = _file_epoch(source)
    filesystem_action = {
        "tool_name": "filesystem",
        "action_name": "read_file",
        "params": {"path": str(source), "output": "text", "head": 3},
    }
    content_sha256 = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    filesystem_result = ActionResult(
        success=True,
        content="alpha\nbeta\ngamma\n" * 8,
        parameter=filesystem_action["params"],
        metadata={
            READ_OBSERVATION_RECEIPT_KEY: {
                "schema_version": READ_OBSERVATION_SCHEMA,
                "authority": "host",
                "path": str(source),
                "epoch": {**epoch, "authority": "host"},
                "coverage": {"kind": "line_range", "start": 1, "end": 3},
                "coverage_complete": True,
                "content_sha256": content_sha256,
                "observation_id": "sha256:" + "a" * 64,
                "cache_hit": False,
                "executed": True,
                "representation": "filesystem.read-file.text.head/v1",
                "source_checkpoint_revision": 0,
            }
        },
    )
    observed_filesystem = runtime.record(
        filesystem_action, filesystem_result, context=context
    )
    assert observed_filesystem.metadata["sandbox_observation"]["observation_id"] == (
        "sha256:" + "a" * 64
    )
    assert observed_filesystem.metadata["sandbox_observation"]["content_sha256"] == (
        content_sha256
    )
    forged_result = ActionResult(
        success=True,
        content="alpha\nbeta\n",
        parameter=filesystem_action["params"],
        metadata={
            READ_OBSERVATION_RECEIPT_KEY: {
                **filesystem_result.metadata[READ_OBSERVATION_RECEIPT_KEY],
                "coverage": {"kind": "line_range", "start": 1, "end": 2},
            }
        },
    )
    forged_observed = SandboxToolObservationRuntime().record(
        filesystem_action,
        forged_result,
        context=context,
    )
    assert (
        "action_semantic_receipt" not in forged_observed.metadata["sandbox_observation"]
    )

    refresh_action = {
        **filesystem_action,
        "params": {**filesystem_action["params"], "refresh": True},
    }
    assert runtime.lookup(refresh_action, context=context) is None

    filesystem_head_action = {
        "tool_name": "filesystem",
        "action_name": "read_file",
        "params": {"path": str(source), "output": "text", "head": 2},
    }
    retained = runtime.lookup(filesystem_head_action, context=context)

    terminal_action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": f"head -n 2 {source}"},
    }
    assert runtime.lookup(terminal_action, context=context) is None
    base64_action = {
        "tool_name": "filesystem",
        "action_name": "read_file",
        "params": {
            "path": str(source),
            "output": "base64",
            "head": 2,
        },
    }
    assert runtime.lookup(base64_action, context=context) is None

    transformed_action = {
        "tool_name": "terminal",
        "action_name": "run_code",
        "params": {"code": f"wc -l {source}"},
    }
    assert runtime.lookup(transformed_action, context=context) is None

    assert retained is not None
    payload = json.loads(retained.content)
    assert payload["type"] == "unchanged"
    assert payload["coverage"] == {"kind": "line_range", "start": 1, "end": 2}
    assert retained.metadata["sandbox_observation"]["cache_validation"] == (
        "host_epoch_overlap"
    )

    context.context_lifecycle_state = _lifecycle(1)
    assert runtime.lookup(filesystem_head_action, context=context) is None


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
        execution_context_sha256=terminal_execution_context_sha256(sys.executable),
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
