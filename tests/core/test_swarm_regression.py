# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Regression tests for Swarm functionality after Hybrid implementation.

This test suite ensures that the addition of Hybrid build type did not break
existing WORKFLOW, TEAM, and HANDOFF swarm types.
"""
import pytest
from aworld.config.conf import AgentConfig
from aworld.core.agent.swarm import Swarm, GraphBuildType
from aworld.agents.llm_agent import Agent
from aworld.logs.util import logger


class TestWorkflowSwarmRegression:
    """Regression tests for WORKFLOW build type."""

    def test_workflow_basic_creation(self):
        """Test basic workflow swarm creation."""
        agent1 = Agent(name="Agent1", conf=AgentConfig())
        agent2 = Agent(name="Agent2", conf=AgentConfig())
        agent3 = Agent(name="Agent3", conf=AgentConfig())

        # Create workflow swarm (sequential)
        swarm = Swarm(
            agent1, agent2, agent3,
            build_type=GraphBuildType.WORKFLOW
        )
        swarm.reset()

        # Verify build type
        assert swarm.build_type == GraphBuildType.WORKFLOW.value, \
            "Workflow build type should be preserved"

        # Verify topology
        assert len(swarm.agents) == 3, "Should have 3 agents"
        assert len(swarm.ordered_agents) == 3, "Should have 3 ordered agents"

        logger.info("✅ Workflow basic creation test passed")

    def test_workflow_parallel(self):
        """Test workflow with parallel execution."""
        agent1 = Agent(name="Start", conf=AgentConfig())
        agent2 = Agent(name="Parallel1", conf=AgentConfig())
        agent3 = Agent(name="Parallel2", conf=AgentConfig())
        agent4 = Agent(name="End", conf=AgentConfig())

        # Create workflow with parallel section
        swarm = Swarm(
            agent1,
            [agent2, agent3],  # Parallel execution
            agent4,
            build_type=GraphBuildType.WORKFLOW
        )
        swarm.reset()

        assert swarm.build_type == GraphBuildType.WORKFLOW.value
        assert len(swarm.agents) >= 3, "Should have at least 3 agents"

        logger.info("✅ Workflow parallel execution test passed")

    def test_workflow_no_peer_capability(self):
        """Test that workflow agents don't have peer capability."""
        agent1 = Agent(name="Agent1", conf=AgentConfig())
        agent2 = Agent(name="Agent2", conf=AgentConfig())

        swarm = Swarm(
            agent1, agent2,
            build_type=GraphBuildType.WORKFLOW
        )
        swarm.reset()

        # Verify agents don't have peer capability
        assert not agent1._is_peer_enabled, \
            "Workflow agents should NOT have peer capability"
        assert not agent2._is_peer_enabled, \
            "Workflow agents should NOT have peer capability"

        logger.info("✅ Workflow no peer capability test passed")


class TestTeamSwarmRegression:
    """Regression tests for TEAM build type."""

    def test_team_basic_creation(self):
        """Test basic team swarm creation."""
        coordinator = Agent(name="Coordinator", conf=AgentConfig())
        worker1 = Agent(name="Worker1", conf=AgentConfig())
        worker2 = Agent(name="Worker2", conf=AgentConfig())

        # Create team swarm (star topology)
        swarm = Swarm(
            coordinator,
            worker1,
            worker2,
            build_type=GraphBuildType.TEAM
        )
        swarm.reset()

        # Verify build type
        assert swarm.build_type == GraphBuildType.TEAM.value, \
            "Team build type should be preserved"

        # Verify star topology
        assert len(swarm.agents) == 3, "Should have 3 agents"
        root = swarm.agent_graph.root_agent
        assert root.name() == "Coordinator", "Root should be coordinator"

        # Verify handoffs
        assert len(coordinator.handoffs) == 2, \
            "Coordinator should have handoffs to 2 workers"

        logger.info("✅ Team basic creation test passed")

    def test_team_with_root_agent_param(self):
        """Test team swarm with explicit root_agent parameter."""
        coordinator = Agent(name="Coordinator", conf=AgentConfig())
        worker1 = Agent(name="Worker1", conf=AgentConfig())
        worker2 = Agent(name="Worker2", conf=AgentConfig())

        # Create team swarm with explicit root_agent
        swarm = Swarm(
            worker1,
            worker2,
            root_agent=coordinator,
            build_type=GraphBuildType.TEAM
        )
        swarm.reset()

        assert swarm.build_type == GraphBuildType.TEAM.value
        assert swarm.agent_graph.root_agent.name() == "Coordinator"

        logger.info("✅ Team with root_agent parameter test passed")

    def test_team_no_peer_capability(self):
        """Test that team agents don't have peer capability."""
        coordinator = Agent(name="Coordinator", conf=AgentConfig())
        worker1 = Agent(name="Worker1", conf=AgentConfig())
        worker2 = Agent(name="Worker2", conf=AgentConfig())

        swarm = Swarm(
            coordinator,
            worker1,
            worker2,
            build_type=GraphBuildType.TEAM
        )
        swarm.reset()

        # Verify agents don't have peer capability
        assert not coordinator._is_peer_enabled, \
            "Team coordinator should NOT have peer capability"
        assert not worker1._is_peer_enabled, \
            "Team workers should NOT have peer capability"
        assert not worker2._is_peer_enabled, \
            "Team workers should NOT have peer capability"

        logger.info("✅ Team no peer capability test passed")


class TestHandoffSwarmRegression:
    """Regression tests for HANDOFF build type."""

    def test_handoff_basic_creation(self):
        """Test basic handoff swarm creation."""
        agent1 = Agent(name="Agent1", conf=AgentConfig())
        agent2 = Agent(name="Agent2", conf=AgentConfig())
        agent3 = Agent(name="Agent3", conf=AgentConfig())

        # Create handoff swarm (all pairs)
        swarm = Swarm(
            (agent1, agent2),
            (agent2, agent3),
            (agent3, agent1),  # Cycle allowed
            build_type=GraphBuildType.HANDOFF
        )
        swarm.reset()

        # Verify build type
        assert swarm.build_type == GraphBuildType.HANDOFF.value, \
            "Handoff build type should be preserved"

        # Verify agents
        assert len(swarm.agents) == 3, "Should have 3 agents"

        # Handoff can have cycles
        assert swarm.has_cycle, "Handoff swarm can have cycles"

        logger.info("✅ Handoff basic creation test passed")

    def test_handoff_no_peer_capability(self):
        """Test that handoff agents don't have peer capability."""
        agent1 = Agent(name="Agent1", conf=AgentConfig())
        agent2 = Agent(name="Agent2", conf=AgentConfig())

        swarm = Swarm(
            (agent1, agent2),
            (agent2, agent1),
            build_type=GraphBuildType.HANDOFF
        )
        swarm.reset()

        # Verify agents don't have peer capability
        assert not agent1._is_peer_enabled, \
            "Handoff agents should NOT have peer capability"
        assert not agent2._is_peer_enabled, \
            "Handoff agents should NOT have peer capability"

        logger.info("✅ Handoff no peer capability test passed")


class TestBackwardCompatibility:
    """Test backward compatibility of Swarm API."""

    def test_default_build_type(self):
        """Test default build type is still WORKFLOW."""
        agent1 = Agent(name="Agent1", conf=AgentConfig())
        agent2 = Agent(name="Agent2", conf=AgentConfig())

        # Create swarm without explicit build_type
        swarm = Swarm(agent1, agent2)
        swarm.reset()

        assert swarm.build_type == GraphBuildType.WORKFLOW.value, \
            "Default build type should still be WORKFLOW"

        logger.info("✅ Default build type test passed")

    def test_swarm_without_reset(self):
        """Test swarm creation without immediate reset."""
        agent1 = Agent(name="Agent1", conf=AgentConfig())
        agent2 = Agent(name="Agent2", conf=AgentConfig())

        # Create swarm without reset (should not crash)
        swarm = Swarm(
            agent1, agent2,
            build_type=GraphBuildType.TEAM
        )

        assert swarm.agent_graph is None, \
            "Graph should be None before reset"

        # Reset should work normally
        swarm.reset()
        assert swarm.agent_graph is not None, \
            "Graph should exist after reset"

        logger.info("✅ Swarm without reset test passed")

    def test_existing_swarm_attributes(self):
        """Test that existing swarm attributes still exist."""
        agent1 = Agent(name="Agent1", conf=AgentConfig())
        agent2 = Agent(name="Agent2", conf=AgentConfig())

        swarm = Swarm(
            agent1, agent2,
            build_type=GraphBuildType.WORKFLOW
        )
        swarm.reset()

        # Check existing attributes
        assert hasattr(swarm, 'agents'), "Should have 'agents' attribute"
        assert hasattr(swarm, 'ordered_agents'), "Should have 'ordered_agents' attribute"
        assert hasattr(swarm, 'agent_graph'), "Should have 'agent_graph' attribute"
        assert hasattr(swarm, 'build_type'), "Should have 'build_type' attribute"
        assert hasattr(swarm, 'communicate_agent'), "Should have 'communicate_agent' attribute"

        logger.info("✅ Existing attributes test passed")


class TestBuildTypeEnum:
    """Test GraphBuildType enum integrity."""

    def test_enum_values_exist(self):
        """Test all expected enum values exist."""
        assert hasattr(GraphBuildType, 'WORKFLOW'), \
            "WORKFLOW enum should exist"
        assert hasattr(GraphBuildType, 'HANDOFF'), \
            "HANDOFF enum should exist"
        assert hasattr(GraphBuildType, 'TEAM'), \
            "TEAM enum should exist"
        assert hasattr(GraphBuildType, 'HYBRID'), \
            "HYBRID enum should exist (new)"

        logger.info("✅ Enum values exist test passed")

    def test_enum_string_values(self):
        """Test enum string values are correct."""
        assert GraphBuildType.WORKFLOW.value == "workflow"
        assert GraphBuildType.HANDOFF.value == "handoff"
        assert GraphBuildType.TEAM.value == "team"
        assert GraphBuildType.HYBRID.value == "hybrid"

        logger.info("✅ Enum string values test passed")


def run_all_tests():
    """Run all regression tests."""
    logger.info("="*80)
    logger.info("SWARM REGRESSION TESTS")
    logger.info("="*80)

    test_classes = [
        TestWorkflowSwarmRegression,
        TestTeamSwarmRegression,
        TestHandoffSwarmRegression,
        TestBackwardCompatibility,
        TestBuildTypeEnum,
    ]

    total_tests = 0
    passed_tests = 0
    failed_tests = []

    for test_class in test_classes:
        logger.info(f"\n{test_class.__name__}:")
        test_instance = test_class()

        # Get all test methods
        test_methods = [
            method for method in dir(test_instance)
            if method.startswith('test_') and callable(getattr(test_instance, method))
        ]

        for method_name in test_methods:
            total_tests += 1
            try:
                method = getattr(test_instance, method_name)
                method()
                passed_tests += 1
            except Exception as e:
                failed_tests.append((test_class.__name__, method_name, str(e)))
                logger.error(f"❌ {method_name} FAILED: {e}")

    logger.info("\n" + "="*80)
    logger.info("REGRESSION TEST SUMMARY")
    logger.info("="*80)
    logger.info(f"Total tests: {total_tests}")
    logger.info(f"Passed: {passed_tests}")
    logger.info(f"Failed: {len(failed_tests)}")

    if failed_tests:
        logger.error("\nFailed tests:")
        for class_name, method_name, error in failed_tests:
            logger.error(f"  - {class_name}.{method_name}: {error}")
        return False
    else:
        logger.info("\n✅ ALL REGRESSION TESTS PASSED")
        logger.info("Hybrid implementation did not break existing functionality!")
        return True


if __name__ == "__main__":
    import sys
    success = run_all_tests()
    sys.exit(0 if success else 1)
