# RalphRunner Fresh-Context Verify Example Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a realistic quick-start example that demonstrates `RalphRunner` with `fresh_context` execution and terminal-backed verification on a business-oriented coding task.

**Architecture:** Add a new `examples/aworld_quick_start/ralph_runner/` example with a small deterministic setup helper that seeds a business-rules workspace, then build `run.py` on top of that helper using `Runners.ralph_run(...)`. Keep tests focused on deterministic setup and config behavior so the example stays reliable without depending on live model execution.

**Tech Stack:** Python 3.12, pytest, existing `Agent`/`Task`/`Runners.ralph_run(...)` APIs, `RalphConfig`, `RalphVerifyConfig`.

---

## File Structure

### New files

- Create: `examples/aworld_quick_start/ralph_runner/__init__.py`
- Create: `examples/aworld_quick_start/ralph_runner/example_setup.py`
- Create: `examples/aworld_quick_start/ralph_runner/run.py`
- Create: `examples/aworld_quick_start/ralph_runner/README.md`
- Create: `tests/examples/test_ralph_runner_example_setup.py`

### Existing files to modify

- Modify: `examples/aworld_quick_start/README.md`
- Modify: `examples/aworld_quick_start/README_zh.md`

Responsibilities:

- `example_setup.py`: isolated workspace paths, reset behavior, seeded business files, example config helpers
- `run.py`: runnable entrypoint that wires the seeded workspace into a real `RalphRunner` task
- `README.md`: explains the scenario, behavior, and run steps
- `test_ralph_runner_example_setup.py`: deterministic regression coverage for setup/config helpers
- quick-start indexes: add the new example to the curated list

---

### Task 1: Add Failing Tests For Ralph Example Setup And Config

**Files:**
- Create: `tests/examples/test_ralph_runner_example_setup.py`

- [ ] **Step 1: Write the failing tests for workspace creation, reset, and Ralph config**

```python
from pathlib import Path

from aworld.runners.ralph.config import RalphConfig
from examples.aworld_quick_start.ralph_runner.example_setup import (
    RalphExamplePaths,
    build_ralph_runner_example_config,
    build_ralph_runner_example_criteria,
    ensure_ralph_runner_example_workspace,
)


def test_ensure_ralph_runner_example_workspace_creates_seed_files(tmp_path: Path) -> None:
    paths = RalphExamplePaths.from_root(tmp_path / "ralph-runner-example")

    ensure_ralph_runner_example_workspace(paths)

    assert paths.root.is_dir()
    assert paths.src_dir.is_dir()
    assert paths.tests_dir.is_dir()
    assert paths.rules_file.is_file()
    assert paths.module_file.is_file()
    assert paths.test_file.is_file()


def test_build_ralph_runner_example_config_uses_fresh_context_and_verify(tmp_path: Path) -> None:
    config = build_ralph_runner_example_config(workspace=str(tmp_path))

    assert isinstance(config, RalphConfig)
    assert config.execution_mode == "fresh_context"
    assert config.reuse_context is False
    assert config.verify.enabled is True
    assert config.verify.run_before_completion is True
    assert config.verify.commands == ["PYTHONPATH=. pytest -q"]


def test_build_ralph_runner_example_criteria_sets_reasonable_iteration_budget() -> None:
    criteria = build_ralph_runner_example_criteria()

    assert criteria.max_iterations == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
pytest tests/examples/test_ralph_runner_example_setup.py -q
```

Expected:
- FAIL because `examples.aworld_quick_start.ralph_runner.example_setup` does not exist yet

- [ ] **Step 3: Commit only after the task is green**

```bash
git add tests/examples/test_ralph_runner_example_setup.py examples/aworld_quick_start/ralph_runner/example_setup.py
git commit -m "test: add ralph runner example setup coverage"
```

---

### Task 2: Implement The Example Setup Helper

**Files:**
- Create: `examples/aworld_quick_start/ralph_runner/example_setup.py`
- Modify: `tests/examples/test_ralph_runner_example_setup.py`

- [ ] **Step 1: Implement the path model and safe workspace initializer**

```python
@dataclass(frozen=True)
class RalphExamplePaths:
    root: Path
    src_dir: Path
    tests_dir: Path
    rules_file: Path
    module_file: Path
    test_file: Path
    marker_file: Path

    @classmethod
    def from_root(cls, root: Path | str) -> "RalphExamplePaths":
        root_path = Path(root).expanduser().resolve()
        return cls(
            root=root_path,
            src_dir=root_path / "src",
            tests_dir=root_path / "tests",
            rules_file=root_path / "business_rules.md",
            module_file=root_path / "src" / "order_pricing.py",
            test_file=root_path / "tests" / "test_order_pricing.py",
            marker_file=root_path / ".ralph_runner_example_root",
        )
```

- [ ] **Step 2: Seed the business-rules files**

```python
def ensure_ralph_runner_example_workspace(paths: RalphExamplePaths, reset: bool = False) -> None:
    ...
    paths.module_file.write_text(
        "def calculate_quote(items, customer_tier):\n"
        "    raise NotImplementedError('Implement me')\n",
        encoding="utf-8",
    )
```

- [ ] **Step 3: Add config and completion helper builders**

```python
def build_ralph_runner_example_config(workspace: str, model_config=None) -> RalphConfig:
    config = RalphConfig.create(model_config=model_config)
    config.workspace = workspace
    config.execution_mode = "fresh_context"
    config.verify = RalphVerifyConfig(
        enabled=True,
        commands=["PYTHONPATH=. pytest -q"],
        run_before_completion=True,
    )
    return config
```

- [ ] **Step 4: Re-run the focused tests**

Run:
```bash
pytest tests/examples/test_ralph_runner_example_setup.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit the setup helper**

```bash
git add examples/aworld_quick_start/ralph_runner/example_setup.py tests/examples/test_ralph_runner_example_setup.py
git commit -m "feat: add ralph runner example workspace setup"
```

---

### Task 3: Add The Runnable RalphRunner Example

**Files:**
- Create: `examples/aworld_quick_start/ralph_runner/__init__.py`
- Create: `examples/aworld_quick_start/ralph_runner/run.py`
- Create: `examples/aworld_quick_start/ralph_runner/README.md`

- [ ] **Step 1: Add the runnable example entrypoint**

```python
paths = RalphExamplePaths.from_root(Path(__file__).resolve().parent / ".workdir")
ensure_ralph_runner_example_workspace(paths, reset=True)

builder = Agent(...)
task = Task(
    input=(
        "Implement the order pricing module in src/order_pricing.py "
        "so the seeded business tests pass. "
        "Read business_rules.md before editing code."
    ),
    agent=builder,
    conf=build_ralph_runner_example_config(
        workspace=str(paths.root),
        model_config=agent_config.llm_config,
    ),
)
result = await Runners.ralph_run(task=task, completion_criteria=build_ralph_runner_example_criteria())
```

- [ ] **Step 2: Add the example README with run instructions and behavior**

```markdown
This example demonstrates framework-level Ralph execution with:

- `fresh_context`
- verification via `PYTHONPATH=. pytest -q`
- a seeded business-rules workspace
```

- [ ] **Step 3: Smoke-check the example syntax**

Run:
```bash
python -m py_compile examples/aworld_quick_start/ralph_runner/run.py
```

Expected:
- PASS

- [ ] **Step 4: Commit the runnable example**

```bash
git add examples/aworld_quick_start/ralph_runner/__init__.py examples/aworld_quick_start/ralph_runner/run.py examples/aworld_quick_start/ralph_runner/README.md
git commit -m "feat: add ralph runner fresh-context example"
```

---

### Task 4: Update Quick-Start Index Documentation

**Files:**
- Modify: `examples/aworld_quick_start/README.md`
- Modify: `examples/aworld_quick_start/README_zh.md`

- [ ] **Step 1: Add the new example to the quick-start indexes**

```markdown
- [RalphRunner iterative convergence](ralph_runner) \
  Example of framework-level Ralph execution with `fresh_context` and `verify`.
```

- [ ] **Step 2: Run targeted verification**

Run:
```bash
pytest tests/examples/test_ralph_runner_example_setup.py -q
python -m py_compile examples/aworld_quick_start/ralph_runner/run.py
```

Expected:
- PASS

- [ ] **Step 3: Commit the documentation index update**

```bash
git add examples/aworld_quick_start/README.md examples/aworld_quick_start/README_zh.md
git commit -m "docs: add ralph runner quick-start example"
```

---

## Self-Review Checklist

- [ ] The example uses `fresh_context`, not `reuse_context`.
- [ ] Verification is shown through a real pytest command.
- [ ] Tests cover deterministic setup/config logic only.
- [ ] The runnable example uses `Runners.ralph_run(...)`.
- [ ] The quick-start indexes point users to the new example.
