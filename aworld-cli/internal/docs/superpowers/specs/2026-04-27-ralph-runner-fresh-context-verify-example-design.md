# RalphRunner Fresh-Context Verify Example Design

## Context

`RalphRunner` now supports explicit dual-mode execution and framework-level verification, but the repository does not yet include a realistic quick-start example that shows how to use:

- `execution_mode="fresh_context"`
- terminal-backed `verify`
- a real business task with persisted iteration memory

The existing `examples/aworld_quick_start/mcp_tool/ralph_demo_run.py` is useful as a compatibility smoke example, but it does not demonstrate the new framework behavior clearly enough. It also does not show a verify-driven repair loop.

## Goal

Add a runnable quick-start example that demonstrates `RalphRunner` in `fresh_context` mode with `verify` enabled, using a realistic business-oriented coding task.

## Non-Goals

- Do not add new `RalphRunner` framework features.
- Do not introduce CLI plugin behavior into the example.
- Do not make tests depend on live model calls.
- Do not add external service dependencies beyond the existing repository test/runtime environment.

## Recommended Example Shape

The example should model a small but realistic internal business coding task:

- the agent receives a task to implement an order-pricing module
- the workspace is pre-seeded with business-rule tests
- `RalphRunner` runs in `fresh_context`
- verification runs `PYTHONPATH=. pytest -q`
- failed verification feeds the next repair iteration

This gives a concrete example of framework-level Ralph convergence without requiring web servers, databases, or third-party app dependencies.

## Why This Case

This case balances realism and operability:

- it is business-oriented rather than toy arithmetic
- it has clear correctness criteria expressed as tests
- it uses only Python and pytest, which already fit the repository environment
- it demonstrates why `fresh_context` matters: each repair round must rely on persisted memory and verification feedback, not accumulated in-memory conversation state

## User-Facing Structure

Add a new quick-start example directory:

- `examples/aworld_quick_start/ralph_runner/`

Recommended files:

- `run.py`
  Entry point for the example.
- `example_setup.py`
  Creates and resets the isolated work directory and seeds the business tests.
- `README.md`
  Explains what the example demonstrates and how to run it.
- `__init__.py`
  Keeps the example importable and consistent with the rest of `aworld_quick_start`.

## Example Behavior

The example should:

1. Create or reset an isolated work directory
2. Seed files such as:
   - `src/order_pricing.py`
   - `tests/test_order_pricing.py`
   - a short business-rules markdown file
3. Build a standard `Agent`
4. Create `RalphConfig` with:
   - `execution_mode="fresh_context"`
   - `verify.enabled=True`
   - `verify.commands=["PYTHONPATH=. pytest -q"]`
   - `verify.run_before_completion=True`
5. Call `Runners.ralph_run(...)`
6. Print the final answer and the example workspace path

## Testing Strategy

Do not test the live LLM run.

Instead, test deterministic example support code:

- isolated workspace creation
- reset safety
- seeded file contents
- `RalphConfig` defaults for this example
- completion criteria for the example

This keeps the example operational while still giving the repository a reliable regression harness.

## Documentation Changes

Update:

- `examples/aworld_quick_start/README.md`
- `examples/aworld_quick_start/README_zh.md`

Add:

- `examples/aworld_quick_start/ralph_runner/README.md`

The quick-start index should describe this as the `RalphRunner` example for framework-level iterative convergence with `fresh_context + verify`.

## Success Criteria

- A new quick-start example exists under `examples/aworld_quick_start/ralph_runner/`
- The example uses `RalphRunner` through `Runners.ralph_run(...)`
- The example config explicitly uses `fresh_context`
- Verification is enabled with a pytest command
- Repository tests cover deterministic setup/config logic
- Example index docs mention the new example
