# Hybrid Mode: Data Processing Example

This example demonstrates AWorld's **Hybrid MAS architecture** with a data processing pipeline.

**Location**: `examples/multi_agents/hybrid/data_processing/`

## What is Hybrid Mode?

**Hybrid** = **Centralized Coordination** (TeamSwarm) + **Peer-to-peer Communication**

```
Orchestrator (Root)
      |
   [分配任务]
      |
   ┌──┴──┬──────┐
   ↓     ↓      ↓
Agent1 ←→ Agent2 ←→ Agent3
      [Peer通信]
```

**Key Features:**
- Orchestrator controls execution flow (串行/并行)
- Executors can share information with peers (非阻塞)
- No blocking: all peer communication is fire-and-forget

## Architecture Comparison

| Mode | Orchestrator | Peer Communication | Use Case |
|------|-------------|-------------------|----------|
| **Single** | ❌ | ❌ | Simple tasks, one agent |
| **Team** | ✅ | ❌ | Specialized tasks, no collaboration needed |
| **Hybrid** | ✅ | ✅ | Complex tasks requiring coordination |

## Test Cases

### 1. Architecture Validation (With LLM)

**File**: `run_validation.py`

**Purpose**: Validate Hybrid architecture with real LLM agents

**Time:** < 5 seconds

---

### 2. Data Processing Validation (With LLM)

**File**: `run_validation.py`

**Purpose**: Compare Single/Team/Hybrid on real task

**Test Case**: Email processing pipeline
- Input: Mixed valid/invalid emails
- Stages: Filter → Transform → Validate
- Comparison: Quality score across 3 modes

**Agents:**
- `FilterAgent`: Filters invalid emails
- `TransformAgent`: Standardizes email format
- `ValidateAgent`: Validates and scores quality
- `DataCoordinator`: Orchestrates pipeline

**Run:**
```bash
cd examples/multi_agents/hybrid/data_processing
python run_validation.py
```

**Prerequisites:**
- Set `LLM_API_KEY` in `.env` or environment
- Configure `LLM_MODEL_NAME` (default: gpt-4o)

**Expected behavior:**
- **Single-Agent**: Works, but no specialization
- **Team**: Executors work independently, no info sharing
- **Hybrid**: Executors share results via peer API → better coordination

**Time:** ~30 seconds (depends on LLM API)

---

## Peer Communication Pattern

### Using EventManager Directly

**Framework provides the mechanism (EventManager + topology), agents implement the pattern.**

```python
from aworld.core.event.base import TopicType, Constants
import time

class FilterAgent(Agent):
    async def async_policy(self, observation, **kwargs):
        # Process data
        filtered_data = self.filter(observation.content)
        
        # Get context from message
        message = kwargs.get('message')
        context = message.context
        task = context.get_task()
        
        # Find peer agent by name from swarm
        transform_agent = next(
            (a for a in task.swarm.agents if a.name() == "TransformAgent"),
            None
        )
        
        if transform_agent:
            # Share filtered data format with TransformAgent
            await context.event_manager.emit(
                data={
                    "type": "share",
                    "info_type": "data_format",
                    "information": {
                        "format": "standard_email",
                        "count": len(filtered_data)
                    },
                    "sender_name": self.name(),
                    "timestamp": time.time()
                },
                sender=self.id(),
                receiver=transform_agent.id(),
                topic=TopicType.PEER_BROADCAST,
                session_id=context.session_id,
                event_type=Constants.AGENT
            )
        
        return [self.to_action_model(filtered_data)]
```

### Broadcasting to All Peers

```python
class ValidateAgent(Agent):
    async def async_policy(self, observation, **kwargs):
        validation_result = self.validate(observation.content)
        
        message = kwargs.get('message')
        context = message.context
        task = context.get_task()
        
        # Get all executor peers (excluding self and coordinator)
        root_id = task.swarm.agent_graph.root_agent.id()
        peers = [
            agent for agent in task.swarm.agents 
            if agent.id() != self.id() and agent.id() != root_id
        ]
        
        # Broadcast to all peers
        for peer in peers:
            await context.event_manager.emit(
                data={
                    "type": "broadcast",
                    "info_type": "completion",
                    "information": {
                        "status": "validation_complete",
                        "pass_rate": validation_result['pass_rate']
                    },
                    "sender_name": self.name(),
                    "timestamp": time.time()
                },
                sender=self.id(),
                receiver=peer.id(),
                topic=TopicType.PEER_BROADCAST,
                session_id=context.session_id,
                event_type=Constants.AGENT
            )
        
        return [self.to_action_model(validation_result)]
```

### Receiving Peer Messages

```python
class TransformAgent(Agent):
    async def async_policy(self, observation, **kwargs):
        message = kwargs.get('message')
        context = message.context
        
        # Check for peer messages (optional, non-blocking)
        format_info = None
        try:
            msg = await asyncio.wait_for(
                context.event_manager.consume(nowait=True),
                timeout=0.1  # Quick check
            )
            if msg and msg.topic == TopicType.PEER_BROADCAST:
                payload = msg.payload
                if payload.get('info_type') == 'data_format':
                    format_info = payload.get('information')
        except asyncio.TimeoutError:
            pass  # No messages, proceed with defaults
        
        # Transform data (using format_info if available)
        result = self.transform(observation.content, format_info)
        return [self.to_action_model(result)]
```

**Key Points:**
- ✅ Non-blocking: Methods return immediately
- ✅ No response: Fire-and-forget message pattern
- ✅ Automatic setup: HybridBuilder enables peer capability
- ✅ Full mesh: All executors can communicate with each other

## File Structure

```
examples/hybrid_validation/
├── README.md                  # This file
├── test_simple.py            # Architecture test (no LLM)
├── run_validation.py         # Full validation (with LLM)
├── filter_agent.py           # Email filtering agent
├── transform_agent.py        # Email transformation agent
├── validate_agent.py         # Email validation agent
└── coordinator_agent.py      # Pipeline coordinator
```

## Integration with AWorld

### Creating Hybrid Swarm

```python
from aworld.core.agent.swarm import Swarm, GraphBuildType

swarm = Swarm(
    coordinator,      # Root
    agent1, agent2,   # Executors
    build_type=GraphBuildType.HYBRID  # Enable peer communication
)
```

### How HybridBuilder Works

When `build_type=HYBRID`, HybridBuilder automatically:
1. Creates star topology (coordinator → executors)
2. Enables full-mesh peer connectivity (executors can communicate with each other)
3. Logs peer capability availability

Executors can then use EventManager directly for peer-to-peer communication (see examples above)

## Design Principles

### 1. Non-blocking by Design
All peer communication is fire-and-forget. No blocking `ask_peer()` or `request_peer_action()`.

### 2. Orchestrator Controls Flow
Orchestrator decides execution order (串行/并行). Peer communication does NOT change control flow.

### 3. Information Sharing
Peers share:
- Intermediate results
- Status updates
- Alerts and feedback

They do NOT:
- Wait for responses
- Request actions from peers
- Change execution sequence

## Next Steps

After validation:
1. ✅ Phase 1: Core Hybrid implementation complete
2. ⏳ Phase 2: BDD validation on GAIA/XBench (optional)
3. ⏳ Phase 3: Advanced features (metrics, visualization)

## References

- Design doc: `docs/designs/hybrid-swarm-architecture-plan.md`
- Test case: `docs/designs/data-processing-validation-case.md`
- Source: `aworld/core/agent/swarm.py` (HybridBuilder)
- API: `aworld/core/agent/base.py` (peer methods)
