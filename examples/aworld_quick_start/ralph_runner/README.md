# RalphRunner Fresh Context Example

This quick-start shows framework-level `RalphRunner` execution with:

- `execution_mode="fresh_context"`
- terminal-backed verification via `PYTHONPATH=. pytest -q`
- a seeded business workspace that persists files across iterations
- a verify-driven repair loop that stops when the latest verification passes

## Scenario

The example seeds a small internal order-pricing task:

- `business_rules.md` defines the pricing rules
- `src/order_pricing.py` starts as an unfinished module
- `tests/test_order_pricing.py` defines the expected behavior

Each Ralph iteration starts from fresh task context, but the workspace and loop memory persist. Verification runs after every iteration, and failed test output becomes repair feedback for the next round.

## Run

From the project root:

```bash
python examples/aworld_quick_start/ralph_runner/run.py
```

The script resets `examples/aworld_quick_start/ralph_runner/.workdir`, runs `Runners.ralph_run(...)`, then prints the final answer and the example workspace path.

## Files

- `run.py`: runnable entrypoint
- `example_setup.py`: deterministic workspace seeding and config helpers
- `.workdir/`: generated workspace used by the demo at runtime
