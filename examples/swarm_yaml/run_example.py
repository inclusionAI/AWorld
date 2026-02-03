# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""Example usage of YAML-based Swarm builder.

This script demonstrates how to build and use Swarms from YAML configuration files.
"""

import os
from pathlib import Path

from aworld.core.agent.base import Agent
from aworld.core.agent.swarm_builder import build_swarm_from_yaml
from aworld.logs.util import logger


def create_simple_agent(agent_id: str, desc: str = None) -> Agent:
    """Create a simple agent for testing.
    
    Args:
        agent_id: Unique identifier for the agent.
        desc: Optional description of the agent.
    
    Returns:
        Configured Agent instance.
    """
    return Agent(
        name=agent_id,
        desc=desc or f"Agent {agent_id} for testing YAML swarm builder",
    )


def example_simple_workflow():
    """Example: Simple workflow with three sequential agents."""
    print("\n" + "="*60)
    print("Example 1: Simple Workflow")
    print("="*60)
    
    # Create agents
    agents_dict = {
        "agent1": create_simple_agent("agent1", "First agent in the workflow"),
        "agent2": create_simple_agent("agent2", "Second agent in the workflow"),
        "agent3": create_simple_agent("agent3", "Third agent in the workflow"),
    }
    
    # Build swarm from YAML
    yaml_path = Path(__file__).parent / "simple_workflow.yaml"
    swarm = build_swarm_from_yaml(str(yaml_path), agents_dict)
    
    print(f"Swarm type: {swarm.build_type}")
    print(f"Swarm name: {swarm.name()}")
    print(f"Number of agents: {len(swarm.agents)}")
    
    # Initialize swarm
    swarm.reset("Execute a simple workflow task")
    print(f"Ordered agents: {[agent.name() for agent in swarm.ordered_agents]}")
    
    return swarm


def example_parallel_workflow():
    """Example: Workflow with parallel execution."""
    print("\n" + "="*60)
    print("Example 2: Parallel Workflow")
    print("="*60)
    
    # Create agents
    agents_dict = {
        "start": create_simple_agent("start", "Starting agent"),
        "task1": create_simple_agent("task1", "Parallel task 1"),
        "task2": create_simple_agent("task2", "Parallel task 2"),
        "task3": create_simple_agent("task3", "Parallel task 3"),
        "merge": create_simple_agent("merge", "Merge results from parallel tasks"),
        "end": create_simple_agent("end", "Final agent"),
    }
    
    # Build swarm from YAML
    yaml_path = Path(__file__).parent / "parallel_workflow.yaml"
    swarm = build_swarm_from_yaml(str(yaml_path), agents_dict)
    
    print(f"Swarm type: {swarm.build_type}")
    print(f"Swarm name: {swarm.name()}")
    
    # Initialize swarm
    swarm.reset("Execute tasks in parallel")
    print(f"Ordered agents: {[agent.name() for agent in swarm.ordered_agents]}")
    
    return swarm


def example_team_swarm():
    """Example: Team swarm with coordinator and workers."""
    print("\n" + "="*60)
    print("Example 3: Team Swarm")
    print("="*60)
    
    # Create agents
    agents_dict = {
        "coordinator": create_simple_agent("coordinator", "Team coordinator"),
        "worker1": create_simple_agent("worker1", "Worker agent 1"),
        "worker2": create_simple_agent("worker2", "Worker agent 2"),
        "worker3": create_simple_agent("worker3", "Worker agent 3"),
    }
    
    # Build swarm from YAML
    yaml_path = Path(__file__).parent / "team_swarm.yaml"
    swarm = build_swarm_from_yaml(str(yaml_path), agents_dict)
    
    print(f"Swarm type: {swarm.build_type}")
    print(f"Swarm name: {swarm.name()}")
    print(f"Root agent: {swarm.communicate_agent.name()}")
    
    # Initialize swarm
    swarm.reset("Coordinate team to complete task")
    
    # Show handoffs
    root = swarm.communicate_agent
    print(f"Coordinator handoffs: {root.handoffs}")
    
    return swarm


def example_handoff_swarm():
    """Example: Handoff swarm where agents can hand off to each other."""
    print("\n" + "="*60)
    print("Example 4: Handoff Swarm")
    print("="*60)
    
    # Create agents
    agents_dict = {
        "agent1": create_simple_agent("agent1", "First agent with handoff capability"),
        "agent2": create_simple_agent("agent2", "Second agent with handoff capability"),
        "agent3": create_simple_agent("agent3", "Third agent with handoff capability"),
    }
    
    # Build swarm from YAML
    yaml_path = Path(__file__).parent / "handoff_swarm.yaml"
    swarm = build_swarm_from_yaml(str(yaml_path), agents_dict)
    
    print(f"Swarm type: {swarm.build_type}")
    print(f"Swarm name: {swarm.name()}")
    print(f"Has cycle: {swarm.has_cycle}")
    
    # Initialize swarm
    swarm.reset("Execute with dynamic handoffs")
    
    # Show handoffs for each agent
    for agent_id, agent in swarm.agents.items():
        if hasattr(agent, 'handoffs'):
            print(f"{agent.name()} can handoff to: {agent.handoffs}")
    
    return swarm


def example_nested_swarm():
    """Example: Nested swarm with a team swarm embedded in a workflow."""
    print("\n" + "="*60)
    print("Example 5: Nested Swarm")
    print("="*60)
    
    # Create agents for all levels
    agents_dict = {
        "preprocessor": create_simple_agent("preprocessor", "Preprocess data"),
        "coordinator": create_simple_agent("coordinator", "Analysis coordinator"),
        "analyst1": create_simple_agent("analyst1", "Analyst 1"),
        "analyst2": create_simple_agent("analyst2", "Analyst 2"),
        "analyst3": create_simple_agent("analyst3", "Analyst 3"),
        "summarizer": create_simple_agent("summarizer", "Summarize results"),
        "reviewer": create_simple_agent("reviewer", "Review final output"),
    }
    
    # Build swarm from YAML
    yaml_path = Path(__file__).parent / "nested_swarm.yaml"
    swarm = build_swarm_from_yaml(str(yaml_path), agents_dict)
    
    print(f"Swarm type: {swarm.build_type}")
    print(f"Swarm name: {swarm.name()}")
    
    # Initialize swarm
    swarm.reset("Execute nested swarm workflow")
    print(f"Number of top-level agents: {len([a for a in swarm.ordered_agents])}")
    
    return swarm


def example_complex_workflow():
    """Example: Complex workflow with parallel, serial, and branching."""
    print("\n" + "="*60)
    print("Example 6: Complex Workflow")
    print("="*60)
    
    # Create all required agents
    agents_dict = {
        "start": create_simple_agent("start", "Start agent"),
        "quick_task": create_simple_agent("quick_task", "Quick parallel task"),
        "step1": create_simple_agent("step1", "Serial step 1"),
        "step2": create_simple_agent("step2", "Serial step 2"),
        "step3": create_simple_agent("step3", "Serial step 3"),
        "decision_point": create_simple_agent("decision_point", "Decision point"),
        "path_a": create_simple_agent("path_a", "Path A processing"),
        "path_b": create_simple_agent("path_b", "Path B processing"),
        "merge": create_simple_agent("merge", "Merge paths"),
        "final_processing": create_simple_agent("final_processing", "Final processing"),
    }
    
    # Build swarm from YAML
    yaml_path = Path(__file__).parent / "complex_workflow.yaml"
    swarm = build_swarm_from_yaml(str(yaml_path), agents_dict)
    
    print(f"Swarm type: {swarm.build_type}")
    print(f"Swarm name: {swarm.name()}")
    
    # Initialize swarm
    swarm.reset("Execute complex workflow with branching")
    print(f"Ordered agents: {[agent.name() for agent in swarm.ordered_agents]}")
    
    return swarm


def example_multi_level_nested():
    """Example: Multi-level nested swarm."""
    print("\n" + "="*60)
    print("Example 7: Multi-Level Nested Swarm")
    print("="*60)
    
    # Create agents for all levels
    agents_dict = {
        "entry": create_simple_agent("entry", "Entry point"),
        "level1_start": create_simple_agent("level1_start", "Level 1 start"),
        "team_leader": create_simple_agent("team_leader", "Team leader"),
        "worker_a": create_simple_agent("worker_a", "Worker A"),
        "worker_b": create_simple_agent("worker_b", "Worker B"),
        "level1_end": create_simple_agent("level1_end", "Level 1 end"),
        "final_agent": create_simple_agent("final_agent", "Final agent"),
    }
    
    # Build swarm from YAML
    yaml_path = Path(__file__).parent / "multi_level_nested.yaml"
    swarm = build_swarm_from_yaml(str(yaml_path), agents_dict)
    
    print(f"Swarm type: {swarm.build_type}")
    print(f"Swarm name: {swarm.name()}")
    
    # Initialize swarm
    swarm.reset("Execute multi-level nested swarm")
    
    return swarm


def main():
    """Run all examples."""
    print("\n" + "="*60)
    print("YAML-Based Swarm Builder Examples")
    print("="*60)
    
    try:
        # Run examples
        example_simple_workflow()
        example_parallel_workflow()
        example_team_swarm()
        example_handoff_swarm()
        example_nested_swarm()
        example_complex_workflow()
        example_multi_level_nested()
        
        print("\n" + "="*60)
        print("All examples completed successfully!")
        print("="*60 + "\n")
        
    except Exception as e:
        logger.error(f"Error running examples: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
