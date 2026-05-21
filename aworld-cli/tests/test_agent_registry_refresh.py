import pytest

from aworld.core.agent.swarm import Swarm
from aworld_cli.core.agent_registry import LocalAgent


class DummySwarm(Swarm):
    def __init__(self, marker: str):
        self.marker = marker


@pytest.mark.asyncio
async def test_local_agent_refresh_rebuilds_swarm_factory() -> None:
    built = []

    def build_swarm() -> DummySwarm:
        marker = f"build-{len(built) + 1}"
        swarm = DummySwarm(marker)
        built.append(swarm)
        return swarm

    agent = LocalAgent(name="Aworld", desc="test", swarm=build_swarm)

    swarm1 = await agent.get_swarm()
    swarm2 = await agent.get_swarm()
    swarm3 = await agent.get_swarm(refresh=True)

    assert swarm1 is swarm2
    assert swarm3 is not swarm1
    assert [swarm.marker for swarm in built] == ["build-1", "build-2"]
