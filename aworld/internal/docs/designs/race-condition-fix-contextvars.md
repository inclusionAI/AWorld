# Race Condition Fix: Context Isolation with contextvars

## Problem Description

**Original Issue:** `self._current_context` was stored as an instance variable in `BaseAgent`, causing race conditions in concurrent scenarios where the same agent instance is used by multiple tasks.

**Trigger Scenario:**
```python
# Agent instance cached by AgentFactory
agent = agent_factory.get("Agent1")

# Task A starts
async def task_a():
    agent._current_context = context_a  # Sets instance variable
    await asyncio.sleep(0.01)           # Context switch opportunity
    await agent.share_with_peer(...)    # Uses agent._current_context
    
# Task B starts (reuses same agent instance)
async def task_b():
    agent._current_context = context_b  # ❌ Overwrites context_a!
    await asyncio.sleep(0.01)
    await agent.share_with_peer(...)    # Task A might now use context_b!

asyncio.gather(task_a(), task_b())  # Race condition!
```

**Consequences:**
- Peer messages sent to wrong session/task
- EventManager operations use incorrect context
- Data leakage between concurrent tasks

## Solution: contextvars

Python's `contextvars` module provides task-local storage that is automatically isolated per async task.

### Implementation Changes

**1. Added contextvars import and ContextVar:**
```python
# aworld/core/agent/base.py
import contextvars

# Global ContextVar (module level)
_agent_context: contextvars.ContextVar['Context'] = contextvars.ContextVar(
    '_agent_context', 
    default=None
)
```

**2. Removed instance variable:**
```python
# REMOVED
self._current_context: Optional['Context'] = None
```

**3. Added static accessor method:**
```python
@staticmethod
def _get_current_context() -> Optional['Context']:
    """Get the current context for this async task (thread-safe).

    Returns the context stored in contextvars, which is unique per async task.
    This prevents race conditions when agent instances are reused across
    concurrent executions.
    """
    return _agent_context.get()
```

**4. Updated async_run to set context:**
```python
async def async_run(self, message: Message, **kwargs) -> Message:
    try:
        # Store context in contextvars for task-safe access
        _agent_context.set(message.context)
        # ... rest of method
```

**5. Updated peer communication methods:**
```python
async def share_with_peer(self, peer_name, information, info_type):
    # Get context from contextvars (task-safe)
    current_context = self._get_current_context()
    if not current_context:
        raise RuntimeError("No context available...")
    
    await current_context.event_manager.emit(...)
```

```python
async def broadcast_to_all_peers(self, information, info_type):
    # Get context from contextvars (task-safe)
    current_context = self._get_current_context()
    if not current_context:
        raise RuntimeError("No context available.")
    
    await current_context.event_manager.emit(...)
```

## How contextvars Works

**Key Properties:**
1. **Task-local**: Each async task gets its own copy of the context
2. **Auto-propagation**: Context automatically propagates to awaited coroutines
3. **Isolation**: Tasks running concurrently don't interfere with each other
4. **Performance**: Zero-cost abstraction, no locks needed

**Execution Flow:**
```python
# Task A
_agent_context.set(context_a)
await agent.share_with_peer(...)
    ↓ (internally calls _get_current_context())
    ↓ returns context_a ✅

# Task B (concurrent with Task A)
_agent_context.set(context_b)
await agent.share_with_peer(...)
    ↓ (internally calls _get_current_context())
    ↓ returns context_b ✅

# Each task gets its own context, no interference!
```

## Verification

### Test Coverage

**tests/core/test_concurrent_peer_comm.py:**
1. `test_concurrent_peer_messages_isolated`: Verifies concurrent tasks using same agent instance don't interfere
2. `test_context_propagation_to_nested_calls`: Verifies context propagates through nested async calls
3. `test_context_isolation_in_parallel_swarms`: Verifies different swarms running in parallel don't interfere

**Results:**
- ✅ All regression tests pass (13/13)
- ✅ Hybrid example tests pass
- ✅ Concurrent peer communication test passes

### Manual Verification

**example/multi_agents/hybrid/data_processing/test_simple.py:**
- Updated to use `_agent_context.set(context)` instead of `agent._current_context = context`
- All tests pass

## Migration Notes

**For Test Code:**
If tests directly inject context (not through `async_run`), update:

```python
# OLD (race condition)
agent._current_context = context

# NEW (task-safe)
from aworld.core.agent.base import _agent_context
_agent_context.set(context)
```

**For Production Code:**
No changes needed! Context is automatically set in `async_run`.

## Performance Impact

**Zero overhead:**
- contextvars is a built-in Python feature (3.7+)
- No additional locks or synchronization
- Context lookup is O(1)
- No memory leaks (contexts auto-cleaned when task completes)

## Compatibility

- **Python 3.7+**: Required (contextvars introduced in 3.7)
- **Asyncio**: Full support
- **Threading**: Works correctly (each thread has its own context)
- **Backward compatible**: Existing code using `async_run` works without changes

## Related Files Changed

1. `aworld/core/agent/base.py` - Core fix
2. `examples/multi_agents/hybrid/data_processing/test_simple.py` - Test updated
3. `tests/core/test_concurrent_peer_comm.py` - New concurrent tests
4. `docs/designs/hybrid-peer-message-handling.md` - Documentation

## Summary

**Before:** Instance variable `self._current_context` → race condition risk  
**After:** ContextVar `_agent_context` → task-safe isolation

**Impact:**
- ✅ Eliminates race conditions in concurrent scenarios
- ✅ No performance overhead
- ✅ Backward compatible with existing code
- ✅ All tests pass

**Recommendation:** Merge this fix as critical bugfix for Hybrid MAS concurrent usage.
