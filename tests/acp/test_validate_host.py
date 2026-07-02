from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from aworld_cli.acp.validate_host import (
    build_validate_stdio_host_help,
    expand_env_placeholders,
    load_validate_stdio_host_config,
    parse_env_assignments,
    parse_env_json,
    parse_session_params,
    parse_startup_retries,
    parse_startup_timeout_seconds,
    render_validation_config,
    resolve_validate_stdio_host_request,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_parse_env_assignments_accepts_key_value_pairs() -> None:
    parsed = parse_env_assignments(["FOO=bar", "EMPTY=", "X=1=2"])

    assert parsed == {"FOO": "bar", "EMPTY": "", "X": "1=2"}


def test_parse_env_assignments_rejects_invalid_entries() -> None:
    with pytest.raises(ValueError):
        parse_env_assignments(["missing-separator"])


def test_parse_env_json_accepts_object_payload() -> None:
    parsed = parse_env_json('{"FOO":"bar","COUNT":2}')

    assert parsed == {"FOO": "bar", "COUNT": "2"}


def test_parse_env_json_rejects_non_object_payload() -> None:
    with pytest.raises(ValueError):
        parse_env_json('["bad"]')


def test_parse_session_params_defaults_and_accepts_object_payload() -> None:
    assert parse_session_params(None) == {"cwd": ".", "mcpServers": []}
    assert parse_session_params('{"cwd":"/tmp/session","mcpServers":["demo"]}') == {
        "cwd": "/tmp/session",
        "mcpServers": ["demo"],
    }


def test_parse_session_params_rejects_non_object_payload() -> None:
    with pytest.raises(ValueError):
        parse_session_params('["bad"]')


def test_parse_startup_timeout_seconds_validates_positive_values() -> None:
    assert parse_startup_timeout_seconds(None) is None
    assert parse_startup_timeout_seconds(0.5) == 0.5
    with pytest.raises(ValueError):
        parse_startup_timeout_seconds(0)


def test_parse_startup_retries_validates_non_negative_values() -> None:
    assert parse_startup_retries(0) == 0
    assert parse_startup_retries(2) == 2
    with pytest.raises(ValueError):
        parse_startup_retries(-1)


def test_expand_env_placeholders_expands_nested_values() -> None:
    expanded = expand_env_placeholders(
        {
            "cwd": "${ROOT}",
            "env": {"PYTHONPATH": "${ROOT}/src:${ROOT}"},
            "items": ["${ROOT}", 1],
        },
        {"ROOT": "/tmp/demo"},
    )

    assert expanded == {
        "cwd": "/tmp/demo",
        "env": {"PYTHONPATH": "/tmp/demo/src:/tmp/demo"},
        "items": ["/tmp/demo", 1],
    }


def test_expand_env_placeholders_rejects_missing_env() -> None:
    with pytest.raises(ValueError):
        expand_env_placeholders({"cwd": "${MISSING}"}, {})


def test_build_validate_stdio_host_help_reports_profiles_and_defaults() -> None:
    payload = build_validate_stdio_host_help()

    assert "self-test" in payload["profiles"]
    assert payload["defaultSessionParams"] == {"cwd": ".", "mcpServers": []}
    assert payload["configFileFields"] == [
        "topology",
        "command",
        "cwd",
        "profile",
        "sessionParams",
        "env",
        "startupTimeoutSeconds",
        "startupRetries",
    ]
    assert payload["defaultStartupRetries"] == 0
    assert payload["defaultStartupTimeoutSeconds"] is None
    assert [item["name"] for item in payload["topologies"]] == ["base", "distributed", "same-host"]
    assert payload["configAllowedFields"] == [
        "command",
        "cwd",
        "env",
        "profile",
        "sessionParams",
        "startupRetries",
        "startupTimeoutSeconds",
        "topology",
    ]
    assert payload["validateHostTopologyNames"] == ["base", "distributed", "same-host"]
    assert payload["configSchemaPath"].endswith("tests/integration/fixtures/acp_stdio_host_contract.schema.json")


def test_load_validate_stdio_host_config_reads_json_object(tmp_path: Path) -> None:
    config_path = tmp_path / "validate.json"
    config_path.write_text(
        json.dumps(
            {
                "command": "python -m demo_host",
                "cwd": "/tmp/demo",
                "profile": "self-test",
                "sessionParams": {"cwd": "/tmp/session", "mcpServers": []},
                "env": {"FOO": "bar"},
            }
        ),
        encoding="utf-8",
    )

    payload = load_validate_stdio_host_config(config_path)

    assert payload["command"] == "python -m demo_host"
    assert payload["env"] == {"FOO": "bar"}


def test_load_validate_stdio_host_config_expands_env_placeholders(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AWORLD_WORKSPACE", "/tmp/aworld")
    config_path = tmp_path / "validate.json"
    config_path.write_text(
        json.dumps(
            {
                "command": "python -m demo_host",
                "cwd": "${AWORLD_WORKSPACE}",
                "profile": "self-test",
                "sessionParams": {"cwd": "${AWORLD_WORKSPACE}", "mcpServers": []},
                "env": {"PYTHONPATH": "${AWORLD_WORKSPACE}/src:${AWORLD_WORKSPACE}"},
            }
        ),
        encoding="utf-8",
    )

    payload = load_validate_stdio_host_config(config_path)

    assert payload["cwd"] == "/tmp/aworld"
    assert payload["sessionParams"]["cwd"] == "/tmp/aworld"
    assert payload["env"]["PYTHONPATH"] == "/tmp/aworld/src:/tmp/aworld"


def test_load_validate_stdio_host_config_resolves_topology_template_from_config_file(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AWORLD_WORKSPACE", "/tmp/aworld")
    config_path = tmp_path / "validate.json"
    config_path.write_text(
        json.dumps(
            {
                "topology": "same-host",
                "env": {"EXTRA_FLAG": "1"},
            }
        ),
        encoding="utf-8",
    )

    payload = load_validate_stdio_host_config(config_path)

    assert payload["topology"] == "same-host"
    assert payload["cwd"] == "/tmp/aworld"
    assert payload["sessionParams"]["cwd"] == "/tmp/aworld"
    assert payload["env"]["EXTRA_FLAG"] == "1"
    assert payload["env"]["AWORLD_ACP_SELF_TEST_BRIDGE"] == "1"


def test_resolve_validate_stdio_host_request_merges_config_and_cli_overrides(tmp_path: Path) -> None:
    request = resolve_validate_stdio_host_request(
        config={
            "command": "python -m demo_host",
            "cwd": "/tmp/from-config",
            "profile": "self-test",
            "sessionParams": {"cwd": "/tmp/session", "mcpServers": ["cfg"]},
            "env": {"FROM_CONFIG": "1"},
            "startupTimeoutSeconds": 3.0,
            "startupRetries": 1,
        },
        topology=None,
        command=None,
        cwd=".",
        env_assignments=["FROM_ASSIGNMENT=3"],
        env_json='{"FROM_JSON":"2"}',
        profile_name="self-test",
        session_params_json=None,
        startup_timeout_seconds=None,
        startup_retries=0,
    )

    assert request["command"] == "python -m demo_host"
    assert request["cwd"] == "/tmp/from-config"
    assert request["profile_name"] == "self-test"
    assert request["session_params"] == {"cwd": "/tmp/session", "mcpServers": ["cfg"]}
    assert request["env"]["FROM_CONFIG"] == "1"
    assert request["env"]["FROM_JSON"] == "2"
    assert request["env"]["FROM_ASSIGNMENT"] == "3"
    assert request["startup_timeout_seconds"] == 3.0
    assert request["startup_retries"] == 1


def test_resolve_validate_stdio_host_request_uses_topology_resolved_from_config() -> None:
    request = resolve_validate_stdio_host_request(
        config={
            "topology": "same-host",
            "command": "python -m custom_host",
            "cwd": "/tmp/from-config",
            "profile": "self-test",
            "sessionParams": {"cwd": "/tmp/session", "mcpServers": []},
            "env": {"FROM_CONFIG": "1"},
        },
        topology=None,
        command=None,
        cwd=".",
        env_assignments=None,
        env_json=None,
        profile_name="self-test",
        session_params_json=None,
        startup_timeout_seconds=None,
        startup_retries=0,
    )

    assert request["command"] == "python -m custom_host"
    assert request["cwd"] == "/tmp/from-config"
    assert request["session_params"] == {"cwd": "/tmp/session", "mcpServers": []}
    assert request["env"]["FROM_CONFIG"] == "1"


def test_describe_validation_command_reports_machine_checkable_metadata() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aworld_cli.main",
            "--no-banner",
            "acp",
            "describe-validation",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(proc.stdout.strip())

    assert "self-test" in payload["profiles"]
    assert payload["defaultSessionParams"] == {"cwd": ".", "mcpServers": []}
    assert payload["configFileFields"] == [
        "topology",
        "command",
        "cwd",
        "profile",
        "sessionParams",
        "env",
        "startupTimeoutSeconds",
        "startupRetries",
    ]
    assert payload["defaultStartupRetries"] == 0
    assert payload["defaultStartupTimeoutSeconds"] is None
    assert [item["name"] for item in payload["topologies"]] == ["base", "distributed", "same-host"]
    assert payload["configAllowedFields"] == [
        "command",
        "cwd",
        "env",
        "profile",
        "sessionParams",
        "startupRetries",
        "startupTimeoutSeconds",
        "topology",
    ]
    assert payload["validateHostTopologyNames"] == ["base", "distributed", "same-host"]
    assert payload["configSchemaPath"].endswith("tests/integration/fixtures/acp_stdio_host_contract.schema.json")


def test_render_validation_config_command_reports_machine_checkable_payload() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aworld_cli.main",
            "--no-banner",
            "acp",
            "render-validation-config",
            "--topology",
            "distributed",
            "--expand-placeholders",
            "--env",
            "AWORLD_WORKER_WORKSPACE=/tmp/worker",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(proc.stdout)

    assert payload["topology"] == "distributed"
    assert payload["cwd"] == "/tmp/worker"


def test_render_validation_config_command_can_write_output_file(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)
    output_path = tmp_path / "rendered.json"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aworld_cli.main",
            "--no-banner",
            "acp",
            "render-validation-config",
            "--topology",
            "same-host",
            "--expand-placeholders",
            "--env",
            "AWORLD_WORKSPACE=/tmp/aworld",
            "--output-file",
            str(output_path),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    stdout_payload = json.loads(proc.stdout)
    file_payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert stdout_payload == file_payload
    assert file_payload["cwd"] == "/tmp/aworld"


def test_render_validation_config_returns_unexpanded_template_by_default() -> None:
    payload = render_validation_config(
        topology="same-host",
        expand_placeholders_flag=False,
        env_assignments=[],
    )

    assert payload["topology"] == "same-host"
    assert payload["cwd"] == "${AWORLD_WORKSPACE}"


def test_render_validation_config_can_expand_placeholders_from_overrides() -> None:
    payload = render_validation_config(
        topology="distributed",
        expand_placeholders_flag=True,
        env_assignments=["AWORLD_WORKER_WORKSPACE=/tmp/worker"],
    )

    assert payload["topology"] == "distributed"
    assert payload["cwd"] == "/tmp/worker"
    assert payload["env"]["PYTHONPATH"] == "/tmp/worker/aworld-cli/src:/tmp/worker"


def test_validate_stdio_host_command_reports_machine_checkable_summary() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aworld_cli.main",
            "--no-banner",
            "acp",
            "validate-stdio-host",
            "--command",
            f"{sys.executable} -m aworld_cli.main --no-banner acp",
            "--cwd",
            str(REPO_ROOT),
            "--profile",
            "self-test",
            "--session-params-json",
            '{"cwd":".","mcpServers":[]}',
            "--startup-timeout-seconds",
            "5",
            "--startup-retries",
            "0",
            "--env-json",
            '{"IGNORED":"1"}',
            "--env",
            f"PYTHONPATH={REPO_ROOT / 'aworld-cli' / 'src'}:{REPO_ROOT}",
            "--env",
            "AWORLD_ACP_SELF_TEST_BRIDGE=1",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(proc.stdout.strip())

    assert payload["ok"] is True
    assert payload["summary"]["failed"] == 0
    assert any(case["id"] == "tool_lifecycle_closes" for case in payload["cases"])


def test_validate_stdio_host_command_applies_explicit_session_params(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)

    session_cwd = tmp_path / "session-workspace"
    session_cwd.mkdir()

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aworld_cli.main",
            "--no-banner",
            "acp",
            "validate-stdio-host",
            "--command",
            f"{sys.executable} -m aworld_cli.main --no-banner acp",
            "--cwd",
            str(REPO_ROOT),
            "--profile",
            "self-test",
            "--session-params-json",
            json.dumps({"cwd": str(session_cwd), "mcpServers": []}),
            "--env",
            f"PYTHONPATH={REPO_ROOT / 'aworld-cli' / 'src'}:{REPO_ROOT}",
            "--env",
            "AWORLD_ACP_SELF_TEST_BRIDGE=1",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(proc.stdout.strip())
    tool_case = next(case for case in payload["cases"] if case["id"] == "tool_lifecycle_closes")

    assert payload["ok"] is True
    assert tool_case["detail"]["end"]["params"]["update"]["content"]["cwd"] == str(session_cwd)


def test_validate_stdio_host_command_accepts_config_file(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)

    config_path = tmp_path / "validate.json"
    config_path.write_text(
        json.dumps(
            {
                "command": f"{sys.executable} -m aworld_cli.main --no-banner acp",
                "cwd": str(REPO_ROOT),
                "profile": "self-test",
                "sessionParams": {"cwd": ".", "mcpServers": []},
                "env": {
                    "PYTHONPATH": f"{REPO_ROOT / 'aworld-cli' / 'src'}:{REPO_ROOT}",
                    "AWORLD_ACP_SELF_TEST_BRIDGE": "1",
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aworld_cli.main",
            "--no-banner",
            "acp",
            "validate-stdio-host",
            "--config-file",
            str(config_path),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(proc.stdout.strip())

    assert payload["ok"] is True
    assert any(case["id"] == "initialize_handshake" for case in payload["cases"])


def test_validate_stdio_host_command_accepts_topology_template() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aworld_cli.main",
            "--no-banner",
            "acp",
            "validate-stdio-host",
            "--topology",
            "same-host",
            "--env",
            f"AWORLD_WORKSPACE={REPO_ROOT}",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(proc.stdout.strip())

    assert payload["ok"] is True
    assert any(case["id"] == "initialize_handshake" for case in payload["cases"])


def test_validate_stdio_host_command_reports_machine_checkable_setup_error() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aworld_cli.main",
            "--no-banner",
            "acp",
            "validate-stdio-host",
            "--command",
            f"{sys.executable} -m aworld_cli.main --no-banner acp",
            "--profile",
            "self-test",
            "--env",
            "BROKEN_ENV",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(proc.stdout.strip())

    assert proc.returncode == 2
    assert payload["ok"] is False
    assert payload["cases"] == []
    assert payload["error"]["type"] == "ValueError"


def test_validate_stdio_host_command_reports_missing_placeholder_env_as_setup_error(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)

    config_path = tmp_path / "validate.json"
    config_path.write_text(
        json.dumps(
            {
                "command": f"{sys.executable} -m aworld_cli.main --no-banner acp",
                "cwd": "${AWORLD_WORKSPACE}",
                "profile": "self-test",
                "sessionParams": {"cwd": "${AWORLD_WORKSPACE}", "mcpServers": []},
                "env": {
                    "PYTHONPATH": "${AWORLD_WORKSPACE}/aworld-cli/src:${AWORLD_WORKSPACE}",
                    "AWORLD_ACP_SELF_TEST_BRIDGE": "1",
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aworld_cli.main",
            "--no-banner",
            "acp",
            "validate-stdio-host",
            "--config-file",
            str(config_path),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(proc.stdout.strip())

    assert proc.returncode == 2
    assert payload["ok"] is False
    assert payload["error"]["type"] == "ValueError"
    assert "AWORLD_WORKSPACE" in payload["error"]["message"]


def test_validate_stdio_host_command_rejects_config_file_and_topology_combo(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)
    config_path = tmp_path / "validate.json"
    config_path.write_text(
        json.dumps(
            {
                "command": f"{sys.executable} -m aworld_cli.main --no-banner acp",
                "cwd": str(REPO_ROOT),
                "profile": "self-test",
                "sessionParams": {"cwd": ".", "mcpServers": []},
                "env": {
                    "PYTHONPATH": f"{REPO_ROOT / 'aworld-cli' / 'src'}:{REPO_ROOT}",
                    "AWORLD_ACP_SELF_TEST_BRIDGE": "1",
                },
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aworld_cli.main",
            "--no-banner",
            "acp",
            "validate-stdio-host",
            "--config-file",
            str(config_path),
            "--topology",
            "same-host",
            "--env",
            f"AWORLD_WORKSPACE={REPO_ROOT}",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(proc.stdout.strip())

    assert proc.returncode == 2
    assert payload["ok"] is False
    assert "--config-file and --topology cannot be used together" in payload["error"]["message"]


def test_validate_stdio_host_command_reports_unknown_config_field_as_setup_error(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)

    config_path = tmp_path / "validate.json"
    config_path.write_text(
        json.dumps(
            {
                "command": f"{sys.executable} -m aworld_cli.main --no-banner acp",
                "cwd": str(REPO_ROOT),
                "profile": "self-test",
                "sessionParams": {"cwd": ".", "mcpServers": []},
                "env": {
                    "PYTHONPATH": f"{REPO_ROOT / 'aworld-cli' / 'src'}:{REPO_ROOT}",
                    "AWORLD_ACP_SELF_TEST_BRIDGE": "1",
                },
                "unexpected": True,
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aworld_cli.main",
            "--no-banner",
            "acp",
            "validate-stdio-host",
            "--config-file",
            str(config_path),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(proc.stdout.strip())

    assert proc.returncode == 2
    assert payload["ok"] is False
    assert payload["error"]["type"] == "ValueError"
    assert "unsupported field" in payload["error"]["message"]


def test_validate_stdio_host_command_reports_startup_timeout_as_setup_error() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "aworld-cli" / "src") + ":" + str(REPO_ROOT)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aworld_cli.main",
            "--no-banner",
            "acp",
            "validate-stdio-host",
            "--command",
            f"{sys.executable} -c \"import time; time.sleep(5)\"",
            "--startup-timeout-seconds",
            "0.2",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(proc.stdout.strip())

    assert proc.returncode == 2
    assert payload["ok"] is False
    assert "startup after 1 attempt" in payload["error"]["message"]
    assert "timed out waiting for a JSON line" in payload["error"]["message"]
