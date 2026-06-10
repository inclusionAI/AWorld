## 1. Environment Fixture Primitives

- [x] 1.1 Add `EnvironmentSnapshot` with serializable `environment_id`, `trial_id`, and metadata.
- [x] 1.2 Add an `EnvironmentFixture` protocol with async-compatible `reset` and `cleanup`.
- [x] 1.3 Add validation/serialization helpers that exclude live handles from snapshot metadata.

## 2. Runtime Harness Wrapper

- [x] 2.1 Add `EnvironmentIsolatedRuntimeHarness`.
- [x] 2.2 Reset before exactly one base rollout.
- [x] 2.3 Inject snapshot metadata into case input, case metadata, and target.
- [x] 2.4 Add cleanup after rollout and preserve cleanup metadata in rollout state.

## 3. Trial And Retry Semantics

- [x] 3.1 Prove multi-trial suites reset once per trial.
- [x] 3.2 Prove retry attempts do not increase reset count when retry is inside environment isolation.
- [x] 3.3 Document wrapper-order semantics for one-environment-per-trial versus one-environment-per-attempt.

## 4. Failure Semantics

- [x] 4.1 Attempt cleanup when the base harness raises.
- [x] 4.2 Preserve the original rollout exception when rollout and cleanup both fail.
- [x] 4.3 Record cleanup failure metadata on terminal rollout state when cleanup fails after rollout success.

## 5. Report Shape

- [x] 5.1 Ensure environment metadata appears through existing state metadata/artifacts.
- [x] 5.2 Keep report schema additive and compatible.

## 6. Verification

- [x] 6.1 Add focused tests for reset, cleanup, trial count, retry composition, failure cleanup, and report metadata.
- [x] 6.2 Run evaluator regression tests.
- [x] 6.3 Validate this OpenSpec change with `openspec validate aworld-evaluator-environment-isolation-2026-06-10 --strict`.
