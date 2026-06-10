# AWorld CLI Evaluator Source Run

## Why

The manual trajectory evaluator regression proved that AWorld can evaluate real task outputs, trajectories, outcome checks, typed judge schemas, and composite gates. It also showed that a user-facing CLI must not expose the full substrate assembly surface just to run a simple evaluation.

The framework input-source change is responsible for normalizing task files, task+answer files, serialized states, and AWorld trajectory logs into framework-owned evaluation records and replay state. The CLI should be a thin consumer of that layer: parse user intent, select a source adapter and judge agent, run the suite-backed flow, and write a report.

The existing CLI already has an official evaluator command implemented through the builtin plugin command path, with evaluator lifecycle hooks for discovery, pre-run, post-run, and rendering. This change extends that command shape instead of adding an ad hoc script or a separate evaluator CLI.

## What Changes

- Add a source-backed `aworld-cli evaluator --input ...` mode to the existing evaluator command.
- Support source-oriented arguments: `--input`, `--kind`, optional field mappings, optional `--task-id`, `--agent`, `--judge-agent`, and output options.
- Use conventional JSONL field defaults (`id`, `input`, `answer`) so simple task+answer files do not require field-mapping flags.
- Keep the canonical command source-oriented rather than case-specific; trajectory-log, task-only, and task+answer are input kinds, not separate evaluator stacks.
- Build source-backed evaluation flows by calling the framework input-source APIs from `aworld.evaluations`.
- Preserve the existing target/suite evaluator path for current users.
- Integrate through the existing builtin plugin command and evaluator hook model; plugins may observe or customize CLI assembly metadata, but they may not redefine framework execution, scoring, gate, or report semantics.

## Capabilities

### Modified Capabilities

- `cli-evaluator-flow`: add a source-backed run path for simple file/log based evaluation while preserving plugin-backed command registration and hook extensibility.

## Impact

- Affected code: `aworld-cli/src/aworld_cli/top_level_commands/evaluator_cmd.py`, `aworld-cli/src/aworld_cli/evaluator_runtime.py`, builtin evaluator plugin command wiring, CLI rendering/tests.
- Affected APIs: additive CLI flags and runtime helpers; existing `aworld-cli evaluator --target ...` behavior remains compatible.
- Dependencies: this change depends on the framework input-source layer from `aworld-evaluator-input-sources-2026-06-10` and should land after that change.
- Non-goals: no new framework source semantics, no case-specific `trajectory-log` command as the canonical API, no new plugin system, no CLI-owned scoring or gate implementation, no training/optimizer integration.
