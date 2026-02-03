# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""Unit tests for YAML-based Swarm builder."""

import pytest
import tempfile
from pathlib import Path

from aworld.core.agent.base import Agent
from aworld.core.agent.swarm_builder import (
    build_swarm_from_yaml,
    build_swarm_from_dict,
    SwarmConfigValidator,
)
from aworld.core.agent.swarm import WorkflowSwarm, TeamSwarm, HandoffSwarm
from aworld.core.exceptions import AWorldRuntimeException


def create_test_agent(agent_id: str) -> Agent:
    """Create a test agent."""
    return Agent(name=agent_id, desc=f"Test agent {agent_id}")


class TestSwarmConfigValidator:
    """Test configuration validation."""
    
    def test_valid_workflow_config(self):
        """Test valid workflow configuration."""
        config = {
            "swarm": {
                "type": "workflow",
                "agents": [
                    {"id": "agent1", "next": "agent2"},
                    {"id": "agent2"},
                ]
            }
        }
        # Should not raise
        SwarmConfigValidator.validate_config(config)
    
    def test_missing_swarm_key(self):
        """Test missing swarm key."""
        config = {"agents": []}
        with pytest.raises(AWorldRuntimeException, match="Missing 'swarm' key"):
            SwarmConfigValidator.validate_config(config)
    
    def test_invalid_swarm_type(self):
        """Test invalid swarm type."""
        config = {
            "swarm": {
                "type": "invalid_type",
                "agents": [{"id": "agent1"}]
            }
        }
        with pytest.raises(AWorldRuntimeException, match="Invalid swarm type"):
            SwarmConfigValidator.validate_config(config)
    
    def test_duplicate_agent_id(self):
        """Test duplicate agent IDs."""
        config = {
            "swarm": {
                "type": "workflow",
                "agents": [
                    {"id": "agent1"},
                    {"id": "agent1"},
                ]
            }
        }
        with pytest.raises(AWorldRuntimeException, match="Duplicate agent id"):
            SwarmConfigValidator.validate_config(config)
    
    def test_parallel_without_agents_field(self):
        """Test parallel node without agents field."""
        config = {
            "swarm": {
                "type": "workflow",
                "agents": [
                    {"id": "parallel1", "node_type": "parallel"},
                ]
            }
        }
        with pytest.raises(AWorldRuntimeException, match="must have 'agents' field"):
            SwarmConfigValidator.validate_config(config)


class TestSimpleWorkflow:
    """Test simple workflow building."""
    
    def test_simple_workflow_from_dict(self):
        """Test building simple workflow from dict."""
        config = {
            "swarm": {
                "name": "test_workflow",
                "type": "workflow",
                "agents": [
                    {"id": "agent1", "next": "agent2"},
                    {"id": "agent2", "next": "agent3"},
                    {"id": "agent3"},
                ]
            }
        }
        
        agents_dict = {
            "agent1": create_test_agent("agent1"),
            "agent2": create_test_agent("agent2"),
            "agent3": create_test_agent("agent3"),
        }
        
        swarm = build_swarm_from_dict(config, agents_dict)
        
        assert isinstance(swarm, WorkflowSwarm)
        assert swarm.name() == "test_workflow"
        
        # Initialize to check topology
        swarm.reset("test task")
        assert len(swarm.ordered_agents) == 3
    
    def test_workflow_with_explicit_edges(self):
        """Test workflow with explicit edges."""
        config = {
            "swarm": {
                "type": "workflow",
                "agents": [
                    {"id": "agent1"},
                    {"id": "agent2"},
                    {"id": "agent3"},
                ],
                "edges": [
                    {"from": "agent1", "to": "agent2"},
                    {"from": "agent2", "to": "agent3"},
                ]
            }
        }
        
        agents_dict = {
            "agent1": create_test_agent("agent1"),
            "agent2": create_test_agent("agent2"),
            "agent3": create_test_agent("agent3"),
        }
        
        swarm = build_swarm_from_dict(config, agents_dict)
        swarm.reset("test task")
        
        assert len(swarm.ordered_agents) == 3


class TestParallelWorkflow:
    """Test parallel workflow building."""
    
    def test_parallel_group(self):
        """Test parallel agent group."""
        config = {
            "swarm": {
                "type": "workflow",
                "agents": [
                    {"id": "start", "next": "parallel_group"},
                    {
                        "id": "parallel_group",
                        "node_type": "parallel",
                        "agents": ["task1", "task2"],
                        "next": "end"
                    },
                    {"id": "task1"},
                    {"id": "task2"},
                    {"id": "end"},
                ]
            }
        }
        
        agents_dict = {
            "start": create_test_agent("start"),
            "task1": create_test_agent("task1"),
            "task2": create_test_agent("task2"),
            "end": create_test_agent("end"),
        }
        
        swarm = build_swarm_from_dict(config, agents_dict)
        swarm.reset("test task")
        
        # Check that parallel group was created
        assert len(swarm.ordered_agents) == 3  # start, parallel_group, end


class TestSerialWorkflow:
    """Test serial workflow building."""
    
    def test_serial_group(self):
        """Test serial agent group."""
        config = {
            "swarm": {
                "type": "workflow",
                "agents": [
                    {"id": "start", "next": "serial_group"},
                    {
                        "id": "serial_group",
                        "node_type": "serial",
                        "agents": ["step1", "step2"],
                        "next": "end"
                    },
                    {"id": "step1"},
                    {"id": "step2"},
                    {"id": "end"},
                ]
            }
        }
        
        agents_dict = {
            "start": create_test_agent("start"),
            "step1": create_test_agent("step1"),
            "step2": create_test_agent("step2"),
            "end": create_test_agent("end"),
        }
        
        swarm = build_swarm_from_dict(config, agents_dict)
        swarm.reset("test task")
        
        # Check that serial group was created
        assert len(swarm.ordered_agents) == 3  # start, serial_group, end


class TestTeamSwarm:
    """Test team swarm building."""
    
    def test_team_swarm(self):
        """Test team swarm with coordinator."""
        config = {
            "swarm": {
                "type": "team",
                "root_agent": "coordinator",
                "agents": [
                    {"id": "coordinator", "next": ["worker1", "worker2"]},
                    {"id": "worker1"},
                    {"id": "worker2"},
                ]
            }
        }
        
        agents_dict = {
            "coordinator": create_test_agent("coordinator"),
            "worker1": create_test_agent("worker1"),
            "worker2": create_test_agent("worker2"),
        }
        
        swarm = build_swarm_from_dict(config, agents_dict)
        
        assert isinstance(swarm, TeamSwarm)
        
        swarm.reset("test task")
        
        # Root agent should have handoffs to workers
        root = swarm.communicate_agent
        assert len(root.handoffs) >= 2


class TestHandoffSwarm:
    """Test handoff swarm building."""
    
    def test_handoff_swarm(self):
        """Test handoff swarm with explicit edges."""
        config = {
            "swarm": {
                "type": "handoff",
                "root_agent": "agent1",
                "agents": [
                    {"id": "agent1"},
                    {"id": "agent2"},
                    {"id": "agent3"},
                ],
                "edges": [
                    {"from": "agent1", "to": "agent2"},
                    {"from": "agent2", "to": "agent3"},
                    {"from": "agent3", "to": "agent1"},  # Cycle allowed
                ]
            }
        }
        
        agents_dict = {
            "agent1": create_test_agent("agent1"),
            "agent2": create_test_agent("agent2"),
            "agent3": create_test_agent("agent3"),
        }
        
        swarm = build_swarm_from_dict(config, agents_dict)
        
        assert isinstance(swarm, HandoffSwarm)
        
        swarm.reset("test task")
        
        # Handoff swarm can have cycles
        assert swarm.has_cycle


class TestNestedSwarm:
    """Test nested swarm building."""
    
    def test_nested_team_in_workflow(self):
        """Test nested team swarm within workflow."""
        config = {
            "swarm": {
                "type": "workflow",
                "agents": [
                    {"id": "preprocessor", "next": "team"},
                    {
                        "id": "team",
                        "node_type": "swarm",
                        "swarm_type": "team",
                        "root_agent": "leader",
                        "agents": [
                            {"id": "leader", "next": ["worker1", "worker2"]},
                            {"id": "worker1"},
                            {"id": "worker2"},
                        ],
                        "next": "postprocessor"
                    },
                    {"id": "postprocessor"},
                ]
            }
        }
        
        agents_dict = {
            "preprocessor": create_test_agent("preprocessor"),
            "leader": create_test_agent("leader"),
            "worker1": create_test_agent("worker1"),
            "worker2": create_test_agent("worker2"),
            "postprocessor": create_test_agent("postprocessor"),
        }
        
        swarm = build_swarm_from_dict(config, agents_dict)
        
        assert isinstance(swarm, WorkflowSwarm)
        
        swarm.reset("test task")
        
        # Should have 3 top-level agents (preprocessor, team wrapped in TaskAgent, postprocessor)
        assert len(swarm.ordered_agents) == 3


class TestYAMLFile:
    """Test loading from YAML file."""
    
    def test_load_from_yaml_file(self):
        """Test loading swarm from YAML file."""
        yaml_content = """
swarm:
  name: "test_from_file"
  type: "workflow"
  agents:
    - id: "agent1"
      next: "agent2"
    - id: "agent2"
"""
        
        # Create temporary YAML file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            yaml_path = f.name
        
        try:
            agents_dict = {
                "agent1": create_test_agent("agent1"),
                "agent2": create_test_agent("agent2"),
            }
            
            swarm = build_swarm_from_yaml(yaml_path, agents_dict)
            
            assert swarm.name() == "test_from_file"
            assert isinstance(swarm, WorkflowSwarm)
            
        finally:
            # Clean up
            Path(yaml_path).unlink()
    
    def test_file_not_found(self):
        """Test error when YAML file doesn't exist."""
        with pytest.raises(AWorldRuntimeException, match="YAML file not found"):
            build_swarm_from_yaml("nonexistent.yaml", {})


class TestEdgeMerging:
    """Test merging of next syntax and explicit edges."""
    
    def test_next_and_edges_merge(self):
        """Test that next and edges are merged."""
        config = {
            "swarm": {
                "type": "workflow",
                "agents": [
                    {"id": "agent1", "next": "agent2"},
                    {"id": "agent2"},
                    {"id": "agent3"},
                ],
                "edges": [
                    {"from": "agent2", "to": "agent3"},
                ]
            }
        }
        
        agents_dict = {
            "agent1": create_test_agent("agent1"),
            "agent2": create_test_agent("agent2"),
            "agent3": create_test_agent("agent3"),
        }
        
        swarm = build_swarm_from_dict(config, agents_dict)
        swarm.reset("test task")
        
        # Should have created edges from both next and edges
        assert len(swarm.ordered_agents) == 3
    
    def test_edges_override_next(self):
        """Test that explicit edges override next when there's a conflict."""
        config = {
            "swarm": {
                "type": "handoff",
                "root_agent": "agent1",
                "agents": [
                    {"id": "agent1", "next": "agent2"},
                    {"id": "agent2"},
                    {"id": "agent3"},
                ],
                "edges": [
                    {"from": "agent1", "to": "agent2"},  # Same edge, should not duplicate
                    {"from": "agent1", "to": "agent3"},
                ]
            }
        }
        
        agents_dict = {
            "agent1": create_test_agent("agent1"),
            "agent2": create_test_agent("agent2"),
            "agent3": create_test_agent("agent3"),
        }
        
        swarm = build_swarm_from_dict(config, agents_dict)
        swarm.reset("test task")
        
        # agent1 should have handoffs to both agent2 and agent3
        agent1 = agents_dict["agent1"]
        assert len(agent1.handoffs) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
