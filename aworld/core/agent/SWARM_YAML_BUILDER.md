# Swarm YAML Builder Implementation

## Overview

This document describes the implementation of the YAML-based Swarm builder for AWorld, which allows users to define multi-agent topologies using YAML configuration files instead of writing Python code.

## Implementation Summary

### Files Created

1. **aworld/core/agent/swarm_builder.py**
   - Core implementation of the YAML builder
   - `SwarmConfigValidator`: Validates YAML configuration
   - `SwarmYAMLBuilder`: Builds Swarm instances from configuration
   - `build_swarm_from_yaml()`: Main entry point for loading from files
   - `build_swarm_from_dict()`: Alternative entry point for dict configs

2. **examples/swarm_yaml/**
   - Collection of example YAML files demonstrating different patterns
   - `run_example.py`: Runnable examples showing usage
   - `README.md` and `README_zh.md`: Comprehensive documentation

3. **tests/core/test_swarm_yaml_builder.py**
   - Comprehensive unit tests for all functionality

## Design Decisions

### 1. YAML Structure

```yaml
swarm:
  name: "swarm_name"
  type: "workflow"  # workflow | handoff | team
  max_steps: 10
  event_driven: true
  root_agent: "agent_id"
  
  agents:
    - id: "agent1"
      node_type: "agent"  # agent | parallel | serial | swarm
      next: "agent2"  # Syntax sugar for edges
  
  edges:  # Optional explicit edges
    - from: "agent1"
      to: "agent2"
```

**Key Points**:
- `agents` (not `nodes`) as discussed
- Support for `next` syntax sugar and explicit `edges`
- `edges` takes priority when conflicts occur
- Global unique agent IDs across all nesting levels

### 2. Node Types

#### Regular Agent (`node_type: "agent"`)
- Default type, can be omitted
- Maps directly to agents in `agents_dict`

#### Parallel Group (`node_type: "parallel"`)
- Wraps agents in `ParallelizableAgent`
- Agents list references existing agent IDs
- Example:
  ```yaml
  - id: "parallel_group"
    node_type: "parallel"
    agents: ["task1", "task2"]
    next: "merge"
  ```

#### Serial Group (`node_type: "serial"`)
- Wraps agents in `SerialableAgent`
- Agents execute in list order
- Example:
  ```yaml
  - id: "serial_steps"
    node_type: "serial"
    agents: ["step1", "step2", "step3"]
    next: "next_agent"
  ```

#### Nested Swarm (`node_type: "swarm"`)
- Wraps nested swarm in `TaskAgent`
- No exposure of TaskAgent concept to users
- Supports recursive nesting
- Example:
  ```yaml
  - id: "sub_team"
    node_type: "swarm"
    swarm_type: "team"
    root_agent: "leader"
    agents:
      - id: "leader"
        next: ["worker1", "worker2"]
      - id: "worker1"
      - id: "worker2"
    next: "next_agent"
  ```

### 3. Edge Definition

Two ways to define edges:

#### Syntax Sugar with `next`
```yaml
- id: "agent1"
  next: "agent2"  # Single edge

- id: "agent2"
  next: ["agent3", "agent4"]  # Multiple edges
```

#### Explicit Edges
```yaml
edges:
  - from: "agent1"
    to: "agent2"
  - from: "agent2"
    to: "agent3"
```

#### Merging Strategy
1. Both `next` and `edges` are collected
2. Explicit edges are added first
3. Edges from `next` are added if not already present
4. This means `edges` takes priority on conflicts

### 4. Validation

Comprehensive validation includes:
- Required fields (type, agents, id)
- Valid swarm types and node types
- Unique agent IDs
- Node-specific requirements (e.g., parallel must have agents field)
- Recursive validation for nested swarms
- Edge validation (from/to must reference existing agents)

### 5. Agent Namespace

**Decision**: Global unique agent IDs required

**Rationale**:
- Simpler implementation and understanding
- No ambiguity in references
- Easier debugging and tracing
- Consistent with how agents are registered in AgentFactory

**Alternative Considered**: Scoped IDs with path prefixes (e.g., `team.worker1`)
- Would add complexity
- Not aligned with current AWorld architecture

### 6. Parallel/Serial Group References

**Decision**: Reference existing agents only (no inline definitions)

**Rationale**:
- Clear separation of agent definitions and grouping
- All agents defined in one place (agents list)
- Easier to understand topology structure
- Consistent with the principle of single definition

Example (correct):
```yaml
agents:
  - id: "task1"  # Define first
  - id: "task2"
  
  - id: "parallel_group"
    node_type: "parallel"
    agents: ["task1", "task2"]  # Reference
```

## Implementation Details

### Class: SwarmConfigValidator

Validates YAML configuration before building.

**Key Methods**:
- `validate_config(config: Dict)`: Main validation entry point

**Validation Rules**:
- Swarm type must be valid
- At least one agent required
- Agent IDs must be unique
- Node types must be valid
- Specific node type requirements

### Class: SwarmYAMLBuilder

Builds Swarm instances from validated configuration.

**Key Methods**:
- `build()`: Main build method, returns Swarm instance
- `_build_topology()`: Constructs topology list from config
- `_create_agent_if_needed()`: Creates special agent types
- `_create_parallel_agent()`: Creates ParallelizableAgent
- `_create_serial_agent()`: Creates SerialableAgent
- `_create_nested_swarm()`: Recursively creates nested swarms

**Build Process**:
1. Validate configuration
2. Create special agents (parallel/serial/nested)
3. Build edges from `next` and `edges`
4. Merge edges with priority rules
5. Create appropriate Swarm type
6. Return configured Swarm instance

### Function: build_swarm_from_yaml

Main entry point for users.

```python
def build_swarm_from_yaml(
    yaml_path: str,
    agents_dict: Dict[str, BaseAgent],
    **kwargs
) -> Swarm
```

**Parameters**:
- `yaml_path`: Path to YAML config file
- `agents_dict`: Agent ID to instance mapping
- `**kwargs`: Override parameters

**Process**:
1. Load YAML file
2. Parse with yaml.safe_load
3. Pass to SwarmYAMLBuilder
4. Apply kwargs overrides
5. Return Swarm

## Usage Examples

### Simple Workflow

```python
from aworld.core.agent import Agent, build_swarm_from_yaml

agents = {
    "agent1": Agent(name="agent1"),
    "agent2": Agent(name="agent2"),
    "agent3": Agent(name="agent3"),
}

swarm = build_swarm_from_yaml("workflow.yaml", agents)
swarm.reset("Execute workflow")
```

### Nested Swarm

```python
# All agents from all levels must be in agents_dict
agents = {
    "preprocessor": Agent(name="preprocessor"),
    "coordinator": Agent(name="coordinator"),
    "analyst1": Agent(name="analyst1"),
    "analyst2": Agent(name="analyst2"),
    "summarizer": Agent(name="summarizer"),
}

swarm = build_swarm_from_yaml("nested.yaml", agents)
```

## Testing

Comprehensive test coverage in `tests/core/test_swarm_yaml_builder.py`:

- Configuration validation
- Simple workflows
- Parallel/serial groups
- Team swarms
- Handoff swarms
- Nested swarms
- Edge merging
- Error handling

Run tests:
```bash
pytest tests/core/test_swarm_yaml_builder.py -v
```

## Future Enhancements

Potential improvements for future versions:

1. **Conditional Edges**: Support condition functions in YAML
   ```yaml
   edges:
     - from: "agent1"
       to: "agent2"
       condition: "lambda x: x.score > 0.5"
   ```

2. **Agent Configuration in YAML**: Allow agent parameters in YAML
   ```yaml
   - id: "agent1"
     class: "Agent"
     params:
       desc: "My agent"
       temperature: 0.7
   ```

3. **YAML Includes**: Reference external YAML files
   ```yaml
   - id: "sub_swarm"
     node_type: "swarm"
     yaml_file: "sub_swarm.yaml"
   ```

4. **Topology Visualization**: Generate visual diagrams from YAML
   ```python
   visualize_swarm_yaml("config.yaml", output="topology.png")
   ```

5. **Python to YAML Converter**: Extract YAML from existing Python code
   ```python
   swarm_to_yaml(swarm, output="extracted.yaml")
   ```

## Migration Guide

For existing code using Python topology definitions:

### Before (Python)
```python
from aworld.core.agent import Swarm, Agent

agent1 = Agent(name="agent1")
agent2 = Agent(name="agent2")
agent3 = Agent(name="agent3")

swarm = Swarm(
    (agent1, agent2),
    (agent2, agent3),
    build_type=GraphBuildType.WORKFLOW
)
```

### After (YAML)
```yaml
# workflow.yaml
swarm:
  type: "workflow"
  agents:
    - id: "agent1"
      next: "agent2"
    - id: "agent2"
      next: "agent3"
    - id: "agent3"
```

```python
from aworld.core.agent import Agent, build_swarm_from_yaml

agents = {
    "agent1": Agent(name="agent1"),
    "agent2": Agent(name="agent2"),
    "agent3": Agent(name="agent3"),
}

swarm = build_swarm_from_yaml("workflow.yaml", agents)
```

## Performance Considerations

- YAML parsing is fast for reasonable file sizes
- Validation adds minimal overhead
- Recursive nested swarm building is efficient
- Agent creation happens once during build

## Error Handling

All errors raise `AWorldRuntimeException` with descriptive messages:
- Missing required fields
- Invalid types
- Duplicate IDs
- Missing agent references
- Structural violations (e.g., workflow cycles)

## Conclusion

The YAML-based Swarm builder provides a clean, declarative way to define multi-agent systems in AWorld. It supports all major patterns (workflow, handoff, team), nested swarms, parallel/serial execution, and flexible topology definition through both syntax sugar and explicit edges.

The implementation follows the design principles discussed and provides comprehensive examples, documentation, and tests.
