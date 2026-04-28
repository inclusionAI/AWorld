# RalphRunner Dual-Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `RalphRunner` into a framework-level dual-mode Ralph loop that preserves the current public API, adds explicit `reuse_context` and `fresh_context` execution modes, introduces a thin loop memory abstraction over existing AWorld persistence primitives, and wires real verification into the framework loop.

**Architecture:** Keep `RalphRunner` and `aworld.runner.ralph_run(...)` as the public entrypoints, but refactor the internal loop into explicit policy, memory, iteration-input, and evaluation components. Reuse `LoopContext`, `WorkSpace`, and sandbox file/terminal capabilities rather than inventing a parallel storage or orchestration layer. Stage the work so compatibility and structural seams land before behavior changes such as `fresh_context` reconstruction and verification execution.

**Tech Stack:** Python 3.12, pytest, existing AWorld runner/context/workspace/sandbox stack, `Sandbox.builder()` filesystem and terminal namespaces.

## Execution Status

Status: Implemented on April 27, 2026.

Delivered commits:

- `053c43aa` `feat: add explicit ralph execution mode config`
- `4a69a74f` `refactor: extract ralph loop memory store`
- `74bdc286` `fix: restore ralph loop memory compatibility`
- `b70d3042` `refactor: add dual-mode ralph iteration input builder`
- `03112a6e` `fix: seed fresh ralph contexts from iteration input`
- `28ade7ea` `feat: add ralph verification pipeline`
- `4db0b847` `fix: gate ralph verification scheduling`
- `3ba5731e` `fix: persist ralph iteration evaluation state`

Verification executed on the final integrated branch:

```bash
pytest tests/runners/test_ralph_runner_dual_mode.py tests/runners/test_ralph_loop_memory_store.py tests/runners/test_ralph_iteration_evaluator.py tests/runners/test_tool_reset_compat.py -q
```

Expected/actual result:

- PASS (`32 passed`)

All implementation tasks below are complete. The checkbox steps are preserved as execution history for the delivery sequence.

---

## File Structure

### Existing files to modify

- Modify: `aworld/runners/ralph/config.py`
- Modify: `aworld/runners/ralph/state.py`
- Modify: `aworld/runners/ralph_runner.py`
- Modify: `aworld/runner.py`

Responsibilities:

- `config.py`: explicit execution mode and verify config, plus compatibility mapping from `reuse_context`
- `state.py`: move ad hoc read/write behavior behind structured loop memory helpers and enable optional terminal-backed verification
- `ralph_runner.py`: orchestrate the loop via policy, input builder, and evaluator instead of inline mode branching
- `runner.py`: preserve the public `ralph_run(...)` entrypoint while allowing config-driven dual-mode behavior

### New internal modules to create

- Create: `aworld/runners/ralph/policy.py`
- Create: `aworld/runners/ralph/memory.py`
- Create: `aworld/runners/ralph/input_builder.py`
- Create: `aworld/runners/ralph/evaluator.py`

Responsibilities:

- `policy.py`: normalize effective Ralph config, execution mode, and compatibility behavior
- `memory.py`: thin `LoopMemoryStore` over existing artifact/file persistence
- `input_builder.py`: build per-iteration task input for both `reuse_context` and `fresh_context`
- `evaluator.py`: run verify/summary/reflection after each iteration and persist results back into loop memory

### Tests to add or modify

- Add: `tests/runners/test_ralph_runner_dual_mode.py`
- Add: `tests/runners/test_ralph_loop_memory_store.py`
- Add: `tests/runners/test_ralph_iteration_evaluator.py`
- Modify: `tests/runners/__init__.py` only if test package imports require it

Responsibilities:

- `test_ralph_runner_dual_mode.py`: compatibility mapping, runner mode behavior, public API preservation
- `test_ralph_loop_memory_store.py`: artifact-first and file-assisted loop memory persistence
- `test_ralph_iteration_evaluator.py`: verify result shaping and post-iteration feedback behavior

---

### Task 1: Freeze Public Ralph API and Config Compatibility Before Refactor

**Files:**
- Add: `tests/runners/test_ralph_runner_dual_mode.py`
- Modify: `aworld/runners/ralph/config.py`

- [ ] **Step 1: Add a failing test for the new explicit execution mode while preserving old `reuse_context` behavior**

```python
from aworld.runners.ralph.config import RalphConfig


def test_ralph_config_defaults_to_reuse_context_execution_mode():
    config = RalphConfig()

    assert config.execution_mode == "reuse_context"
    assert config.reuse_context is True
```

- [ ] **Step 2: Add a failing test for compatibility mapping when `reuse_context=False` is still used**

```python
from aworld.runners.ralph.policy import RalphLoopPolicy
from aworld.runners.ralph.config import RalphConfig


def test_ralph_loop_policy_maps_reuse_context_false_to_fresh_context():
    config = RalphConfig()
    config.reuse_context = False

    policy = RalphLoopPolicy.from_config(config)

    assert policy.execution_mode == "fresh_context"
```

- [ ] **Step 3: Add the minimal config surface to make those tests pass**

```python
@dataclass
class RalphVerifyConfig:
    enabled: bool = False
    commands: list[str] = field(default_factory=list)
    run_on_each_iteration: bool = False
    run_before_completion: bool = True
    success_policy: str = "all"
    max_output_chars: int = 12000


class RalphConfig(TaskConfig):
    execution_mode: str = Field(default="reuse_context")
    verify: RalphVerifyConfig = Field(default_factory=RalphVerifyConfig)
    reuse_context: bool = Field(default=True)
```

- [ ] **Step 4: Run the new compatibility tests**

Run:
```bash
pytest tests/runners/test_ralph_runner_dual_mode.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit the config-compatibility slice**

```bash
git add aworld/runners/ralph/config.py tests/runners/test_ralph_runner_dual_mode.py
git commit -m "feat: add explicit ralph execution mode config"
```

---

### Task 2: Extract `RalphLoopPolicy` and `LoopMemoryStore` Over Existing AWorld Persistence

**Files:**
- Create: `aworld/runners/ralph/policy.py`
- Create: `aworld/runners/ralph/memory.py`
- Modify: `aworld/runners/ralph/state.py`
- Add: `tests/runners/test_ralph_loop_memory_store.py`

- [ ] **Step 1: Add failing tests for artifact-first and file-assisted memory storage**

```python
import pytest

from aworld.runners.ralph.memory import LoopMemoryStore
from aworld.runners.ralph.state import LoopContext, LoopState
from aworld.runners.ralph.types import CompletionCriteria


@pytest.mark.asyncio
async def test_loop_memory_store_round_trips_iteration_summary(tmp_path):
    context = LoopContext(
        completion_criteria=CompletionCriteria(),
        loop_state=LoopState(),
        work_dir=str(tmp_path),
    )
    store = LoopMemoryStore(context)

    await store.write_iteration_summary("task-1", 1, "summary text")

    assert await store.read_iteration_summary("task-1", 1) == "summary text"
```

- [ ] **Step 2: Add the policy object that centralizes effective mode resolution**

```python
from dataclasses import dataclass

from aworld.runners.ralph.config import RalphConfig


@dataclass
class RalphLoopPolicy:
    execution_mode: str
    verify_enabled: bool

    @classmethod
    def from_config(cls, config: RalphConfig) -> "RalphLoopPolicy":
        mode = config.execution_mode
        if "execution_mode" not in config.__dict__:
            mode = "reuse_context" if config.reuse_context else "fresh_context"
        return cls(execution_mode=mode, verify_enabled=bool(config.verify.enabled))
```

- [ ] **Step 3: Add `LoopMemoryStore` as a thin adapter on top of `workspace` and `sandbox.file`**

```python
class LoopMemoryStore:
    def __init__(self, context: LoopContext):
        self.context = context

    async def write_iteration_summary(self, task_id: str, iteration: int, text: str) -> None:
        artifact = Artifact(
            artifact_id=f"{self.context.summary_dir()}_{task_id}_{iteration}",
            artifact_type=ArtifactType.TEXT,
            content=text,
            metadata={"task_id": task_id, "iteration": iteration, "kind": "summary"},
        )
        await self.context.workspace.add_artifact(artifact, index=False)
```

- [ ] **Step 4: Update `LoopContext` so sandbox tools can support both file memory and later verify execution**

```python
self.sand_box = (
    Sandbox.builder()
    .builtin_tools(["filesystem", "terminal"])
    .workspaces([work_dir])
    .build()
)
```

- [ ] **Step 5: Run the loop-memory tests**

Run:
```bash
pytest tests/runners/test_ralph_loop_memory_store.py -q
```

Expected:
- PASS

- [ ] **Step 6: Commit the policy and memory extraction slice**

```bash
git add aworld/runners/ralph/policy.py aworld/runners/ralph/memory.py aworld/runners/ralph/state.py tests/runners/test_ralph_loop_memory_store.py tests/runners/test_ralph_runner_dual_mode.py
git commit -m "refactor: extract ralph loop policy and memory store"
```

---

### Task 3: Replace Ad Hoc Prompt Injection With `IterationInputBuilder`

**Files:**
- Create: `aworld/runners/ralph/input_builder.py`
- Modify: `aworld/runners/ralph/state.py`
- Modify: `aworld/runners/ralph_runner.py`
- Modify: `tests/runners/test_ralph_runner_dual_mode.py`

- [ ] **Step 1: Add a failing test that distinguishes `reuse_context` from `fresh_context`**

```python
import pytest

from aworld.runners.ralph.input_builder import IterationInputBuilder


@pytest.mark.asyncio
async def test_iteration_input_builder_fresh_context_includes_original_task_and_memory(tmp_path):
    builder = IterationInputBuilder(...)

    payload = await builder.build(
        task_input="Build a REST API",
        iteration=2,
        previous_answer="Created app.py",
        reflection_feedback="Add tests next",
    )

    assert "Original task:" in payload.task_input
    assert "Previous answer summary:" in payload.task_input
    assert "Add tests next" in payload.task_input
```

- [ ] **Step 2: Add a normalized input builder contract**

```python
from dataclasses import dataclass


@dataclass
class IterationInput:
    task_input: str
    reuse_context: bool


class IterationInputBuilder:
    async def build(... ) -> IterationInput:
        ...
```

- [ ] **Step 3: Move the current `read_to_task_context(...)` string assembly into the new builder**

```python
sections = [
    "Original task:",
    original_task,
    "",
    f"Iteration: {iteration}",
    "",
    "Previous answer summary:",
    previous_answer or "No previous answer available.",
    "",
    "Reflection feedback:",
    reflection_feedback or "No reflection feedback available.",
]
```

- [ ] **Step 4: Update `RalphRunner._execute_task(...)` to ask the builder for the next iteration input**

```python
iteration_input = await self.input_builder.build(...)
self.task_context = await self._build_iteration_context(iteration_input, task, iter_num)
task.input = iteration_input.task_input
task.context = self.task_context
```

- [ ] **Step 5: Run runner-mode tests**

Run:
```bash
pytest tests/runners/test_ralph_runner_dual_mode.py -q
```

Expected:
- PASS

- [ ] **Step 6: Commit the input-builder slice**

```bash
git add aworld/runners/ralph/input_builder.py aworld/runners/ralph/state.py aworld/runners/ralph_runner.py tests/runners/test_ralph_runner_dual_mode.py
git commit -m "refactor: add ralph iteration input builder"
```

---

### Task 4: Refactor `RalphRunner` Into an Explicit Dual-Mode Loop While Preserving Entry Points

**Files:**
- Modify: `aworld/runners/ralph_runner.py`
- Modify: `aworld/runner.py`
- Modify: `tests/runners/test_ralph_runner_dual_mode.py`

- [ ] **Step 1: Add a failing test that `aworld.runner.ralph_run(...)` still works with the new config path**

```python
import pytest

from aworld.core.task import Task
from aworld.runner import Runner
from aworld.runners.ralph.config import RalphConfig
from aworld.runners.ralph.types import CompletionCriteria


@pytest.mark.asyncio
async def test_runner_ralph_run_preserves_public_api(monkeypatch):
    task = Task(input="Build API", conf=RalphConfig())

    result = await Runner.ralph_run(task, CompletionCriteria(max_iterations=1))

    assert result is not None
```

- [ ] **Step 2: Add runner seams for explicit mode-based context construction**

```python
async def _build_iteration_context(self, iteration_input, task, iter_num):
    if self.policy.execution_mode == "reuse_context":
        return to_loop_context(self.loop_context, work_dir=self.ralph_config.workspace)
    return to_loop_context(
        await self.loop_context.build_sub_context(
            sub_task_content=iteration_input.task_input,
            sub_task_id=task.id,
            task=task,
        ),
        work_dir=self.ralph_config.workspace,
    )
```

- [ ] **Step 3: Keep the public constructor unchanged while initializing new internal collaborators**

```python
self.policy = RalphLoopPolicy.from_config(self.ralph_config)
self.memory_store = LoopMemoryStore(self.loop_context)
self.input_builder = IterationInputBuilder(self.policy, self.memory_store)
```

- [ ] **Step 4: Make `runner.py` pass through the same config without adding a second public API**

```python
runner = RalphRunner(task=task, completion_criteria=completion_criteria)
return await runner.run()
```

- [ ] **Step 5: Run runner compatibility tests**

Run:
```bash
pytest tests/runners/test_ralph_runner_dual_mode.py -q
```

Expected:
- PASS

- [ ] **Step 6: Commit the dual-mode runner slice**

```bash
git add aworld/runners/ralph_runner.py aworld/runner.py tests/runners/test_ralph_runner_dual_mode.py
git commit -m "feat: add dual-mode execution to ralph runner"
```

---

### Task 5: Add Framework-Level Verification Via `IterationEvaluator`

**Files:**
- Create: `aworld/runners/ralph/evaluator.py`
- Modify: `aworld/runners/ralph/config.py`
- Modify: `aworld/runners/ralph_runner.py`
- Modify: `aworld/runners/ralph/state.py`
- Add: `tests/runners/test_ralph_iteration_evaluator.py`
- Modify: `tests/runners/test_ralph_runner_dual_mode.py`

- [ ] **Step 1: Add a failing test for verify-command execution and persisted failure feedback**

```python
import pytest

from aworld.runners.ralph.evaluator import IterationEvaluator


@pytest.mark.asyncio
async def test_iteration_evaluator_persists_failed_verify_output(tmp_path):
    evaluator = IterationEvaluator(...)

    result = await evaluator.evaluate(
        task_id="task-1",
        iteration=1,
        answer="Created API handlers",
    )

    assert result.verify_result.passed is False
    assert "pytest -q" in result.verify_result.commands[0]["command"]
```

- [ ] **Step 2: Add the evaluator result shape**

```python
from dataclasses import dataclass, field


@dataclass
class VerifyCommandResult:
    command: str
    exit_code: int
    output: str


@dataclass
class IterationEvaluationResult:
    verify_passed: bool
    reflection_feedback: str | None = None
    summary: str | None = None
    command_results: list[VerifyCommandResult] = field(default_factory=list)
```

- [ ] **Step 3: Execute verify commands through sandbox terminal when verify is enabled**

```python
terminal_result = await self.context.sand_box.terminal.run_code(
    code=command,
    timeout=self.verify_config.timeout,
    output_format="markdown",
)
```

- [ ] **Step 4: Persist evaluator output through `LoopMemoryStore` and feed failures into the next iteration**

```python
await self.memory_store.write_verify_result(task_id, iteration, verify_payload)
await self.memory_store.write_reflection_feedback(task_id, iteration, feedback_text)
```

- [ ] **Step 5: Call the evaluator from `RalphRunner.do_run()` after each task execution**

```python
evaluation = await self.evaluator.evaluate(
    task=self.task,
    iter_num=iter_num,
    execution_result=execution_result,
)
```

- [ ] **Step 6: Run the full Ralph runner test slice**

Run:
```bash
pytest tests/runners/test_ralph_runner_dual_mode.py tests/runners/test_ralph_loop_memory_store.py tests/runners/test_ralph_iteration_evaluator.py -q
```

Expected:
- PASS

- [ ] **Step 7: Commit the framework verification slice**

```bash
git add aworld/runners/ralph/evaluator.py aworld/runners/ralph/config.py aworld/runners/ralph/state.py aworld/runners/ralph_runner.py tests/runners/test_ralph_iteration_evaluator.py tests/runners/test_ralph_runner_dual_mode.py tests/runners/test_ralph_loop_memory_store.py
git commit -m "feat: add ralph verification pipeline"
```

---

## Self-Review Checklist

- [x] `execution_mode` is the internal source of truth; `reuse_context` remains compatibility-only.
- [x] `RalphRunner` does not take on CLI session-loop or fresh-process orchestration responsibilities.
- [x] `LoopMemoryStore` reuses `WorkSpace`, artifacts, and sandbox file/terminal capabilities rather than creating a new storage subsystem.
- [x] `fresh_context` rebuilds task context from persisted memory instead of reusing runtime state.
- [x] `aworld.runner.ralph_run(...)` still works without new required parameters.

## Final Verification

Run:
```bash
pytest tests/runners/test_ralph_runner_dual_mode.py tests/runners/test_ralph_loop_memory_store.py tests/runners/test_ralph_iteration_evaluator.py -q
```

Expected:
- PASS
