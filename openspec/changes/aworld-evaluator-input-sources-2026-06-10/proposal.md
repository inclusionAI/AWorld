# AWorld Evaluator Input Sources

## Why

The current manual trajectory-log regression proves that AWorld's evaluator substrate can judge final answers, inspect trajectories, run typed LLM-as-judge schemas, apply outcome checks, and gate reports. It also exposes an integration problem: callers must hand-write too much glue code to get external evaluation inputs into `EvalCaseDef` and normalized evaluator state.

This is not specific to trajectory logs. The same problem appears for several common inputs:

- a file containing task + answer pairs, where the evaluator should judge existing outputs without re-execution
- an AWorld trajectory log, where the evaluator should reconstruct `RolloutState` from prior execution
- future files containing tasks only or serialized rollout/task responses, which should implement the same source contracts when they gain real consumers

Adding a dedicated `trajectory_log.py` top-level path would solve one case but repeat the same problem for task files, answer files, rollout dumps, and future stores. The framework needs a small input-source and state-adapter layer that converts heterogeneous evaluation inputs into the existing suite/case/state substrate.

## What Changes

- Add framework-owned `EvalSource` primitives that load external evaluation inputs into `EvalCaseDef` rows plus source metadata.
- Add state adapters that convert source records with existing outputs into normalized `EvalState` or `RolloutState`.
- Let sources expose a default state adapter when the adapter is uniquely implied by the source kind.
- Add first built-in source/adapters for task+answer files and AWorld trajectory logs.
- Add a reusable replay harness that uses source adapters to provide rollout/eval state without re-executing an agent.
- Add helper factories so callers can create suite-backed evaluations from sources without hand-writing parser, replay, schema-normalization, and report plumbing.
- Keep CLI integration out of scope; this change is framework-only under `aworld/evaluations/`.

## Capabilities

### Modified Capabilities

- `evaluation-substrate`: add source-backed evaluation input normalization so suite-backed evaluation can consume task+answer and trajectory-log inputs through one framework path, with protocols that allow task-only and serialized-state sources to be added later.

## Impact

- Affected code: `aworld/evaluations/**`, especially new source/adapters, runtime-composition replay harness integration, and suite factory helpers.
- Affected APIs: additive framework APIs; existing suite-backed and runtime-composition APIs remain compatible.
- Affected tests: replace manual trajectory-log test-local glue with framework source/adapters; add focused coverage for task+answer and trajectory-log source behavior.
- Non-goals: no `aworld-cli` command shape changes, no untrusted file execution, no production storage connectors, no sandbox reset or external environment management.
