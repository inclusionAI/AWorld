from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aworld_cli.core.builtin_skills import (
    AWORLD_DEFAULT_SKILL_NAMES,
    get_builtin_skills_path,
)
from aworld_cli.core.skill_activation_resolver import (
    SkillActivationResolver,
    SkillResolverRequest,
)

from aworld.skills.filesystem_provider import FilesystemSkillProvider


REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_workbench(
    script: Path,
    env: dict[str, str],
    *arguments: str,
    check: bool = True,
) -> dict:
    completed = subprocess.run(
        [sys.executable, str(script), *arguments],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = completed.stdout if completed.returncode == 0 else completed.stderr
    payload = json.loads(output.splitlines()[-1])
    if check:
        assert completed.returncode == 0, output
        assert payload["success"] is True
    return payload


def test_builtin_workbench_requires_explicit_selection() -> None:
    implicit = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=(),
            runtime_scope="session",
            task_text="Use Workbench to validate and promote the candidate.",
            default_skill_names=AWORLD_DEFAULT_SKILL_NAMES,
        )
    )

    assert "workbench" not in AWORLD_DEFAULT_SKILL_NAMES
    assert "workbench" not in implicit.available_skill_names
    assert "workbench" not in implicit.active_skill_names

    explicit = SkillActivationResolver().resolve(
        SkillResolverRequest(
            plugin_roots=(),
            runtime_scope="session",
            task_text="Complete the requested automation.",
            requested_skill_names=("workbench",),
            default_skill_names=AWORLD_DEFAULT_SKILL_NAMES,
        )
    )

    assert explicit.active_skill_names == ("long-running-agent", "workbench")
    assert explicit.skill_configs["workbench"]["active"] is True


def test_builtin_workbench_declares_self_contained_cli_assets() -> None:
    root = get_builtin_skills_path()
    provider = FilesystemSkillProvider("builtin-test", root)
    descriptor = next(
        item for item in provider.list_descriptors() if item.skill_name == "workbench"
    )

    assert descriptor.metadata["default_enabled"] is False
    assert descriptor.execution_assets["relative_paths"] == [
        "scripts/workbench.py",
        "scripts/workbench_runtime/__init__.py",
        "scripts/workbench_runtime/_process.py",
        "scripts/workbench_runtime/_process_child.py",
        "scripts/workbench_runtime/session.py",
        "scripts/workbench_runtime/store.py",
        "scripts/workbench_runtime/store_inputs.py",
        "scripts/workbench_runtime/store_io.py",
        "scripts/workbench_runtime/validation.py",
    ]
    assert (root / "workbench" / "SKILL.md").is_file()
    assert (root / "workbench" / "scripts" / "workbench.py").is_file()


def test_builtin_workbench_skill_exposes_only_the_fixed_explicit_cli() -> None:
    skill_root = get_builtin_skills_path() / "workbench"
    skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    normalized = skill.lower()
    compacted = " ".join(normalized.split())

    assert '"$AWORLD_PYTHON_EXECUTABLE" /skills/workbench/scripts/workbench.py' in skill
    assert "default_enabled: false" in skill
    assert "agent self-check evidence only" in normalized
    assert "ordinary environment variables" in compacted
    assert "not a security boundary" in compacted
    assert "compact by default" in normalized
    assert "terminal-bench" not in normalized
    assert "parsebench" not in normalized


def test_builtin_workbench_cli_requires_runtime_configuration(
    tmp_path: Path,
) -> None:
    script = get_builtin_skills_path() / "workbench" / "scripts" / "workbench.py"
    state = tmp_path / "state"
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("WORKBENCH_") and key != "AWORLD_PYTHON_EXECUTABLE"
    }
    env["WORKBENCH_STATE_ROOT"] = str(state)
    env["WORKBENCH_SCOPE_ID"] = "aworld-test-scope"

    payload = _run_workbench(script, env, "inspect", check=False)

    assert payload["success"] is False
    assert payload["authority"] == "agent_self_check"
    assert payload["task_reward"] == "not_assessed"
    assert "WORKBENCH_WORKSPACE_ROOT" in payload["error"]
    assert not state.exists()


def test_builtin_workbench_cli_candidate_lifecycle_uses_explicit_contract(
    tmp_path: Path,
) -> None:
    script = get_builtin_skills_path() / "workbench" / "scripts" / "workbench.py"
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    workspace.mkdir()
    env = {
        **os.environ,
        "AWORLD_DISABLE_AUTO_DOTENV": "1",
        "AWORLD_PYTHON_EXECUTABLE": sys.executable,
        "PYTHONPATH": os.pathsep.join(
            value
            for value in (str(REPO_ROOT), os.environ.get("PYTHONPATH", ""))
            if value
        ),
        "WORKBENCH_WORKSPACE_ROOT": str(workspace),
        "WORKBENCH_STATE_ROOT": str(state),
        "WORKBENCH_SCOPE_ID": "aworld-test-scope",
    }
    output = workspace / "result.txt"
    contract = workspace / "contract.json"
    contract.write_text(
        json.dumps(
            {
                "schema_version": "workbench.contract/v1",
                "authority": "agent_self_check",
                "outputs": [
                    {
                        "id": "result",
                        "path": str(output),
                        "checks": [
                            {
                                "id": "result-nonempty",
                                "kind": "nonempty",
                                "path": str(output),
                            }
                        ],
                    }
                ],
                "inputs": [],
                "checks": [],
                "policy": {
                    "mandatory_checks": ["result-nonempty"],
                    "hard_constraints": [],
                },
            }
        ),
        encoding="utf-8",
    )
    initialized = _run_workbench(script, env, "init", "--contract", str(contract))
    assert initialized["authority"] == "agent_self_check"
    assert initialized["task_reward"] == "not_assessed"

    draft = workspace / "draft.txt"
    draft.write_text("verified candidate", encoding="utf-8")
    manifest = workspace / "candidate.json"
    manifest.write_text(
        json.dumps({"files": {str(output): str(draft)}}),
        encoding="utf-8",
    )
    saved = _run_workbench(script, env, "save-candidate", "--manifest", str(manifest))
    candidate_id = saved["result"]["candidate_id"]
    validated = _run_workbench(
        script, env, "validate-candidate", "--candidate-id", candidate_id
    )
    promoted = _run_workbench(
        script,
        env,
        "promote-candidate",
        "--candidate-id",
        candidate_id,
        "--receipt-id",
        validated["result"]["receipt_id"],
    )

    assert promoted["result"]["promoted"] is True
    assert output.read_text(encoding="utf-8") == "verified candidate"
    inspected = _run_workbench(script, env, "inspect")
    assert len(json.dumps(inspected).encode("utf-8")) < 128 * 1024
