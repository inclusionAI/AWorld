# Subagent Trajectory Design

## Overview

This document describes how to extend the existing trajectory system to support subagent delegation patterns, enabling training data collection for hierarchical multi-agent systems.

## Current Trajectory Architecture

**SAR Structure:**
```python
TrajectoryItem:
  - meta: ExpMeta (session_id, task_id, agent_id, step, pre_agent)
  - state: TrajectoryState (input, messages, context)
  - action: TrajectoryAction (content, tool_calls, is_agent_finished)
  - reward: TrajectoryReward (tool_outputs, status, score)
```

**Key Components:**
- `DefaultTrajectoryStrategy`: Generates SAR from Message events
- `FilteredTrajectoryStrategy`: Filters by agent_id/step
- `message_to_trajectory_item()`: Core conversion logic

## Subagent-Specific Requirements

### 1. Hierarchical Relationships

**Need to track:**
- Parent-child agent relationships
- Delegation directives (why subagent was spawned)
- Nesting depth (support multi-level delegation)

**Solution: Extend ExpMeta**
```python
class SubagentExpMeta(ExpMeta):
    parent_agent_id: Optional[str] = None        # Parent agent ID
    subagent_depth: int = 0                      # 0=root, 1=direct child, 2=grandchild
    spawn_directive: Optional[str] = None        # Delegation instruction
    spawn_reason: Optional[str] = None           # LLM's reasoning for spawning
```

### 2. Tool Access Control Tracking

**Need to record:**
- Which tools were available to subagent
- Why certain tools were restricted
- Tool usage patterns under constraints

**Solution: Extend TrajectoryState**
```python
class SubagentTrajectoryState(TrajectoryState):
    allowed_tools: List[str]                     # Tools accessible to this agent
    disallowed_tools: List[str]                  # Blacklisted tools
    tool_filter_reason: Optional[str] = None     # Why tools were restricted
```

### 3. Context Isolation Metadata

**Need to track:**
- Whether subagent used isolated context
- Token usage attribution (parent vs child)
- Context merge points

**Solution: Add context isolation info**
```python
class SubagentContextInfo:
    context_isolated: bool                       # Used build_sub_context()?
    parent_context_id: Optional[str] = None      # Parent context ID
    merge_timestamp: Optional[float] = None      # When merged back
    token_usage_split: Dict[str, int]           # Separate parent/child tokens
```

### 4. Result Aggregation

**Need to record:**
- Subagent's output to parent
- Whether parent used the result
- How result influenced parent's next action

**Solution: Extend TrajectoryReward**
```python
class SubagentTrajectoryReward(TrajectoryReward):
    spawn_result: Optional[str] = None           # Subagent's return value
    parent_usage: Optional[str] = None           # How parent used result
    result_quality_score: Optional[float] = None # Quality assessment
```

## Implementation Strategy

### Phase 1: Extend Data Models

Create subagent-specific trajectory types in `aworld/dataset/types.py`:

```python
from dataclasses import dataclass
from typing import Optional, List, Dict

@dataclass
class SubagentExpMeta(ExpMeta):
    """Extended metadata for subagent trajectories."""
    parent_agent_id: Optional[str] = None
    subagent_depth: int = 0
    spawn_directive: Optional[str] = None
    spawn_reason: Optional[str] = None
    
    def to_dict(self):
        base = super().to_dict()
        base.update({
            'parent_agent_id': self.parent_agent_id,
            'subagent_depth': self.subagent_depth,
            'spawn_directive': self.spawn_directive,
            'spawn_reason': self.spawn_reason
        })
        return base

@dataclass
class SubagentTrajectoryState(TrajectoryState):
    """Extended state with tool access control info."""
    allowed_tools: List[str] = None
    disallowed_tools: List[str] = None
    tool_filter_reason: Optional[str] = None
    context_isolated: bool = False
    parent_context_id: Optional[str] = None
    
    def to_dict(self):
        base = super().to_dict()
        base.update({
            'allowed_tools': self.allowed_tools,
            'disallowed_tools': self.disallowed_tools,
            'tool_filter_reason': self.tool_filter_reason,
            'context_isolated': self.context_isolated,
            'parent_context_id': self.parent_context_id
        })
        return base

@dataclass
class SubagentTrajectoryReward(TrajectoryReward):
    """Extended reward with subagent result info."""
    spawn_result: Optional[str] = None
    parent_usage: Optional[str] = None
    result_quality_score: Optional[float] = None
    
    def to_dict(self):
        base = super().to_dict()
        base.update({
            'spawn_result': self.spawn_result,
            'parent_usage': self.parent_usage,
            'result_quality_score': self.result_quality_score
        })
        return base
```

### Phase 2: SubagentTrajectoryStrategy

Create `aworld/dataset/subagent_strategy.py`:

```python
from typing import Any, Optional
from aworld.dataset.trajectory_strategy import DefaultTrajectoryStrategy
from aworld.dataset.types import TrajectoryItem, ExpMeta, TrajectoryState, TrajectoryReward
from aworld.logs.util import logger

class SubagentTrajectoryStrategy(DefaultTrajectoryStrategy):
    """
    Trajectory strategy for subagent delegation scenarios.
    
    Extends DefaultTrajectoryStrategy to capture:
    - Parent-child relationships
    - Tool access control
    - Context isolation
    - Result aggregation
    """
    
    async def build_trajectory_state(self, source: Any, **kwargs) -> Optional[TrajectoryState]:
        """Build state with subagent-specific metadata."""
        # Get base state from parent class
        base_state = await super().build_trajectory_state(source, **kwargs)
        
        # Extract subagent metadata from context
        ctx = getattr(source, 'context', None)
        if not ctx:
            return base_state
        
        # Check if this is a subagent execution
        subagent_meta = getattr(ctx, 'subagent_metadata', None)
        if not subagent_meta:
            return base_state  # Not a subagent, use base state
        
        # Extend with subagent info
        from aworld.dataset.types import SubagentTrajectoryState
        
        return SubagentTrajectoryState(
            input=base_state.input,
            messages=base_state.messages,
            allowed_tools=subagent_meta.get('allowed_tools', []),
            disallowed_tools=subagent_meta.get('disallowed_tools', []),
            tool_filter_reason=subagent_meta.get('filter_reason'),
            context_isolated=subagent_meta.get('context_isolated', False),
            parent_context_id=subagent_meta.get('parent_context_id')
        )
    
    async def build_trajectory_reward(self, source: Any, **kwargs) -> Optional[TrajectoryReward]:
        """Build reward with subagent result info."""
        base_reward = await super().build_trajectory_reward(source, **kwargs)
        
        ctx = getattr(source, 'context', None)
        subagent_meta = getattr(ctx, 'subagent_metadata', None) if ctx else None
        
        if not subagent_meta:
            return base_reward
        
        from aworld.dataset.types import SubagentTrajectoryReward
        
        return SubagentTrajectoryReward(
            tool_outputs=base_reward.tool_outputs,
            status=base_reward.status,
            score=base_reward.score,
            spawn_result=subagent_meta.get('spawn_result'),
            parent_usage=subagent_meta.get('parent_usage'),
            result_quality_score=subagent_meta.get('result_quality')
        )
    
    async def message_to_trajectory_item(
        self,
        message: Any,
        state_manager: Any = None,
        use_tools_in_prompt: bool = False
    ) -> Optional[TrajectoryItem]:
        """Build trajectory item with subagent metadata."""
        from aworld.dataset.types import SubagentExpMeta
        
        # Get base item
        base_item = await super().message_to_trajectory_item(
            message, state_manager, use_tools_in_prompt
        )
        
        if not base_item:
            return None
        
        # Check for subagent metadata
        ctx = getattr(message, 'context', None)
        subagent_meta = getattr(ctx, 'subagent_metadata', None) if ctx else None
        
        if not subagent_meta:
            return base_item  # Not a subagent execution
        
        # Create extended meta
        extended_meta = SubagentExpMeta(
            session_id=base_item.meta.session_id,
            task_id=base_item.meta.task_id,
            task_name=base_item.meta.task_name,
            agent_id=base_item.meta.agent_id,
            step=base_item.meta.step,
            execute_time=base_item.meta.execute_time,
            pre_agent=base_item.meta.pre_agent,
            parent_agent_id=subagent_meta.get('parent_agent_id'),
            subagent_depth=subagent_meta.get('depth', 0),
            spawn_directive=subagent_meta.get('directive'),
            spawn_reason=subagent_meta.get('spawn_reason')
        )
        
        # Build extended state and reward
        state = await self.build_trajectory_state(message, state_manager=state_manager)
        reward = await self.build_trajectory_reward(message, state_manager=state_manager)
        
        return TrajectoryItem(
            id=base_item.id,
            meta=extended_meta,
            state=state,
            action=base_item.action,
            reward=reward
        )
```

### Phase 3: Integrate with SubagentManager

Modify `SubagentManager.spawn()` to add trajectory metadata:

```python
# In SubagentManager.spawn() method (after line 620)

async def spawn(
    self,
    name: str,
    directive: str,
    model: Optional[str] = None,
    disallowedTools: Optional[List[str]] = None
) -> str:
    # ... existing validation code ...
    
    # Build sub-context
    sub_context = parent_context.build_sub_context()
    
    # *** ADD SUBAGENT METADATA FOR TRAJECTORY ***
    sub_context.subagent_metadata = {
        'parent_agent_id': self.agent.id(),
        'depth': self._get_subagent_depth(parent_context),
        'directive': directive,
        'allowed_tools': list(filtered_tools),
        'disallowed_tools': disallowed or [],
        'filter_reason': f'Subagent {name} tool access control',
        'context_isolated': True,
        'parent_context_id': parent_context.session_id,
        'spawn_reason': None  # Will be filled by LLM reasoning
    }
    
    # ... rest of spawn logic ...
    
    # After subagent execution, record result
    sub_context.subagent_metadata['spawn_result'] = str(result)
    sub_context.subagent_metadata['result_quality'] = self._assess_result_quality(result)
    
    # Merge context (existing code handles this)
    parent_context.merge_sub_context(sub_context)
    
    return str(result)

def _get_subagent_depth(self, context: Context) -> int:
    """Calculate nesting depth from context metadata."""
    parent_meta = getattr(context, 'subagent_metadata', None)
    if parent_meta:
        return parent_meta.get('depth', 0) + 1
    return 1  # Direct child of root agent

def _assess_result_quality(self, result: Any) -> Optional[float]:
    """Simple heuristic for result quality (can be improved with LLM evaluation)."""
    if not result:
        return 0.0
    result_str = str(result)
    if 'error' in result_str.lower() or 'failed' in result_str.lower():
        return 0.3
    if len(result_str) < 10:  # Too short
        return 0.5
    return 1.0  # Success
```

### Phase 4: Filtering Strategy for Analysis

Create filters to analyze subagent patterns:

```python
class SubagentOnlyTrajectoryStrategy(FilteredTrajectoryStrategy):
    """Filter to only include subagent executions."""
    
    def filter_by_item(self, item: Dict[str, Any]) -> bool:
        meta = item.get('meta', {})
        # Keep only items with parent_agent_id (subagents)
        return 'parent_agent_id' in meta and meta['parent_agent_id'] is not None

class DepthFilteredTrajectoryStrategy(FilteredTrajectoryStrategy):
    """Filter by subagent nesting depth."""
    
    def __init__(self, max_depth: int = 1):
        self.max_depth = max_depth
    
    def filter_by_item(self, item: Dict[str, Any]) -> bool:
        meta = item.get('meta', {})
        depth = meta.get('subagent_depth', 0)
        return depth <= self.max_depth
```

## Usage Examples

### Example 1: Collect Subagent Delegation Patterns

```python
from aworld.dataset.subagent_strategy import SubagentTrajectoryStrategy
from aworld.dataset.trajectory_dataset import TrajectoryDataset

# Use subagent-aware strategy
strategy = SubagentTrajectoryStrategy()
dataset = TrajectoryDataset(
    strategy=strategy,
    enable_storage=True
)

# Run tasks with subagent delegation
result = await Runners.async_run(
    input="Complex task requiring delegation",
    swarm=team_swarm,
    trajectory_dataset=dataset
)

# Trajectories now include parent-child relationships
trajectories = await dataset.get_trajectories(task_id=result.task_id)

for item in trajectories:
    if item['meta'].get('parent_agent_id'):
        print(f"Subagent: {item['meta']['agent_id']}")
        print(f"Parent: {item['meta']['parent_agent_id']}")
        print(f"Directive: {item['meta']['spawn_directive']}")
        print(f"Tools: {item['state']['allowed_tools']}")
        print()
```

### Example 2: Analyze Tool Access Patterns

```python
# Filter trajectories to analyze tool restrictions
from collections import Counter

tool_usage = Counter()
for item in trajectories:
    if 'allowed_tools' in item['state']:
        for tool in item['state']['allowed_tools']:
            tool_usage[tool] += 1

print("Most restricted tools for subagents:")
print(tool_usage.most_common(10))
```

### Example 3: Training Data for Delegation Policy

```python
# Extract delegation patterns for training
delegation_patterns = []

for item in trajectories:
    meta = item['meta']
    if meta.get('parent_agent_id'):
        pattern = {
            'parent_task': item['state']['input'],
            'spawn_directive': meta['spawn_directive'],
            'subagent_type': meta['agent_id'],
            'tools_given': item['state']['allowed_tools'],
            'result_quality': item['reward'].get('result_quality_score', 0.0)
        }
        delegation_patterns.append(pattern)

# Use for training delegation policy
# (e.g., "given task X, spawn subagent Y with tools Z yields quality Q")
```

## Integration Checklist

- [ ] Extend data models (SubagentExpMeta, SubagentTrajectoryState, SubagentTrajectoryReward)
- [ ] Implement SubagentTrajectoryStrategy
- [ ] Modify SubagentManager.spawn() to add metadata
- [ ] Add depth calculation helper
- [ ] Add result quality assessment
- [ ] Create filtering strategies (SubagentOnlyTrajectoryStrategy, DepthFilteredTrajectoryStrategy)
- [ ] Update TrajectoryDataset to support subagent strategy
- [ ] Add integration tests
- [ ] Document usage examples

## Future Enhancements

1. **LLM-Based Result Quality Assessment**
   - Use LLM to evaluate subagent result quality
   - Compare result against directive
   - Assess parent's actual usage of result

2. **Delegation Policy Learning**
   - Train model to predict: task → best subagent + tools
   - Use trajectory data to optimize delegation decisions
   - Learn from successful vs failed delegations

3. **Multi-Level Trajectory Aggregation**
   - Roll up grandchild → child → parent trajectories
   - Analyze full delegation chains
   - Identify bottlenecks in hierarchical execution

4. **Tool Restriction Impact Analysis**
   - Correlate tool restrictions with success rate
   - Identify minimal tool sets for subagents
   - Optimize tool access policies

## References

- Current trajectory implementation: `aworld/dataset/trajectory_strategy.py`
- Subagent core logic: `aworld/core/agent/subagent_manager.py`
- Context isolation: `aworld/core/context/base.py` (build_sub_context/merge_sub_context)
- Integration test demo: `examples/subagent_integration/`
