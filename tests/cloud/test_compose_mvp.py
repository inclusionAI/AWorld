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
        mount.get("source") == "/var/run/docker.sock"
        for mount in services["aworld-cloud-worker"]["volumes"]
    )


def test_deployment_assets_pin_real_harbor_and_terminal_bench() -> None:
    dockerfile = (DEPLOYMENT / "Dockerfile").read_text()
    compose = (DEPLOYMENT / "docker-compose.yml").read_text()
    verifier = (ROOT / "scripts" / "verify-aworld-cloud-terminal-bench.sh").read_text()

    assert "ccf3df5ef50141b004322d4008b84b64797b76b9" in dockerfile
    assert "github.com/harbor-framework/harbor.git" in dockerfile
    assert "terminal-bench@2.0" in compose
    assert "fix-git" in compose
    assert "benchmark_outcome" in verifier
    assert 'reward"] == 1.0' in verifier
    assert "trajectory.atif.json" in verifier
