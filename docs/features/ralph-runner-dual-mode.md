# RalphRunner Dual-Mode

**Version:** 1.0  
**Status:** Production Ready  
**Feature Type:** Framework Runner

---

## Overview

`RalphRunner` is the framework-level Ralph execution capability in AWorld.

It is designed for agents or swarms that need iterative convergence inside a single AWorld runtime. It is separate from the CLI `ralph-session-loop` plugin:

- `RalphRunner` handles inner task convergence
- the CLI plugin handles outer interactive session continuation

The two layers are intentionally independent.

---

## When To Use It

Use `RalphRunner` when you want:

- programmatic Ralph execution from Python
- a bounded inner loop with explicit completion criteria
- iteration memory persisted through AWorld workspace and sandbox primitives
- framework-managed verification before accepting completion

Do not use it for:

- fresh process spawning
- CLI session continuation
- operator-driven `exit` / resume control

Those belong to the CLI plugin or future external orchestration.

---

## Public Entry Points

You can use either:

```python
from aworld.runner import Runners
```

or:

```python
from aworld.runners.ralph_runner import RalphRunner
```

The compatibility goal is unchanged:

- `Runners.ralph_run(task, completion_criteria)`
- `RalphRunner(task=task, completion_criteria=...)`

---

## Quick Start

```python
from aworld.agents.llm_agent import Agent
from aworld.core.task import Task
from aworld.runner import Runners
from aworld.runners.ralph.config import RalphConfig
from aworld.runners.ralph.types import CompletionCriteria

agent = Agent(name="builder", conf=...)

ralph_config = RalphConfig.create(model_config=agent.conf.llm_config)
ralph_config.execution_mode = "reuse_context"

task = Task(
    input="Build a REST API with tests",
    agent=agent,
    conf=ralph_config,
)

result = await Runners.ralph_run(
    task=task,
    completion_criteria=CompletionCriteria(max_iterations=5),
)
```

---

## Execution Modes

### `reuse_context`

Use this when each iteration should continue in the same task/runtime context.

Characteristics:

- preserves running context across iterations
- best when the agent benefits from accumulated context
- remains the default for backward compatibility

### `fresh_context`

Use this when each iteration should rebuild from persisted loop memory instead of reusing runtime context.

Characteristics:

- each iteration starts from a fresh sub-context
- loop continuity comes from persisted memory, not prior in-memory context
- useful when you want tighter iteration isolation

Example:

```python
ralph_config.execution_mode = "fresh_context"
```

`reuse_context` is still supported as a compatibility field, but `execution_mode` is now the preferred internal and external knob.

---

## Verification

`RalphRunner` can execute real verification commands through the sandbox terminal.

```python
from aworld.runners.ralph.config import RalphVerifyConfig

ralph_config.verify = RalphVerifyConfig(
    enabled=True,
    commands=[
        "pytest tests/api -q",
        "ruff check .",
    ],
    run_on_each_iteration=False,
    run_before_completion=True,
)
```

Behavior:

- verification can run after each iteration and/or before final completion
- failed pre-completion verification keeps the Ralph loop alive for another repair round
- verify outputs and feedback are persisted into loop memory

---

## Iteration Limits And Boundary With The CLI Plugin

Framework and CLI iteration controls are separate:

- `CompletionCriteria(max_iterations=...)` controls the inner `RalphRunner` loop
- `/ralph-loop --max-iterations ...` controls the outer CLI session continuation loop

Neither overrides the other automatically.

This is the intended boundary:

- outer plugin decides whether the current CLI session should continue
- inner runner decides whether the current task execution has converged

---

## Memory Model

`RalphRunner` does not introduce a new storage system. It reuses existing AWorld primitives:

- workspace artifacts for structured iteration memory
- sandbox file access for file-shaped intermediate state
- sandbox terminal for verification execution

This gives `fresh_context` mode a durable memory contract without coupling the runner to CLI-only orchestration.

---

## Compatibility Notes

- Existing callers using `Runners.ralph_run(...)` still work.
- Existing callers using `reuse_context` still work.
- Prefer `execution_mode` for new code.
- `RalphRunner` remains a framework capability, not a CLI-only mechanism.
