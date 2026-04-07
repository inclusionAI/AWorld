# Architecture Rollback: BaseAgent Stays Pure

## Problem Identified

**User Insight:** Peer communication methods在 BaseAgent 中不合适，因为：
1. BaseAgent 是顶级抽象，应该只包含所有 agent 共有的核心能力
2. Hybrid 是特定拓扑实现，不应污染基类
3. 违反了单一职责原则和开闭原则

**架构问题：**
```
BaseAgent (顶级抽象)
    ├─ policy() ✅ 核心能力
    ├─ run() ✅ 核心能力
    └─ share_with_peer() ❌ Hybrid 特定，不属于这里
```

## Rollback Actions

### 1. Removed from BaseAgent
- ❌ `share_with_peer()` method
- ❌ `broadcast_to_all_peers()` method
- ❌ `_get_peer_info_from_graph()` helper
- ❌ `_find_peer_by_name()` helper

### 2. Removed from AgentGraph
- ❌ `peer_enabled_agents: Set[str]`
- ❌ `peer_connections: Dict[str, Dict[str, BaseAgent]]`

### 3. Simplified HybridBuilder
- ❌ Removed code that sets agent instance state
- ✅ Kept star topology construction
- ✅ Added log message indicating peer capability available

### 4. Updated Documentation
- ✅ `examples/multi_agents/hybrid/README.md` - Shows direct EventManager usage
- ✅ `docs/designs/hybrid-peer-message-handling.md` - Explains mechanism vs policy

### 5. Updated Tests
- ✅ Removed peer capability tests from regression suite (relied on removed attributes)
- ✅ Kept core topology tests (10/10 passing)
- ❌ Removed `test_simple.py` and `test_concurrent_peer_comm.py` (depended on removed API)

## New Design: Pure BaseAgent + Usage Pattern

**BaseAgent remains pure:**
```python
class BaseAgent:
    # ✅ Core capabilities only
    async def async_policy(self, observation, **kwargs)
    async def async_run(self, message)
    @staticmethod
    def _get_current_context()
    
    # ❌ NO topology-specific methods
```

**Peer communication becomes a usage pattern:**
```python
class MyHybridAgent(Agent):
    async def async_policy(self, observation, **kwargs):
        message = kwargs.get('message')
        context = message.context
        task = context.get_task()
        
        # Find peer from swarm
        peer = next(
            (a for a in task.swarm.agents if a.name() == "PeerName"),
            None
        )
        
        if peer:
            # Use EventManager API directly
            await context.event_manager.emit(
                data={"info": "my_data"},
                sender=self.id(),
                receiver=peer.id(),
                topic=TopicType.PEER_BROADCAST,
                session_id=context.session_id,
                event_type=Constants.AGENT
            )
        
        return [self.to_action_model(result)]
```

## Design Principles Maintained

**1. Separation of Concerns:**
- BaseAgent: Core agent capabilities (policy, run, tools)
- AgentGraph: Topology structure (edges, order)
- Usage patterns: How agents use available mechanisms

**2. Mechanism vs Policy:**
- ✅ Framework provides: EventManager, TopicType.PEER_BROADCAST, Context
- ✅ Agent decides: When to send, what to send, how to receive

**3. Single Responsibility:**
- BaseAgent: Be a general-purpose agent abstraction
- Not: Know about Hybrid, Team, Workflow specifics

**4. Open/Closed:**
- BaseAgent: Closed for modification (stable interface)
- Agents: Open for extension (can implement any pattern)

## Benefits

### Architectural Clarity
- ✅ Clear boundaries: core vs extensions
- ✅ No state pollution across swarms
- ✅ Easier to understand and maintain

### Flexibility
- ✅ Other topologies can define their own patterns
- ✅ No artificial constraints from framework
- ✅ Agents have full control

### Testability
- ✅ BaseAgent tests don't need to mock topology
- ✅ Topology tests don't need to verify agent methods
- ✅ Clean separation of concerns

## Verification

**Regression Tests:** ✅ 10/10 passed
- Workflow, Team, Handoff creation
- Backward compatibility
- BuildType enum

**Core Functionality:** ✅ Verified
- BaseAgent has no peer methods
- AgentGraph has no peer state
- HybridBuilder only constructs topology

## Migration for Existing Code

**If you were using removed API:**

```python
# OLD (no longer available)
await self.share_with_peer("PeerName", data)

# NEW (use EventManager directly)
message = kwargs.get('message')
context = message.context
task = context.get_task()

peer = next((a for a in task.swarm.agents if a.name() == "PeerName"), None)
if peer:
    await context.event_manager.emit(
        data={"information": data},
        sender=self.id(),
        receiver=peer.id(),
        topic=TopicType.PEER_BROADCAST,
        session_id=context.session_id,
        event_type=Constants.AGENT
    )
```

## Files Changed

**Core:**
1. `aworld/core/agent/base.py` - Removed peer methods
2. `aworld/core/agent/swarm.py` - Removed peer state from AgentGraph, simplified HybridBuilder

**Documentation:**
3. `examples/multi_agents/hybrid/README.md` - Updated to show direct EventManager usage
4. `docs/designs/hybrid-peer-message-handling.md` - Explains mechanism vs policy

**Tests:**
5. `tests/core/test_swarm_regression.py` - Removed peer capability tests
6. `tests/core/test_concurrent_peer_comm.py` - Deleted (depended on removed API)
7. `examples/multi_agents/hybrid/data_processing/test_simple.py` - Deleted (depended on removed API)

## Conclusion

**Before:**
- BaseAgent had topology-specific peer methods
- AgentGraph stored peer state
- Tight coupling between core and topology

**After:**
- BaseAgent is pure (only core capabilities)
- AgentGraph is pure (only topology structure)
- Loose coupling: agents use EventManager directly

**Result:** Cleaner architecture, better separation of concerns, more flexible extensibility.

**Critical Fix Retained:** contextvars for task-safe context storage (prevents race conditions) remains in place.
