# AWorld Evaluator Environment Isolation

## Why

Runtime composition can now run one rollout and trial policy can repeat cases for pass@k/pass^k. Those trials are not truly independent if they share filesystem, database, service, or in-memory state. Evaluator users need a framework-owned reset lifecycle so every trial can start from a declared clean environment and record enough metadata to audit the reset.

## What Changes

- Add a trusted environment fixture contract for setup/reset/cleanup around each runtime-composed rollout.
- Add a wrapper harness that runs environment reset before the base harness and cleanup after the terminal rollout.
- Inject serializable environment metadata into the case/target visible to the base harness.
- Preserve environment metadata in rollout state, evaluator state, and report artifacts/metadata.
- Keep real sandbox/container/database adapters out of scope; this change defines the lifecycle and trusted in-process contract.

## Impact

- Affected code: `aworld/evaluations/runtime_composition.py`, runtime-composed substrate paths, report metadata through existing state serialization.
- Affected tests: add focused coverage for reset-per-trial, retry separation, cleanup-on-failure, and report metadata.
- Follow-ups: concrete filesystem/database/container environment fixtures and LLM-backed user simulators remain separate changes.
