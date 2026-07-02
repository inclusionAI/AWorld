# Parallel Subagent Spawning

**Version:** 1.0  
**Status:** Production Ready  
**Feature Type:** Tool Enhancement

---

## Overview

The **Parallel Subagent Spawning** feature extends the `spawn_subagent` tool with a new `spawn_parallel` action that enables concurrent execution of multiple independent subagent tasks. This significantly improves performance when dealing with parallelizable workloads.

**Key Benefits:**
- ⚡ **Faster Execution:** Run multiple subagents concurrently instead of sequentially
- 🎯 **Efficient Resource Usage:** Control concurrency with semaphore-based throttling
- 🛡️ **Robust Error Handling:** Continue-on-failure or fail-fast modes
- 📊 **Flexible Output:** Human-readable summaries or structured JSON

---

## Quick Start

### Basic Example

```python
# In agent's system prompt or directive
spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {"name": "analyzer1", "directive": "Analyze dataset A"},
        {"name": "analyzer2", "directive": "Analyze dataset B"},
        {"name": "reporter", "directive": "Generate summary report"}
    ],
    max_concurrent=3,
    aggregate=True
)
```

### Prerequisites

1. **Enable Subagent Functionality:**
   ```python
   agent = Agent(
       name="coordinator",
       enable_subagent=True,  # Required
       ...
   )
   ```

2. **Configure Subagents:**
   - Add agents to TeamSwarm, OR
   - Create `agent.md` files in `.aworld/agents/`

3. **Set Up Tools:**
   - Parent agent must have `spawn_subagent` tool
   - Subagents must have required tools configured

---

## API Reference

### Action: `spawn_parallel`

**Tool Name:** `spawn_subagent`  
**Action Name:** `spawn_parallel`

#### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `tasks` | Array[Object] | Yes | - | Array of task configurations (see below) |
| `max_concurrent` | Integer | No | 10 | Maximum concurrent executions |
| `aggregate` | Boolean | No | true | Aggregate results into summary |
| `fail_fast` | Boolean | No | false | Stop on first task failure |

#### Task Object Schema

Each task in the `tasks` array must have:

```typescript
{
  name: string;           // Subagent name (must be registered)
  directive: string;      // Clear task instruction
  model?: string;         // Optional: Override model
  disallowedTools?: string; // Optional: Comma-separated tool blacklist
}
```

#### Return Value

**Aggregated Mode (`aggregate=true`):**
```markdown
## Parallel Subagent Execution Results

**Summary:** 3/3 tasks succeeded (Total execution time: 5.23s)

---

### ✅ Task 1: analyzer1
**Status:** success (took 1.8s)

**Result:**
```
[Analysis results...]
```

---

### ✅ Task 2: analyzer2
...
```

**Structured Mode (`aggregate=false`):**
```json
{
  "summary": {
    "total_tasks": 3,
    "success_count": 3,
    "failed_count": 0,
    "total_elapsed": 5.23
  },
  "tasks": [
    {
      "name": "analyzer1",
      "status": "success",
      "result": "...",
      "elapsed": 1.8
    },
    ...
  ]
}
```

---

## Usage Patterns

### Pattern 1: Data Processing Pipeline

**Scenario:** Process multiple datasets in parallel.

```python
coordinator = Agent(
    name="data_coordinator",
    system_prompt="""Process datasets A, B, C in parallel.

Use spawn_parallel:
tasks=[
    {"name": "processor", "directive": "Process dataset A"},
    {"name": "processor", "directive": "Process dataset B"},
    {"name": "processor", "directive": "Process dataset C"}
]
""",
    enable_subagent=True
)

processor = Agent(name="processor", desc="Data processing agent")

swarm = TeamSwarm(coordinator, processor)
```

**Expected Behavior:**
- 3 tasks execute concurrently
- Total time ≈ max(task_time), not sum(task_times)
- Results aggregated into summary

---

### Pattern 2: Code Review Workflow

**Scenario:** Analyze code quality, documentation, and tests simultaneously.

```python
reviewer = Agent(
    name="code_reviewer",
    system_prompt="""Review codebase comprehensively.

Spawn parallel tasks:
tasks=[
    {
        "name": "quality_checker",
        "directive": "Analyze code quality metrics",
        "disallowedTools": "write_file"  # Read-only
    },
    {
        "name": "doc_checker",
        "directive": "Verify documentation completeness"
    },
    {
        "name": "test_runner",
        "directive": "Run test suite and report coverage"
    }
]
""",
    enable_subagent=True
)

swarm = TeamSwarm(
    reviewer,
    quality_checker,
    doc_checker,
    test_runner
)
```

**Best Practices:**
- Use `disallowedTools` for security (prevent write access)
- Set appropriate `max_concurrent` (3-5 for code review)
- Enable `aggregate=True` for human-readable reports

---

### Pattern 3: Deployment Validation

**Scenario:** Run pre-deployment checks, stop on first failure.

```python
validator = Agent(
    system_prompt="""Validate deployment readiness.

Use fail_fast=True to stop on first error:
tasks=[
    {"name": "db_validator", "directive": "Validate database migrations"},
    {"name": "api_validator", "directive": "Check API endpoint health"},
    {"name": "security_validator", "directive": "Verify security configs"}
],
fail_fast=True
""",
    enable_subagent=True
)
```

**When to Use `fail_fast`:**
- ✅ Pre-deployment checks (critical validations)
- ✅ Dependency chains (later tasks depend on earlier)
- ❌ Comprehensive reports (need all results)
- ❌ Non-critical analysis (want full picture)

---

## Performance Tuning

### Choosing `max_concurrent`

| Task Type | Recommended | Reason |
|-----------|-------------|--------|
| CPU-bound | 4-8 | Match CPU cores |
| I/O-bound | 10-20 | High concurrency OK |
| API calls | 2-5 | Respect rate limits |
| LLM inference | 5-10 | Balance throughput/cost |

### Benchmarks

**Scenario:** 10 tasks, each taking 5 seconds

| `max_concurrent` | Total Time | Speedup |
|------------------|------------|---------|
| 1 (sequential) | 50s | 1x |
| 3 | 20s | 2.5x |
| 5 | 10s | 5x |
| 10 | 5s | 10x |

**Formula:**
```
total_time ≈ (num_tasks / max_concurrent) * avg_task_time
```

---

## Error Handling

### Default Behavior (`fail_fast=false`)

All tasks complete regardless of failures:

```python
tasks=[
    {"name": "task1", "directive": "..."},  # ✅ Succeeds
    {"name": "task2", "directive": "..."},  # ❌ Fails
    {"name": "task3", "directive": "..."}   # ✅ Succeeds (still runs)
]
```

**Result:**
- `success_count`: 2
- `failed_count`: 1
- `reward`: 0.67 (2/3)

**Use When:**
- Need comprehensive error report
- Failures are non-critical
- Want to see all results

---

### Fail-Fast Mode (`fail_fast=true`)

Stops on first failure, cancels remaining:

```python
tasks=[
    {"name": "critical", "directive": "..."},  # ❌ Fails first
    {"name": "task2", "directive": "..."},     # 🚫 Cancelled
    {"name": "task3", "directive": "..."}      # 🚫 Cancelled
]
```

**Result:**
- Minimal tasks completed
- Fast failure detection
- Lower token/API cost

**Use When:**
- Failures are blocking (deployment gates)
- Later tasks depend on earlier success
- Cost optimization (stop early)

---

## Advanced Usage

### Pattern: Dynamic Task Generation

```python
# Generate tasks based on runtime data
files = ["module1.py", "module2.py", "module3.py"]

tasks = [
    {
        "name": "code_analyzer",
        "directive": f"Analyze {file} for security vulnerabilities"
    }
    for file in files
]

spawn_subagent(
    action="spawn_parallel",
    tasks=tasks,
    max_concurrent=5
)
```

---

### Pattern: Heterogeneous Subagents

```python
# Use different subagent types
tasks=[
    {"name": "python_analyzer", "directive": "Analyze Python code"},
    {"name": "js_analyzer", "directive": "Analyze JavaScript code"},
    {"name": "rust_analyzer", "directive": "Analyze Rust code"}
]
```

**Tip:** Each subagent can have specialized tools and prompts.

---

### Pattern: Tool Access Control

```python
# Restrict dangerous operations
tasks=[
    {
        "name": "file_scanner",
        "directive": "Scan codebase for secrets",
        "disallowedTools": "terminal,write_file,git_commit"
    },
    {
        "name": "reporter",
        "directive": "Generate report",
        "disallowedTools": "terminal"  # Only prevent terminal access
    }
]
```

**Security Best Practice:**
- Always use least-privilege principle
- Blacklist dangerous tools for read-only tasks
- Audit tool usage in logs

---

## System Prompt Integration

Add guidance to parent agent's system prompt:

```markdown
## Available Subagents

You can delegate subtasks using spawn_subagent tool:

### Single Task
spawn_subagent(name="analyzer", directive="...")

### Parallel Tasks (Faster!)
spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {"name": "analyzer1", "directive": "..."},
        {"name": "analyzer2", "directive": "..."}
    ],
    max_concurrent=5
)

**When to Use Parallel:**
- ✅ Multiple independent subtasks
- ✅ I/O-bound operations (API calls, file reads)
- ✅ Data processing on separate datasets
- ❌ Tasks with dependencies (use sequential spawn)
- ❌ Single complex task (use regular spawn)

**Parameters:**
- max_concurrent: 3-5 for API-heavy, 10+ for I/O-bound
- aggregate: true for human summary, false for JSON
- fail_fast: true for deployment gates, false for reports
```

---

## Comparison: Sequential vs Parallel

### Sequential Spawning (Original)

```python
# Call spawn_subagent multiple times
result1 = spawn_subagent(name="task1", directive="...")
result2 = spawn_subagent(name="task2", directive="...")
result3 = spawn_subagent(name="task3", directive="...")

# Total time: T1 + T2 + T3
```

**Pros:**
- Simpler for dependent tasks
- Lower memory usage

**Cons:**
- Slower for independent tasks
- Blocks on each task

---

### Parallel Spawning (New)

```python
# Single call with task array
results = spawn_subagent(
    action="spawn_parallel",
    tasks=[
        {"name": "task1", "directive": "..."},
        {"name": "task2", "directive": "..."},
        {"name": "task3", "directive": "..."}
    ]
)

# Total time: max(T1, T2, T3)
```

**Pros:**
- **Much faster** for independent tasks
- Efficient resource usage
- Built-in error aggregation

**Cons:**
- Higher memory/API concurrency
- Not suitable for dependent tasks

---

## Troubleshooting

### Issue: "No SubagentManager available"

**Cause:** Parent agent doesn't have `enable_subagent=True`

**Fix:**
```python
agent = Agent(
    name="parent",
    enable_subagent=True,  # Add this
    ...
)
```

---

### Issue: "Subagent 'X' not found"

**Cause:** Subagent not registered (not in TeamSwarm or agent.md missing)

**Fix:**
```python
# Option 1: Add to TeamSwarm
swarm = TeamSwarm(parent, subagent_x, ...)

# Option 2: Create agent.md
# File: .aworld/agents/subagent_x.md
# ---
# name: subagent_x
# description: Does X
# tool_names: [tool1, tool2]
# ---
```

---

### Issue: Tasks completing slowly

**Diagnosis:**
1. Check `max_concurrent` (too low?)
2. Verify tasks are truly independent
3. Monitor API rate limits

**Optimization:**
```python
# Increase concurrency for I/O-bound tasks
max_concurrent=20  # Up from default 10

# Profile task execution time
# (check elapsed field in results)
```

---

### Issue: High API costs

**Cause:** Too many concurrent LLM calls

**Fix:**
```python
# Reduce concurrency
max_concurrent=3  # Lower for cost control

# Use fail_fast to stop early
fail_fast=True

# Optimize subagent prompts (shorter system prompts)
```

---

## Implementation Details

### Architecture

```
Parent Agent
    └─ spawn_subagent tool
        └─ SpawnSubagentTool.do_step()
            ├─ action="spawn" → _spawn_single()
            └─ action="spawn_parallel" → _spawn_parallel()
                 └─ _execute_tasks_parallel()
                      ├─ asyncio.Semaphore (concurrency control)
                      ├─ asyncio.gather() (parallel execution)
                      └─ SubagentManager.spawn() (per task)
```

### Concurrency Control

Uses `asyncio.Semaphore` for throttling:

```python
semaphore = asyncio.Semaphore(max_concurrent)

async def spawn_with_limit(task):
    async with semaphore:  # Acquire slot
        result = await subagent_manager.spawn(...)
    return result  # Release slot

# Only max_concurrent tasks run at once
```

### Context Isolation

Each spawned subagent gets isolated context:

```python
# Per-task isolation
sub_context = await parent_context.build_sub_context(...)
result = await subagent.execute(sub_context)
parent_context.merge_sub_context(sub_context)  # Merge back
```

**Isolated State:**
- Token usage tracking
- KV store
- Event manager

**Shared State:**
- Workspace directory
- Configuration
- Tool registry

---

## Best Practices

### ✅ Do

- Use parallel spawning for independent tasks
- Set appropriate `max_concurrent` for task type
- Use `disallowedTools` for security
- Monitor token/API usage
- Add clear directives to each task
- Use `aggregate=True` for human review
- Use `aggregate=False` for programmatic processing

### ❌ Don't

- Don't use parallel for dependent tasks (use sequential)
- Don't set `max_concurrent` too high (resource exhaustion)
- Don't ignore error handling (check failed_count)
- Don't use for single tasks (overhead not worth it)
- Don't skip security controls (always whitelist/blacklist tools)

---

## Future Enhancements

**Planned Features:**
1. Task prioritization (high-priority tasks first)
2. Dynamic concurrency adjustment (adaptive throttling)
3. Task dependencies graph (auto-sequencing)
4. Retry policies (auto-retry failed tasks)
5. Progress callbacks (real-time status updates)

**Under Consideration:**
- Task timeout per subagent
- Resource quotas (token/time limits per task)
- Result caching (skip duplicate tasks)

---

## Related Documentation

- [Subagent Architecture](../design/subagent-architecture.md)
- [SubagentManager API](../api/subagent-manager.md)
- [TeamSwarm Guide](../guides/team-swarm.md)
- [Tool Access Control](../security/tool-access-control.md)

---

## Changelog

### v1.0 (2026-04-07)
- Initial release
- Added `spawn_parallel` action to `spawn_subagent` tool
- Implemented concurrency control with `asyncio.Semaphore`
- Added aggregated and structured output modes
- Added fail-fast mode for early termination
- Comprehensive test coverage

---

---

## Background Execution

### Overview

**Background execution** enables **non-blocking subagent spawning**, allowing the orchestrator to continue working while subagents execute independently. This implements the **Fire-and-Forget** pattern with full lifecycle management.

**Key Differences from Parallel Execution:**

| Feature | `spawn_parallel` | `spawn_background` |
|---------|------------------|---------------------|
| **Blocking** | Yes (waits for all) | No (returns immediately) |
| **Use Case** | Need all results before proceeding | Orchestrator continues independently |
| **Overhead** | Lower (one-shot wait) | Higher (task tracking) |
| **Flexibility** | Lower (all-or-nothing) | Higher (selective waiting) |

### When to Use Background Execution

**✅ Use background when:**
- Orchestrator has independent work to do while tasks run
- Tasks are long-running (minutes, not seconds)
- Results are not immediately needed
- Need selective waiting (wait for specific tasks)

**❌ Use parallel instead when:**
- Need all results before proceeding
- Tasks are fast (< 10 seconds)
- Simpler all-or-nothing pattern suffices

---

### Background Actions

#### 1. `spawn_background` - Start Background Task

Spawns a subagent and returns immediately.

**Parameters:**
```typescript
{
  name: string;              // Subagent name (required)
  directive: string;         // Task description (required)
  model?: string;            // Optional model override
  disallowedTools?: string;  // Comma-separated tool blacklist
  task_id?: string;          // Custom ID (auto-generated if omitted)
}
```

**Returns:**
- Immediate return with task_id
- `info['task_id']`: Task identifier for tracking
- `info['action']`: 'spawn_background'

**Example:**
```python
spawn_subagent(
    action="spawn_background",
    name="Deep_Researcher",
    directive="Research quantum computing trends",
    task_id="research_quantum"  # Optional custom ID
)
# Returns immediately, agent runs in background
```

---

#### 2. `check_task` - Query Task Status

Check status of one or all background tasks (non-blocking).

**Parameters:**
```typescript
{
  task_id: string;           // Task ID or 'all' (required)
  include_result?: boolean;  // Include result if completed (default: true)
}
```

**Returns:**
- `info['status']`: 'running', 'completed', 'error', or 'cancelled'
- `info['elapsed']`: Elapsed time in seconds
- `info['result']`: Task result (if completed)
- `info['error']`: Error message (if status='error')

**Example:**
```python
# Check specific task
spawn_subagent(
    action="check_task",
    task_id="research_quantum",
    include_result=True
)

# Check all tasks (summary)
spawn_subagent(
    action="check_task",
    task_id="all"
)
# Returns: total_tasks, running, completed, failed counts
```

---

#### 3. `wait_task` - Wait for Completion

Block until specified tasks complete or timeout.

**Parameters:**
```typescript
{
  task_ids: string;  // Comma-separated IDs, 'any', or 'all' (required)
  timeout?: number;  // Timeout in seconds (default: 300)
}
```

**Returns:**
- `info['completed']`: Number of tasks completed
- `info['pending']`: Number still running
- `info['timed_out']`: Boolean (timeout occurred)
- `info['already_completed']`: Boolean (all were already done)

**Example:**
```python
# Wait for specific tasks
spawn_subagent(
    action="wait_task",
    task_ids="task1,task2,task3",
    timeout=120
)

# Wait for any one task
spawn_subagent(
    action="wait_task",
    task_ids="any",
    timeout=60
)

# Wait for all background tasks
spawn_subagent(
    action="wait_task",
    task_ids="all",
    timeout=300
)
```

---

#### 4. `cancel_task` - Cancel Running Task

Cancel one or all background tasks.

**Parameters:**
```typescript
{
  task_id: string;  // Task ID or 'all' (required)
}
```

**Returns:**
- `info['cancelled']`: Boolean (success for single task)
- `info['cancelled_count']`: Number cancelled (for 'all')

**Example:**
```python
# Cancel specific task
spawn_subagent(
    action="cancel_task",
    task_id="research_quantum"
)

# Cancel all background tasks
spawn_subagent(
    action="cancel_task",
    task_id="all"
)
```

---

### Background Execution Patterns

#### Pattern 1: Spawn and Forget

Fire off tasks without tracking:

```python
coordinator = Agent(
    system_prompt="""You are a research orchestrator.

Start background research tasks:
spawn_subagent(action="spawn_background", name="researcher", directive="Research AI trends")
spawn_subagent(action="spawn_background", name="researcher", directive="Research quantum computing")

Then continue your analysis work without waiting.
""",
    enable_subagent=True
)
```

**Use Case:** Fire-and-forget logging, notifications, data collection

---

#### Pattern 2: Spawn, Work, Then Collect

Start tasks, do other work, then collect results:

```python
coordinator = Agent(
    system_prompt="""Research orchestrator workflow:

1. Start background research tasks:
   spawn_subagent(action="spawn_background", name="deep_researcher", directive="...", task_id="research_1")
   spawn_subagent(action="spawn_background", name="deep_researcher", directive="...", task_id="research_2")

2. Continue with other work (analyze previous data, plan next steps, etc.)

3. Wait for research to complete:
   spawn_subagent(action="wait_task", task_ids="research_1,research_2", timeout=300)

4. Retrieve and process results:
   spawn_subagent(action="check_task", task_id="research_1", include_result=True)
   spawn_subagent(action="check_task", task_id="research_2", include_result=True)
""",
    enable_subagent=True
)
```

**Use Case:** Orchestrator with independent work while long tasks run

---

#### Pattern 3: Mixed Foreground/Background

Combine blocking and non-blocking execution:

```python
coordinator = Agent(
    system_prompt="""Execute mixed workflow:

# Start slow background task
spawn_subagent(
    action="spawn_background",
    name="deep_analyzer",
    directive="Comprehensive analysis (takes 5 minutes)",
    task_id="deep_analysis"
)

# Execute quick foreground tasks (blocks)
result = spawn_subagent(
    name="quick_validator",
    directive="Quick validation check"
)

# Check background status
spawn_subagent(action="check_task", task_id="deep_analysis")

# Wait if needed
if task_still_running:
    spawn_subagent(action="wait_task", task_ids="deep_analysis", timeout=300)
""",
    enable_subagent=True
)
```

**Use Case:** Critical path optimization (fast tasks first, wait for slow)

---

#### Pattern 4: Adaptive Waiting

Wait for any task to complete first (early exit):

```python
# Start multiple research tasks
for topic in ['AI', 'Quantum', 'Blockchain']:
    spawn_subagent(
        action="spawn_background",
        name="researcher",
        directive=f"Quick scan: {topic}",
        task_id=f"scan_{topic}"
    )

# Wait for any one to complete (first-wins)
spawn_subagent(
    action="wait_task",
    task_ids="any",  # Returns when first task completes
    timeout=60
)

# Cancel the rest
spawn_subagent(action="cancel_task", task_id="all")
```

**Use Case:** Racing algorithms, redundant research (first-to-finish)

---

### Performance Characteristics

**Benchmark Results** (3 tasks @ 500ms each):

| Mode | Spawn Time | Total Time | Speedup |
|------|------------|------------|---------|
| Sequential | N/A | 1500ms | 1x |
| `spawn_parallel` | N/A | 500ms + overhead | 3x |
| `spawn_background` | <10ms (non-blocking!) | 500ms (with overlap) | 3x |

**Key Advantage:**
- **Background**: Orchestrator can do 100ms of work *during* subagent execution
- **Parallel**: Orchestrator blocks for full execution time

**Formula:**
```
Without background: T_orchestrator + T_subagents (sequential)
With background:    max(T_orchestrator, T_subagents) (overlapped)
```

---

### Best Practices

**1. Task ID Management:**
```python
# ✅ Good: Descriptive custom IDs
task_id="research_quantum_computing_2026"

# ❌ Bad: Auto-generated only (hard to track in logs)
# Let task_id auto-generate
```

**2. Timeout Strategy:**
```python
# ✅ Set reasonable timeouts
spawn_subagent(action="wait_task", task_ids="...", timeout=300)  # 5 minutes

# ❌ No timeout (can hang forever)
spawn_subagent(action="wait_task", task_ids="...")
```

**3. Error Handling:**
```python
# ✅ Always check status
result = spawn_subagent(action="check_task", task_id="task1")
if result['status'] == 'error':
    handle_error(result['error'])

# ❌ Assume success
result = spawn_subagent(action="check_task", task_id="task1")
process(result['result'])  # May not exist!
```

**4. Resource Management:**
```python
# ✅ Limit concurrent background tasks
MAX_BACKGROUND = 5
if len(active_tasks) < MAX_BACKGROUND:
    spawn_subagent(action="spawn_background", ...)

# ❌ Spawn unlimited tasks (resource exhaustion)
for i in range(1000):
    spawn_subagent(action="spawn_background", ...)
```

---

### Comparison: Parallel vs Background

| Aspect | `spawn_parallel` | `spawn_background` |
|--------|------------------|---------------------|
| **Orchestrator Blocking** | Yes | No |
| **Task Tracking** | Automatic | Manual (via task_id) |
| **Result Collection** | Automatic aggregation | Manual retrieval |
| **Use Complexity** | Low (one call) | Medium (spawn + wait + check) |
| **Flexibility** | Low (all-or-nothing) | High (selective waiting) |
| **Performance** | Good (if no orchestrator work) | Better (if orchestrator has work) |
| **Memory Overhead** | Lower | Higher (task registry) |

**Decision Tree:**

```
Do you need all results before proceeding?
├─ Yes → Use spawn_parallel
└─ No → Does orchestrator have independent work?
    ├─ Yes → Use spawn_background
    └─ No → Use spawn_parallel (simpler)
```

---

### Implementation Details

**Task Registry:**
```python
_background_tasks: Dict[str, Dict[str, Any]] = {
    'task_id': {
        'task': asyncio.Task,        # Running task
        'name': str,                 # Subagent name
        'directive': str,            # Task description
        'start_time': float,         # Start timestamp
        'status': str,               # 'running', 'completed', 'error', 'cancelled'
        'result': Optional[str],     # Task result
        'error': Optional[str]       # Error message
    }
}
```

**Thread Safety:**
```python
# All registry access protected by lock
self._bg_lock = asyncio.Lock()

async with self._bg_lock:
    self._background_tasks[task_id] = {...}
```

**Task Lifecycle:**
```
Created ──> Running ──┬──> Completed
                      ├──> Error
                      └──> Cancelled
```

---

### Testing

**Unit Tests:** `tests/core/tool/test_spawn_background.py`
- 18 comprehensive test cases
- Coverage: spawn, check, wait, cancel, error handling
- Validation: thread safety, status transitions, timeout behavior

**Integration Tests:** `examples/subagent_integration/test_background_spawn.py`
- Real-world orchestrator scenarios
- Performance validation (3x speedup)
- Mixed foreground/background patterns

**Run Tests:**
```bash
# Unit tests
pytest tests/core/tool/test_spawn_background.py -v

# Integration tests
python examples/subagent_integration/test_background_spawn.py
```

---

## Support

**Questions?** Open an issue on GitHub  
**Feature Requests?** Submit a proposal via discussions  
**Bugs?** File a bug report with reproduction steps
