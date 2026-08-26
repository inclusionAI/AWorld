from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
DEPLOYMENT = ROOT / "deploy" / "aworld-cloud"


def test_compose_has_only_the_runnable_mvp_roles() -> None:
    compose = yaml.safe_load((DEPLOYMENT / "docker-compose.yml").read_text())
    services = compose["services"]

    assert set(services) == {
        "aworld-cloud-server",
        "aworld-cloud-worker",
        "aworld-cloud-cli",
    }
    assert services["aworld-cloud-cli"]["profiles"] == ["tools"]
    assert services["aworld-cloud-server"]["healthcheck"]
    assert services["aworld-cloud-worker"]["healthcheck"]
    assert any(
        mount.get("source") == "${AWORLD_CLOUD_DOCKER_SOCKET:-/var/run/docker.sock}"
        and mount.get("target") == "/var/run/docker.sock"
        for mount in services["aworld-cloud-worker"]["volumes"]
    )


def test_deployment_assets_pin_real_harbor_and_terminal_bench() -> None:
    dockerfile = (DEPLOYMENT / "Dockerfile").read_text()
    compose = (DEPLOYMENT / "docker-compose.yml").read_text()
    verifier = (ROOT / "scripts" / "verify-aworld-cloud-terminal-bench.sh").read_text()

    assert "FROM python:3.12.11-bookworm\n" in dockerfile
    assert "slim-bookworm" not in dockerfile
    assert "ccf3df5ef50141b004322d4008b84b64797b76b9" in dockerfile
    assert (
        "github.com/harbor-framework/harbor/archive/${HARBOR_COMMIT}.tar.gz"
        in dockerfile
    )
    assert "4c17f78744952ecafd09951d85e0065bc5fa1660aff591ffad2ee42ffee5e8c8" in (
        dockerfile
    )
    assert "sha256sum --check --strict" in dockerfile
    assert "apt-get" not in dockerfile
    assert "git clone" not in dockerfile
    assert "openssh-client" not in dockerfile
    assert "terminal-bench@2.0" in compose
    assert "fix-git" in compose
    assert "benchmark_outcome" in verifier
    assert 'reward"] == 1.0' in verifier
    assert "trajectory.atif.json" in verifier
    assert "result = json.load(sys.stdin)" in verifier
    assert "trajectory = json.load(sys.stdin)" in verifier
    assert '< "${artifacts}/${run_id}-result.json"' in verifier
    assert '< "${artifacts}/${run_id}-trajectory.atif.json"' in verifier
    assert 'data_directory="${AWORLD_CLOUD_DATA_DIR:-}"' in verifier


def test_compose_does_not_mount_host_codex_state() -> None:
    compose = (DEPLOYMENT / "docker-compose.yml").read_text()

    assert "~/.codex" not in compose
    assert "${HOME}/.codex" not in compose
