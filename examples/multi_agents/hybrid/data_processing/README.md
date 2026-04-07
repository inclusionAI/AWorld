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

### 1. Simple Architecture Test (No LLM)

**File**: `test_simple.py`

**Purpose**: Validate Hybrid architecture at framework level

**What it tests:**
- ✅ HybridBuilder creates star topology
- ✅ Peer capability enabled for executors only
- ✅ `share_with_peer()` works
- ✅ `broadcast_to_all_peers()` works
- ✅ Team vs Hybrid differentiation
- ✅ Error handling

**Run:**
```bash
cd examples/multi_agents/hybrid/data_processing
python test_simple.py
```

**Expected output:**
```
TEST 1 PASSED: HybridBuilder works correctly
TEST 2 PASSED: Team vs Hybrid correctly differentiated  
TEST 3 PASSED: Peer API works correctly
✅ ALL TESTS PASSED
```

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

## Peer API Usage

### Non-blocking Communication

```python
# ✅ Share with specific peer (unicast)
await self.share_with_peer(
    peer_name="Agent2",
    information={"stage": "complete", "data": results},
    info_type="status"
)
# Returns immediately, continues execution

# ✅ Broadcast to all peers
await self.broadcast_to_all_peers(
    information={"system_status": "ready"},
    info_type="alert"
)
# Returns immediately, continues execution
```

### Error Cases

```python
# ❌ Not in Hybrid swarm
RuntimeError: "Agent is not in a Hybrid swarm"

# ❌ Invalid peer name
ValueError: "Peer 'Unknown' not found. Available peers: [...]"
```

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

### Automatic Peer Enablement

When `build_type=HYBRID`, HybridBuilder automatically:
1. Creates star topology (coordinator → executors)
2. Sets `agent._is_peer_enabled = True` for all executors
3. Injects `agent._peer_agents` dict with peer references

Executors can then use:
- `await self.share_with_peer(...)`
- `await self.broadcast_to_all_peers(...)`

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
