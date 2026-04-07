# Hybrid Multi-Agent System Examples

This directory contains examples demonstrating AWorld's **Hybrid MAS architecture**.

## What is Hybrid Mode?

**Hybrid** = **Centralized Coordination** (TeamSwarm) + **Peer-to-peer Communication**

```
Orchestrator (Root)
      |
   [任务分配]
      |
   ┌──┴──┬──────┐
   ↓     ↓      ↓
Agent1 ←→ Agent2 ←→ Agent3
      [Peer通信]
```

### Key Features

- **Hierarchical Orchestration**: Root agent coordinates workflow (serial/parallel execution)
- **Peer Communication**: Executors can share information directly (non-blocking)
- **Full Mesh Topology**: All executors can communicate with each other
- **Fire-and-Forget**: No blocking waits for peer responses

### Architecture Comparison

| Mode | Orchestrator | Peer Communication | Use Case |
|------|-------------|-------------------|----------|
| **Single** | ❌ | ❌ | Simple tasks, one agent |
| **Workflow** | ❌ | ❌ | Sequential DAG execution |
| **Handoff** | ❌ | ✅ | Dynamic agent delegation |
| **Team** | ✅ | ❌ | Specialized tasks, independent work |
| **Hybrid** | ✅ | ✅ | **Complex tasks requiring coordination** |

## Examples

### 1. Data Processing Pipeline

**Directory**: `data_processing/`

**Scenario**: Process a batch of email addresses through filter → transform → validate stages

**Agents**:
- **DataCoordinator** (Root): Orchestrates the pipeline
- **FilterAgent**: Filters invalid emails, shares format info with peers
- **TransformAgent**: Standardizes email format, broadcasts completion status
- **ValidateAgent**: Validates quality, provides feedback to peers

**Peer Communication Patterns**:
- FilterAgent → TransformAgent: Share data format specifications
- ValidateAgent → TransformAgent: Share quality feedback
- All agents: Broadcast stage completion status

**Run**:
```bash
cd data_processing

# Quick architecture test (no LLM required, <5s)
python test_simple.py

# Full validation with LLM agents
python run_validation.py
```

**Expected Results**:
- Single-Agent: 70/100 quality score (no specialization)
- Team: 60/100 (independent work, no coordination)
- **Hybrid: 90/100** (specialization + peer coordination)

## Using Hybrid in Your Code

### Basic Usage

```python
from aworld.core.agent.swarm import Swarm, GraphBuildType
from aworld.agents.llm_agent import Agent

# Create agents
coordinator = Agent(name="Coordinator", ...)
analyst1 = Agent(name="Analyst1", ...)
analyst2 = Agent(name="Analyst2", ...)
analyst3 = Agent(name="Analyst3", ...)

# Create Hybrid swarm (two equivalent ways)

# Method 1: Using HybridSwarm alias (recommended)
from aworld.core.agent.swarm import HybridSwarm
swarm = HybridSwarm(
    coordinator,      # Root orchestrator
    analyst1,         # Executor 1
    analyst2,         # Executor 2
    analyst3          # Executor 3
)

# Method 2: Using Swarm with explicit build_type
swarm = Swarm(
    coordinator,
    analyst1,
    analyst2,
    analyst3,
    build_type=GraphBuildType.HYBRID
)

# Run task
from aworld.runner import Runners
result = await Runners.async_run(
    input="Your task description",
    swarm=swarm
)
```

### Peer Communication API

Executors automatically get peer communication capabilities:

```python
class MyAgent(Agent):
    async def async_policy(self, observation, **kwargs):
        # Check if in Hybrid mode
        if self._is_peer_enabled:
            # Share information with specific peer
            await self.share_with_peer(
                peer_name="Analyst2",
                information={"stage": "complete", "data": results},
                info_type="status"
            )
            
            # Broadcast to all peers
            await self.broadcast_to_all_peers(
                information={"alert": "high_priority"},
                info_type="alert"
            )
        
        # Continue with agent logic
        return self.to_action_model(results)
```

**Key Points**:
- ✅ Non-blocking: Methods return immediately
- ✅ No response: Fire-and-forget message pattern
- ✅ Automatic setup: HybridBuilder enables peer capability
- ✅ Full mesh: All executors can communicate with each other

## When to Use Hybrid

**Use Hybrid when**:
- ✅ Tasks require specialized agents (different expertise)
- ✅ Agents need to coordinate and share information
- ✅ Orchestrator should control overall workflow
- ✅ Information sharing improves result quality

**Use Team instead when**:
- Agents work independently without coordination needs
- No information sharing required between executors

**Use Handoff instead when**:
- Dynamic agent delegation without fixed orchestrator
- Conversational/iterative problem solving

## Architecture Details

### Topology Construction

HybridBuilder extends TeamBuilder:
1. Creates star topology (root → executors)
2. Enables `_is_peer_enabled = True` for all executors
3. Injects `_peer_agents` dict with peer references
4. No explicit peer edges (full mesh implied)

### Event-Driven Communication

Peer messages use EventManager:
- Topic: `TopicType.PEER_BROADCAST`
- Category: `Constants.AGENT`
- Non-blocking: `emit()` returns immediately
- No waiting: No `consume()` or response handling

## References

- **Design Doc**: `docs/designs/hybrid-swarm-architecture-plan.md`
- **Implementation**: `aworld/core/agent/swarm.py` (HybridBuilder)
- **Peer API**: `aworld/core/agent/base.py` (share_with_peer, broadcast_to_all_peers)
- **Events**: `aworld/core/event/base.py` (TopicType.PEER_BROADCAST)
