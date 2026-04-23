from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "tests" / "integration" / "fixtures"
TEMPLATE_PATHS = {
    "base": FIXTURES_DIR / "acp_stdio_host_contract.template.json",
    "same-host": FIXTURES_DIR / "acp_stdio_host_contract.same_host.template.json",
    "distributed": FIXTURES_DIR / "acp_stdio_host_contract.distributed.template.json",
}


def _validation_config_file() -> str | None:
    return os.environ.get("AWORLD_ACP_VALIDATION_CONFIG_FILE")


def _validation_topology() -> str | None:
    return os.environ.get("AWORLD_ACP_VALIDATION_TOPOLOGY")


def _validation_enabled() -> bool:
    return bool(_validation_config_file() or _validation_topology())


def _build_validation_command() -> list[str]:
    command = [
        sys.executable,
        "-m",
        "aworld_cli.main",
        "--no-banner",
        "acp",
        "validate-stdio-host",
    ]
    if _validation_config_file():
        command.extend(["--config-file", _validation_config_file() or ""])
        return command

    topology = _validation_topology()
    if topology:
        command.extend(["--topology", topology])
        return command

    raise ValueError(
        "manual ACP stdio host contract validation requires AWORLD_ACP_VALIDATION_CONFIG_FILE "
        "or AWORLD_ACP_VALIDATION_TOPOLOGY"
    )


def _required_template_fields(payload: dict[str, object]) -> None:
    assert payload["profile"] == "self-test"
    assert isinstance(payload["command"], str)
    assert isinstance(payload["cwd"], str)
    assert isinstance(payload["sessionParams"], dict)
    assert isinstance(payload["env"], dict)


def test_validation_command_prefers_config_file(monkeypatch) -> None:
    monkeypatch.setenv("AWORLD_ACP_VALIDATION_CONFIG_FILE", "/tmp/validate.json")
    monkeypatch.setenv("AWORLD_ACP_VALIDATION_TOPOLOGY", "same-host")

    assert _build_validation_command()[-2:] == ["--config-file", "/tmp/validate.json"]


def test_validation_command_supports_topology_only(monkeypatch) -> None:
    monkeypatch.delenv("AWORLD_ACP_VALIDATION_CONFIG_FILE", raising=False)
    monkeypatch.setenv("AWORLD_ACP_VALIDATION_TOPOLOGY", "distributed")

    assert _build_validation_command()[-2:] == ["--topology", "distributed"]


def test_acp_stdio_host_contract_templates_are_machine_readable() -> None:
    loaded = {
        name: json.loads(path.read_text(encoding="utf-8"))
        for name, path in TEMPLATE_PATHS.items()
    }

    for payload in loaded.values():
        _required_template_fields(payload)

    assert "topology" not in loaded["base"]
    assert loaded["same-host"]["topology"] == "same-host"
    assert loaded["distributed"]["topology"] == "distributed"
    assert "${AWORLD_WORKSPACE}" in loaded["same-host"]["cwd"]
    assert "${AWORLD_WORKER_WORKSPACE}" in loaded["distributed"]["cwd"]


@pytest.mark.skipif(
    not _validation_enabled(),
    reason=(
        "manual ACP stdio host contract validation; set AWORLD_ACP_VALIDATION_CONFIG_FILE "
        "or AWORLD_ACP_VALIDATION_TOPOLOGY to enable"
    ),
)
def test_external_stdio_host_satisfies_phase1_contract_via_configured_cli_entrypoint() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)

    proc = subprocess.run(
        _build_validation_command(),
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(proc.stdout.strip())
    assert payload["ok"] is True, payload
