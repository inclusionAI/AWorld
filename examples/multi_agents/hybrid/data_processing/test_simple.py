# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Simplified Hybrid Mode Architecture Test (No LLM Required)

This script tests the Hybrid swarm architecture at the framework level:
- Verifies HybridBuilder correctly creates star topology
- Verifies peer capability is enabled for executors
- Verifies peer API methods work (share_with_peer, broadcast_to_all_peers)
- Compares Team vs Hybrid peer enablement

No LLM calls, pure architecture validation.
"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from aworld.config.conf import AgentConfig
from aworld.core.agent.swarm import HybridSwarm, TeamSwarm
from aworld.agents.llm_agent import Agent
from aworld.logs.util import logger


def test_hybrid_builder():
    """Test HybridBuilder creates correct topology."""
    logger.info("\n" + "="*80)
    logger.info("TEST 1: HybridBuilder Topology Construction")
    logger.info("="*80)

    # Create simple agents
    coordinator = Agent(
        name="Coordinator",
        conf=AgentConfig(),
        desc="Root coordinator"
    )

    agent1 = Agent(
        name="Agent1",
        conf=AgentConfig(),
        desc="Executor 1"
    )

    agent2 = Agent(
        name="Agent2",
        conf=AgentConfig(),
        desc="Executor 2"
    )

    agent3 = Agent(
        name="Agent3",
        conf=AgentConfig(),
        desc="Executor 3"
    )

    # Create Hybrid swarm using HybridSwarm alias
    hybrid_swarm = HybridSwarm(
        coordinator,
        agent1,
        agent2,
        agent3
    )

    # Initialize swarm (builds the graph)
    hybrid_swarm.reset()

    # Verify topology
    logger.info(f"Build type: {hybrid_swarm.build_type}")
    logger.info(f"Root agent: {hybrid_swarm.agent_graph.root_agent.name()}")
    logger.info(f"Total agents: {len(hybrid_swarm.agents)}")

    # Verify star topology (root has 3 successors)
    root_id = coordinator.id()
    successors = hybrid_swarm.agent_graph.successor.get(root_id, {})
    logger.info(f"Root successors: {len(successors)} (expected: 3)")

    assert len(successors) == 3, f"Expected 3 successors, got {len(successors)}"
    logger.info("✅ Star topology verified")

    # Verify peer capability enabled for executors
    logger.info("\nPeer Capability Check:")
    logger.info(f"  Coordinator._is_peer_enabled: {coordinator._is_peer_enabled}")
    logger.info(f"  Agent1._is_peer_enabled: {agent1._is_peer_enabled}")
    logger.info(f"  Agent2._is_peer_enabled: {agent2._is_peer_enabled}")
    logger.info(f"  Agent3._is_peer_enabled: {agent3._is_peer_enabled}")

    assert not coordinator._is_peer_enabled, "Coordinator should NOT have peer capability"
    assert agent1._is_peer_enabled, "Agent1 should have peer capability"
    assert agent2._is_peer_enabled, "Agent2 should have peer capability"
    assert agent3._is_peer_enabled, "Agent3 should have peer capability"
    logger.info("✅ Peer capability correctly assigned")

    # Verify peer references
    logger.info("\nPeer References:")
    logger.info(f"  Agent1 peers: {[p.name() for p in agent1._peer_agents.values()]}")
    logger.info(f"  Agent2 peers: {[p.name() for p in agent2._peer_agents.values()]}")
    logger.info(f"  Agent3 peers: {[p.name() for p in agent3._peer_agents.values()]}")

    assert len(agent1._peer_agents) == 2, "Agent1 should have 2 peers"
    assert len(agent2._peer_agents) == 2, "Agent2 should have 2 peers"
    assert len(agent3._peer_agents) == 2, "Agent3 should have 2 peers"
    logger.info("✅ Peer references correctly set (full mesh among executors)")

    logger.info("\n" + "="*80)
    logger.info("TEST 1 PASSED: HybridBuilder works correctly")
    logger.info("="*80)


def test_team_vs_hybrid():
    """Compare Team vs Hybrid peer enablement."""
    logger.info("\n" + "="*80)
    logger.info("TEST 2: Team vs Hybrid Comparison")
    logger.info("="*80)

    # Create agents
    coord_team = Agent(name="CoordTeam", conf=AgentConfig())
    agent_team_1 = Agent(name="TeamAgent1", conf=AgentConfig())
    agent_team_2 = Agent(name="TeamAgent2", conf=AgentConfig())

    coord_hybrid = Agent(name="CoordHybrid", conf=AgentConfig())
    agent_hybrid_1 = Agent(name="HybridAgent1", conf=AgentConfig())
    agent_hybrid_2 = Agent(name="HybridAgent2", conf=AgentConfig())

    # Create Team swarm using TeamSwarm alias
    team_swarm = TeamSwarm(
        coord_team,
        agent_team_1,
        agent_team_2
    )
    team_swarm.reset()

    # Create Hybrid swarm using HybridSwarm alias
    hybrid_swarm = HybridSwarm(
        coord_hybrid,
        agent_hybrid_1,
        agent_hybrid_2
    )
    hybrid_swarm.reset()

    # Compare peer enablement
    logger.info("\nTeam Mode:")
    logger.info(f"  TeamAgent1._is_peer_enabled: {agent_team_1._is_peer_enabled}")
    logger.info(f"  TeamAgent2._is_peer_enabled: {agent_team_2._is_peer_enabled}")

    logger.info("\nHybrid Mode:")
    logger.info(f"  HybridAgent1._is_peer_enabled: {agent_hybrid_1._is_peer_enabled}")
    logger.info(f"  HybridAgent2._is_peer_enabled: {agent_hybrid_2._is_peer_enabled}")

    # Verify
    assert not agent_team_1._is_peer_enabled, "Team agents should NOT have peer capability"
    assert not agent_team_2._is_peer_enabled, "Team agents should NOT have peer capability"
    assert agent_hybrid_1._is_peer_enabled, "Hybrid agents SHOULD have peer capability"
    assert agent_hybrid_2._is_peer_enabled, "Hybrid agents SHOULD have peer capability"

    logger.info("\n✅ Key Difference: Team = no peer capability, Hybrid = peer capability enabled")

    logger.info("\n" + "="*80)
    logger.info("TEST 2 PASSED: Team vs Hybrid correctly differentiated")
    logger.info("="*80)


async def test_peer_api():
    """Test peer API methods work without errors."""
    logger.info("\n" + "="*80)
    logger.info("TEST 3: Peer API Functionality")
    logger.info("="*80)

    # Create Hybrid swarm
    coordinator = Agent(name="Coordinator", conf=AgentConfig())
    agent1 = Agent(name="Agent1", conf=AgentConfig())
    agent2 = Agent(name="Agent2", conf=AgentConfig())
    agent3 = Agent(name="Agent3", conf=AgentConfig())

    hybrid_swarm = HybridSwarm(
        coordinator,
        agent1,
        agent2,
        agent3
    )
    hybrid_swarm.reset()

    # Mock context (required for peer communication)
    from aworld.core.context.base import Context
    from aworld.events.manager import EventManager

    context = Context()
    context._task_id = "test_task_123"
    context._session_id = "test_session_456"
    context._event_manager = EventManager(context)

    # Mock task.id for EventManager
    class MockTask:
        def __init__(self):
            self.id = "test_task_123"

    context._task = MockTask()

    # Inject context into agents
    agent1._current_context = context
    agent2._current_context = context
    agent3._current_context = context

    logger.info("\nTesting share_with_peer():")
    try:
        result = await agent1.share_with_peer(
            peer_name="Agent2",
            information={"stage": "test", "data": [1, 2, 3]},
            info_type="test_data"
        )
        logger.info(f"  Agent1 → Agent2: {result}")
        assert result is True, "share_with_peer should return True"
        logger.info("  ✅ share_with_peer() works")
    except Exception as e:
        logger.error(f"  ❌ share_with_peer() failed: {e}")
        raise

    logger.info("\nTesting broadcast_to_all_peers():")
    try:
        count = await agent1.broadcast_to_all_peers(
            information={"status": "complete", "value": 42},
            info_type="status"
        )
        logger.info(f"  Agent1 broadcast to {count} peers")
        assert count == 2, f"Expected 2 peers, got {count}"
        logger.info("  ✅ broadcast_to_all_peers() works")
    except Exception as e:
        logger.error(f"  ❌ broadcast_to_all_peers() failed: {e}")
        raise

    logger.info("\nTesting error handling (invalid peer name):")
    try:
        await agent1.share_with_peer(
            peer_name="NonExistentAgent",
            information={"test": "data"}
        )
        logger.error("  ❌ Should have raised ValueError")
        assert False, "Expected ValueError for invalid peer name"
    except ValueError as e:
        logger.info(f"  ✅ Correctly raised ValueError: {e}")

    logger.info("\nTesting error handling (not in Hybrid swarm):")
    non_hybrid_agent = Agent(name="NonHybridAgent", conf=AgentConfig())
    try:
        await non_hybrid_agent.share_with_peer(
            peer_name="SomeAgent",
            information={"test": "data"}
        )
        logger.error("  ❌ Should have raised RuntimeError")
        assert False, "Expected RuntimeError for non-Hybrid agent"
    except RuntimeError as e:
        logger.info(f"  ✅ Correctly raised RuntimeError: {e}")

    logger.info("\n" + "="*80)
    logger.info("TEST 3 PASSED: Peer API works correctly")
    logger.info("="*80)


def main():
    """Run all tests."""
    logger.info("="*80)
    logger.info("HYBRID MODE ARCHITECTURE VALIDATION")
    logger.info("="*80)

    try:
        # Test 1: HybridBuilder topology
        test_hybrid_builder()

        # Test 2: Team vs Hybrid comparison
        test_team_vs_hybrid()

        # Test 3: Peer API functionality (async)
        asyncio.run(test_peer_api())

        logger.info("\n" + "="*80)
        logger.info("✅ ALL TESTS PASSED")
        logger.info("="*80)
        logger.info("""
Summary:
1. HybridBuilder correctly creates star topology
2. Peer capability is enabled only for Hybrid executors (not Team)
3. Peer API methods (share_with_peer, broadcast_to_all_peers) work correctly
4. Error handling works as expected

Hybrid architecture is ready for use!
""")

    except AssertionError as e:
        logger.error(f"\n❌ TEST FAILED: {e}")
        raise
    except Exception as e:
        import traceback
        logger.error(f"\n❌ UNEXPECTED ERROR: {e}")
        logger.error(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
