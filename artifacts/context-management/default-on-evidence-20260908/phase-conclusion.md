# Adaptive Context default-on evidence checkpoint (2026-09-08)

> Superseded on 2026-09-09 for the verified `openai:amni:async` capability. The
> runtime-audited 12-pair, three-workload release report is
> `combined-adaptive-default-on-v13-release.json`; its machine decision is
> `status=ready` with no gate failures. The remainder of this document preserves
> the earlier 2026-09-08 checkpoint and its then-valid blockers for audit history.

## Decision

`adaptive` is a materially improved release candidate, but the production default-on gate is not yet satisfied. The evidence below proves state preservation, recovery behavior, trajectory fidelity, cross-workload reward non-regression, and a large reduction in model calls/request volume on two frozen workloads. It does not yet prove a statistically significant Reward gain, and it does not replace production canary or rollback evidence.

No benchmark instruction, task environment, verifier assertion, or task-specific Tool was changed. The candidate differs from `ablation-40-progressive-skills` only through the pre-frozen generic Context bundle: adaptive checkpointing, Amni WorkingState/continuation preservation, typed progress, destructive-sandbox checkpoint/rollback, and the elastic progress gate.

## Complete paired evidence

| Workload | Complete pairs | Baseline reward | Candidate reward | Model-call direction | Request-volume direction |
|---|---:|---:|---:|---|---|
| Terminal Bench `db-wal-recovery` | 3 | 3/3 | 3/3 | mean 21.67 -> 11.67 (-46.2%) | mean 1,211,888 -> 651,190 bytes (-46.3%) |
| SkillsBench PDF/Excel | 3 | 3/3 | 3/3 | mean 22.67 -> 15.00 (-33.8%) | median 1,831,037 -> 543,951 bytes (-70.3%) |
| Combined | 6 | 6/6 | 6/6 | lower in both workloads | lower in both workloads |

Terminal Bench mean wall time also fell from 377.51 s to 317.12 s (-16.0%). SkillsBench wall time is not comparable as a framework latency claim because several candidate requests waited through documented GLM transport instability.

All twelve complete runs have finalized Raw trajectory output, checksum-valid capture, and a provider request/trace match rate of 1.0. The combined machine-readable report is `combined-terminal-skillsbench-benefit.json`; workload reports are `terminal-db-wal-benefit.json` and `skillsbench-pdf-excel-benefit.json`.

## Mechanism attribution

The `db-wal-recovery` candidate used six generic rollback recoveries across the three seeds. In the earlier same-seed frozen pair, baseline failed and candidate passed after AWorld detected implicit tracked-artifact loss, restored the sandbox snapshot, and retained the recovered-record count and next action in Amni WorkingState through compaction. The new clean three-seed run reproduced candidate success in every seed; it also showed that the baseline can pass because GLM output is stochastic, so the earlier 0 -> 1 observation remains mechanism evidence rather than a standalone statistical claim.

This closes the previously observed failure mode where adaptive compaction prolonged execution but removed the state needed to continue. The invariant is now: a compacted request retains a bounded verified work ledger, a task checkpoint, and the most recent complete assistant/Tool atomic group; if that cannot be proven, compaction fails safe.

## Invalid infrastructure attempts

The interrupted `regex-log` attempts are excluded from paired benefit evidence. One run exhausted six provider attempts without a model response or any Tool action; a second was manually interrupted during the same outage. Their journals remain useful capture diagnostics, but they are not Reward observations.

The harness now derives provider exhaustion from append-only call status evidence, records `reward=null`, skips the verifier as a quality observation, and opens a batch circuit-breaker so remaining frozen jobs are not consumed during an outage. This is workload-independent and does not inspect task text.

The clean infrastructure probe is stored in `terminal-regex-log-adaptive-default-on-gate-v1-3seed-circuit-breaker-20260908`. Its first scheduled rollout recorded six failed provider attempts, zero successful responses and zero Tool actions. It retained a finalized Raw trajectory and opened the circuit with five jobs unstarted. The provider diagnostic reported tenant RPM limiting, but classification was derived from the append-only call states rather than that message.

## Remaining release gates

- At least 10 complete pairs are required; the current cross-workload matrix has 6.
- The paired Reward delta is 0.0. A significant quality gain or a complete versioned cost-per-success interval is still required by the benefit gate.
- Some GLM calls omit or conflict on cache-usage details, so normalized-cost readiness correctly remains fail-closed even though request bytes and prompt tokens are lower.
- A production capability/canary receipt and an externally executable rollback bundle are still operationally required before production default-on.
- `regex-log` and `mteb-retrieve` must be resumed from a clean batch only after a real-request provider preflight is stable.

Accordingly, the code candidate is suitable for continued shadow/canary validation, but the evidence does not authorize claiming statistically significant Reward improvement or enabling production default-on today.

Validation after the circuit-breaker change: 664 passed and 4 skipped in the selected Context, Memory, runner, sandbox and evaluation suite. Eleven AWORLD instruction-neuron tests also pass under an isolated home-instruction fixture, preventing a developer's real `~/.aworld/AWORLD.md` from contaminating expected workspace scope.
