## 1. Contracts And Configuration

- [x] 1.1 Define candidate replay request/result data contracts.
- [x] 1.2 Define skill overlay metadata and artifact contracts.
- [x] 1.3 Add config knobs for replay timeout, max replay steps, max replay
  tokens/cost, and replay enablement.
- [x] 1.4 Ensure online `auto_verified` requires replay success for skill
  targets when replay is enabled.
- [x] 1.5 Document that replay prerequisites are environment requirements, not
  mutation targets.
- [x] 1.6 Add replay repetition, replay candidate limit, and stability-margin
  config fields.
- [x] 1.7 Extend replay cost preflight to include baseline reruns, candidate
  reruns, candidate count limits, and judge repetitions.

## 2. Candidate Skill Overlay

- [x] 2.1 Implement overlay directory creation under `.aworld/self_evolve/`.
- [x] 2.2 Write candidate `SKILL.md` into the overlay without touching the real
  skill.
- [x] 2.3 Add provenance linking overlay content to candidate id and target
  fingerprint.
- [x] 2.4 Implement a materialized shadow skill root containing the candidate
  skill plus every other baseline-visible skill except the original selected
  target skill.
- [x] 2.5 Pass the shadow skill root as the replay `skills_path` for legacy
  loaders and as the first framework-controlled skill source for runtime
  bootstrap paths.
- [x] 2.6 Ensure replay creates a fresh skill registry and does not reuse
  baseline registry or content-cache objects.
- [ ] 2.7 Add cleanup or retention policy for overlay artifacts.
- [x] 2.8 Test proposal and shadow modes do not mutate installed skills.
- [x] 2.9 Test unrelated skills remain available while only the selected target
  skill is shadowed.

## 3. Task Replay Harness

- [x] 3.1 Extract replayable task input from current trajectory and explicit
  invocation context.
- [x] 3.2 Resolve the agent/runtime used for replay without hardcoding a
  specific agent or skill.
- [x] 3.3 Execute the task with the candidate skill overlay.
- [x] 3.4 Capture candidate trajectory, stdout/stderr, metrics, and failure
  diagnostics.
- [x] 3.5 Enforce replay timeout, token, cost, and step budgets.
- [x] 3.6 Return structured replay failure when prerequisites are missing.
- [x] 3.7 Support baseline rerun for verified apply when the baseline skill and
  replay prerequisites are available.
- [ ] 3.8 Support candidate replay repetitions and aggregate metric reporting.
- [x] 3.9 Add fake-runtime tests for successful replay and replay failure.
- [ ] 3.10 Add tests showing fixed historical baseline plus one candidate rerun
  remains limited confidence unless policy explicitly allows it.

## 4. Paired Trajectory Evaluation

- [x] 4.1 Build paired baseline/candidate evaluation cases after replay.
- [x] 4.2 Populate `variant_trajectories` automatically in dataset metadata.
- [x] 4.3 Reuse `AWorldTrajectoryEvaluatorBackend` for evaluator-agent scoring.
- [x] 4.4 Persist evaluator reports and link them from self-evolve `report.json`.
- [x] 4.5 Test candidate trajectory metrics are used instead of text-only
  overlay heuristics.
- [ ] 4.6 Test aggregate baseline/candidate metrics and stability-margin
  rejection under noisy replay results.

## 5. Runner And Gates

- [x] 5.1 Integrate replay before candidate selection for verified apply.
- [x] 5.2 Gate `auto_verified` on replay success.
- [x] 5.3 Gate `auto_verified` on evaluator score delta and trajectory gate
  pass.
- [x] 5.4 Preserve deterministic, held-out, protected-path, budget, provenance,
  malformed, noop, token, cost, and latency gates.
- [x] 5.5 Persist an original-skill backup and apply journal before writing the
  real skill.
- [x] 5.6 Add post-apply verification that loads the applied candidate through a
  fresh production runtime skill registry.
- [ ] 5.7 Add cache invalidation or registry refresh for long-lived runtimes.
- [ ] 5.8 Add rollback or failed-state reporting for post-apply verification
  failure and interrupted worker apply.
- [x] 5.9 Test that file equality alone does not satisfy post-apply
  verification.
- [x] 5.10 Test that evaluator-agent-only improvement cannot produce verified
  apply.

## 6. Scheduler And Async Worker

- [x] 6.1 Make background jobs run the full candidate replay loop.
- [x] 6.2 Ensure original task response remains best-effort and non-blocking.
- [ ] 6.3 Persist running/succeeded/failed job state with replay diagnostics.
- [ ] 6.4 Add tests for worker drain success, replay failure, evaluator failure,
  and gate rejection.
- [x] 6.5 Add a real drain entrypoint for applications that enable post-run
  self-evolve.
- [ ] 6.6 Define worker event-loop strategy so full agent replay does not call
  `asyncio.run()` inside an already-running loop.
- [ ] 6.7 Define worker resource, timeout, cancellation, and concurrency
  behavior.
- [ ] 6.8 Test worker recovery from an interrupted apply using the persisted
  backup and apply journal.

## 7. CLI And Validation Script

- [x] 7.1 Keep `aworld-cli optimize` as a thin wrapper over framework replay.
- [x] 7.2 Add CLI output fields for replay artifact paths and evaluator report
  paths.
- [ ] 7.3 Extend `scripts/self_evolve_cli_trajectory_case.py` to assert a real
  candidate trajectory was generated when strict verified mode is requested.
- [x] 7.4 Add tests proving CLI does not own overlay, replay, evaluator, or
  apply logic.

## 8. Documentation And Skill Guidance

- [x] 8.1 Update built-in `self_evolve` skill guidance after implementation is
  tested.
- [x] 8.2 Document available, conditional, and unsupported replay paths.
- [x] 8.3 Document environment prerequisites and replay diagnostics.
- [x] 8.4 Add examples for proposal-only, verified replay, and failed replay
  diagnostics.
- [x] 8.5 Document post-apply runtime loader verification and cache refresh
  semantics.
- [x] 8.6 Document replay variance controls and candidate replay limits.

## 9. Verification

- [x] 9.1 Run self-evolve unit and integration tests.
- [x] 9.2 Run evaluator runtime tests.
- [x] 9.3 Run CLI optimize tests.
- [x] 9.4 Run OpenSpec validation for this change.
- [x] 9.5 Record remaining unsupported target types or replay environments.
