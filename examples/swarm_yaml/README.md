# Swarm YAML Builder Examples

This directory contains examples of building AWorld Swarms using YAML configuration files.

## Overview

The YAML-based Swarm builder provides a declarative way to define multi-agent systems without writing Python code for topology construction. It supports:

- **Three Swarm Types**: Workflow, Handoff, and Team
- **Nested Swarms**: Embed swarms within swarms
- **Parallel/Serial Groups**: Express parallel and sequential execution
- **Flexible Topology**: Use syntax sugar (`next`) or explicit edges

## Quick Start

### 1. Install Dependencies

```bash
pip install pyyaml
```

### 2. Run Examples

```bash
cd examples/swarm_yaml
python run_example.py
```

## YAML Structure

### Basic Structure

```yaml
swarm:
  name: "my_swarm"
  type: "workflow"  # workflow | handoff | team
  max_steps: 10
  event_driven: true
  root_agent: "agent1"  # Optional
  
  agents:
    - id: "agent1"
      next: "agent2"
    
    - id: "agent2"
      next: "agent3"
    
    - id: "agent3"
  
  # Optional: explicit edges (merged with 'next')
  edges:
    - from: "agent1"
      to: "agent2"
```

### Node Types

#### 1. Regular Agent (default)

```yaml
- id: "agent1"
  node_type: "agent"  # Can be omitted
  next: "agent2"
```

#### 2. Parallel Group

Agents execute in parallel (wrapped in `ParallelizableAgent`):

```yaml
- id: "parallel_tasks"
  node_type: "parallel"
  agents: ["task1", "task2", "task3"]
  next: "merge_agent"

- id: "task1"
- id: "task2"
- id: "task3"
```

#### 3. Serial Group

Agents execute sequentially (wrapped in `SerialableAgent`):

```yaml
- id: "serial_steps"
  node_type: "serial"
  agents: ["step1", "step2", "step3"]
  next: "next_agent"

- id: "step1"
- id: "step2"
- id: "step3"
```

#### 4. Nested Swarm

Embed a swarm within another swarm (wrapped in `TaskAgent`):

```yaml
- id: "sub_team"
  node_type: "swarm"
  swarm_type: "team"  # workflow | handoff | team
  root_agent: "leader"
  agents:
    - id: "leader"
      next: ["worker1", "worker2"]
    - id: "worker1"
    - id: "worker2"
  next: "next_agent"
```

## Examples

### Example 1: Simple Workflow

**File**: `simple_workflow.yaml`

A linear workflow with three agents executing sequentially.

```yaml
swarm:
  name: "simple_workflow"
  type: "workflow"
  
  agents:
    - id: "agent1"
      next: "agent2"
    - id: "agent2"
      next: "agent3"
    - id: "agent3"
```

### Example 2: Parallel Workflow

**File**: `parallel_workflow.yaml`

Demonstrates parallel execution of multiple agents.

```yaml
swarm:
  name: "parallel_workflow"
  type: "workflow"
  
  agents:
    - id: "start"
      next: "parallel_tasks"
    
    - id: "parallel_tasks"
      node_type: "parallel"
      agents: ["task1", "task2", "task3"]
      next: "merge"
    
    - id: "task1"
    - id: "task2"
    - id: "task3"
    - id: "merge"
```

### Example 3: Team Swarm

**File**: `team_swarm.yaml`

A coordinator agent managing multiple worker agents (star topology).

```yaml
swarm:
  name: "team_example"
  type: "team"
  root_agent: "coordinator"
  
  agents:
    - id: "coordinator"
      next: ["worker1", "worker2", "worker3"]
    - id: "worker1"
    - id: "worker2"
    - id: "worker3"
```

### Example 4: Handoff Swarm

**File**: `handoff_swarm.yaml`

Agents can dynamically hand off control to each other.

```yaml
swarm:
  name: "handoff_example"
  type: "handoff"
  root_agent: "agent1"
  
  agents:
    - id: "agent1"
    - id: "agent2"
    - id: "agent3"
  
  edges:
    - from: "agent1"
      to: "agent2"
    - from: "agent1"
      to: "agent3"
    - from: "agent2"
      to: "agent3"
    - from: "agent3"
      to: "agent1"  # Cycles allowed
```

### Example 5: Nested Swarm

**File**: `nested_swarm.yaml`

A team swarm embedded within a workflow.

```yaml
swarm:
  name: "nested_swarm_example"
  type: "workflow"
  
  agents:
    - id: "preprocessor"
      next: "analysis_team"
    
    - id: "analysis_team"
      node_type: "swarm"
      swarm_type: "team"
      root_agent: "coordinator"
      agents:
        - id: "coordinator"
          next: ["analyst1", "analyst2", "analyst3"]
        - id: "analyst1"
        - id: "analyst2"
        - id: "analyst3"
      next: "summarizer"
    
    - id: "summarizer"
```

### Example 6: Complex Workflow

**File**: `complex_workflow.yaml`

Combines parallel, serial, and branching paths.

### Example 7: Multi-Level Nested

**File**: `multi_level_nested.yaml`

Multiple levels of swarm nesting.

## Usage in Python

```python
from aworld.core.agent.base import Agent
from aworld.core.agent.swarm_builder import build_swarm_from_yaml

# Create agents
agents_dict = {
    "agent1": Agent(name="agent1", desc="First agent"),
    "agent2": Agent(name="agent2", desc="Second agent"),
    "agent3": Agent(name="agent3", desc="Third agent"),
}

# Build swarm from YAML
swarm = build_swarm_from_yaml("simple_workflow.yaml", agents_dict)

# Initialize and use
swarm.reset("Your task description")

# Access swarm properties
print(f"Swarm type: {swarm.build_type}")
print(f"Ordered agents: {[a.name() for a in swarm.ordered_agents]}")
```

## Syntax Sugar: `next` vs `edges`

### Using `next` (Recommended for simple cases)

```yaml
agents:
  - id: "agent1"
    next: "agent2"  # Single edge
  
  - id: "agent2"
    next: ["agent3", "agent4"]  # Multiple edges
```

### Using `edges` (Recommended for complex graphs)

```yaml
agents:
  - id: "agent1"
  - id: "agent2"
  - id: "agent3"

edges:
  - from: "agent1"
    to: "agent2"
  - from: "agent1"
    to: "agent3"
```

### Combining Both

If both `next` and `edges` are defined:
- Both are merged into the final topology
- If there's a conflict (same edge defined twice), `edges` takes priority

## Key Design Principles

1. **Agent IDs Must Be Globally Unique**: All agent IDs across all nesting levels must be unique.

2. **Parallel/Serial Groups Reference Existing Agents**: Agents in `parallel` or `serial` groups must be defined in the same level or outer level.

3. **Nested Swarms Are Transparent**: Nested swarms appear as regular nodes in the parent topology, but internally maintain their own structure.

4. **Validation on Load**: Configuration is validated when loading, catching errors early.

## API Reference

### Main Function

```python
def build_swarm_from_yaml(
    yaml_path: str,
    agents_dict: Dict[str, BaseAgent],
    **kwargs
) -> Swarm:
    """Build a Swarm instance from YAML configuration file.
    
    Args:
        yaml_path: Path to YAML configuration file.
        agents_dict: Dictionary mapping agent IDs to agent instances.
        **kwargs: Additional parameters to override YAML configuration.
    
    Returns:
        Constructed Swarm instance.
    """
```

### Alternative Function

```python
def build_swarm_from_dict(
    config: Dict[str, Any],
    agents_dict: Dict[str, BaseAgent],
    **kwargs
) -> Swarm:
    """Build a Swarm instance from configuration dictionary.
    
    Useful when config is loaded from other sources (JSON, database, etc.).
    """
```

## Validation Rules

The YAML configuration is validated with the following rules:

1. **Required Fields**:
   - `swarm.type`: Must be one of `workflow`, `handoff`, `team`
   - `swarm.agents`: Must have at least one agent
   - Each agent must have an `id` field

2. **Agent ID Uniqueness**: No duplicate agent IDs allowed

3. **Node Type Validation**:
   - `parallel` and `serial` nodes must have `agents` field
   - `swarm` nodes must have `swarm_type` and `agents` fields

4. **Edge Validation**:
   - Each edge must have `from` and `to` fields
   - Referenced agents must exist

5. **Swarm Type Constraints**:
   - `workflow`: Cannot have cycles (DAG only)
   - `handoff`: All topology items must be agent pairs
   - `team`: Root agent must be specified or be the first agent

## Troubleshooting

### Error: "Agent 'xxx' not found in agents_dict"

Make sure all agents referenced in the YAML (including those in nested swarms) are present in the `agents_dict` parameter.

### Error: "Duplicate agent id: xxx"

Agent IDs must be globally unique. Rename one of the conflicting agents.

### Error: "Workflow unsupported cycle graph"

Workflow type cannot have cycles. Use `handoff` type if you need cycles, or restructure your topology.

### Error: "Swarm node must have 'swarm_type' field"

When using `node_type: "swarm"`, you must specify the `swarm_type` field (workflow/handoff/team).

## Best Practices

1. **Use Descriptive Agent IDs**: Make agent IDs meaningful (e.g., `data_preprocessor` instead of `agent1`)

2. **Choose the Right Swarm Type**:
   - Use `workflow` for deterministic, sequential processes
   - Use `handoff` for dynamic, AI-driven agent collaboration
   - Use `team` for coordinator-worker patterns

3. **Prefer `next` for Simple Topologies**: It's more readable than explicit edges

4. **Use Explicit `edges` for Complex Graphs**: When there are many cross-connections

5. **Document Your Topology**: Add comments in YAML to explain the flow

6. **Validate Early**: Test your YAML configuration with simple agents first

## Related Documentation

- [AWorld Swarm Documentation](../../docs/core_concepts/mas/index.html)
- [Agent Documentation](../../docs/Agents/)
- [Workflow vs Handoff vs Team](../../docs/Get%20Start/Core%20Capabilities.md)

## License

Copyright (c) 2025 inclusionAI.
