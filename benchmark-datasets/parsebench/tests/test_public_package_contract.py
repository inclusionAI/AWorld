from __future__ import annotations

import tomllib

from parsebench_dataset import dataset, package_contract
from parsebench_dataset.contracts import PINNED_PARSEBENCH_RUNTIME_IMAGE


def test_default_task_environments_use_a_public_base_and_independent_scorer():
    base = PINNED_PARSEBENCH_RUNTIME_IMAGE
    assert base.startswith("python:3.12-slim-bookworm@sha256:")
    agent = package_contract.agent_dockerfile(runtime_image=base).decode()
    verifier = package_contract.verifier_dockerfile(runtime_image=base).decode()
    for dockerfile in (agent, verifier):
        assert dockerfile.startswith(f"FROM {base}\n")
        assert "aworld-filex-parsebench" not in dockerfile
        assert "aworld-wheels" not in dockerfile
    assert "poppler-utils" in agent
    assert "COPY scorer/" in verifier
    assert "install_scorer.py" in verifier
    assert "--require-hashes" in verifier
    assert "COPY parsebench-scope.json /tests/parsebench-scope.json" in verifier
    assert set(dataset._scorer_build_files()) == {
        "install_scorer.py",
        "requirements.txt",
        "build-requirements.txt",
        "AWORLD_SCORER_BUNDLE_MANIFEST.json",
        "VENDORED.md",
    }


def test_public_submission_contract_has_no_solver_requirement():
    instruction = package_contract.instruction(
        source_runtime_path="/workspace/input/document.pdf", page=5
    ).decode()
    assert "one-indexed page 5" in instruction
    assert "layout_pages" in instruction and "document.md" in instruction
    assert "accurate `rowspan`/`colspan`" in instruction
    assert "each data point in its own value cell" in instruction
    assert "A prose chart summary alone" in instruction
    assert "superscript, subscript" in instruction
    assert "use only observed page geometry" in instruction
    assert not any(
        value in instruction.lower()
        for value in ("aworld", "filex", "paddle", "result.json")
    )
    assert {item["source"] for item in package_contract.artifact_specs()} == {
        "/logs/artifacts/document.md",
        "/logs/artifacts/layout.json",
    }
    task_config = tomllib.loads(package_contract.task_toml().decode())
    assert task_config["artifacts"] == [
        "/logs/artifacts/document.md",
        "/logs/artifacts/layout.json",
    ]


def test_scorer_build_recipe_uses_only_fixed_public_inputs():
    files = dataset._scorer_build_files()
    installer = files["install_scorer.py"].decode()
    assert "https://codeload.github.com/run-llama/parsebench/" in installer
    assert (
        "44f21b59e97c955633cf9d1f5e7ded5227c4fba8d5fa58e88acededa6c3c7723" in installer
    )
    assert "sha256" in files["requirements.txt"].decode()
    assert "sha256" in files["build-requirements.txt"].decode()
