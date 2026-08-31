# Project Progress

## Current Status

**Phase:** Milestone 1
**Current milestone:** Trajectory Control Plane Foundation
**Current task:** Tasks 1.2 and 1.3 implementation in isolated worktrees
**Last action:** Merged and re-verified Task 1.1; dispatched finalize barrier and JSONL v2 codec/reader in parallel.

## Completed Foundations

- Built-in Agent portability, optional native capability degradation, and direct-run typed failure/non-zero semantics.
- Provider-bound LLM call capture and compiler/provider snapshot merge.
- DockerSandbox attach implementation, bounded/reversible Tool output artifacts, real Docker capability gate.
- Generic paired benchmark driver, evidence provenance, and Context-benefit evaluation contract.

## Current Milestone: Trajectory Control Plane Foundation

### Task Status

| Task | Status | Notes |
|---|---|---|
| 1.1 Typed trajectory build contracts | complete | Immutable control result, canonical checksum, invariants, TaskResponse projections |
| 1.2 Tracked update/finalize barrier | in_progress | `/tmp/aworld-context-trajectory-finalize` |
| 1.3 JSONL v2 dual-read/write | in_progress | `/tmp/aworld-context-trajectory-io`; writer integration deferred until 1.2 |
| 1.4 Integration and architecture review | pending | Depends on 1.1-1.3 |

## Decisions Log

### Decision: First implementation milestone
- Options considered: Context Compiler models first; trajectory finalization first; scoped instructions first.
- Chose: trajectory control plane first.
- Rationale: provider capture and benchmark driver already exist, while missing finalize/fidelity makes every later Context
  experiment unable to distinguish execution failure from persistence failure.
- Trade-offs accepted: Context compilation remains observe-only/implicit until Milestone 2.

### Decision: Benchmark role
- Options considered: optimize Terminal Bench directly; use it as one validation adapter.
- Chose: interchangeable validation adapter.
- Rationale: the goal is generalized AWorld framework capability from Context Management.
- Trade-offs accepted: improvements require broader evidence and may take longer to validate.

### Decision: Trajectory build contract ownership
- Options considered: store control metadata inside `TrajectoryItem`; import a dataset contract from `core.task`; define a
  dependency-light core contract.
- Chose: `aworld.core.trajectory` owns immutable build metadata and canonical trajectory checksum; `TaskResponse` binds one
  canonical result and exposes read-only compatibility projections.
- Rationale: SAR remains the existing semantic projection, and importing `aworld.dataset` from `core.task` would initialize
  dataset modules that already depend on core, risking a circular import.
- Trade-offs accepted: JSONL envelopes and finalize integration remain separate Tasks 1.2-1.3.

### Decision: Trajectory registry ownership
- Options considered: runner-local task set; Context-owned registry; root TrajectoryDataset-owned registry.
- Chose: one registry reachable from the root Context/TrajectoryDataset boundary, partitioned by task id.
- Rationale: Post-LLM hooks, subtask group merges, deep-copied Context, and Amni root delegation bypass a runner-only set.
- Trade-offs accepted: the dataset/control boundary gains lifecycle state and must explicitly fence storage writes.

### Decision: JSONL v2 physical format
- Options considered: reuse Loguru trajectory.log; replace legacy output; use an independent v2 JSONL sibling.
- Chose: independent `trajectory.jsonl` sink with legacy/dual/jsonl_v2 modes.
- Rationale: a real Loguru record has a header plus Python repr and nested JSON strings, so it cannot satisfy one-object-per-
  line integrity. Dual mode preserves existing consumers.
- Trade-offs accepted: dual mode duplicates redacted trajectory bytes during migration and requires explicit retention.

## Architecture State

### Components
- `llm_calls`: provider request truth with provider-bound snapshots.
- Runtime events/TrajectoryDataset: action/result truth and mutable SAR projection.
- Docker Tool output policy: bounded inline view plus checksummed retrievable artifact.
- Benchmark driver: frozen invariant manifests, independent verifier, paired Context metrics.

### Known Issues
- Typed TrajectoryBuildResult is bound to TaskResponse but not yet produced by runner finalization or sinks.
- Trajectory updates may race task finalization.
- `trajectory.log` remains legacy logger/Python-repr encoding.
- Current compiler/request capture can observe hook drift but does not yet enforce a universal final boundary.

### Audit Findings
- Three event-runner trajectory updates are bare `create_task` calls and can race the final storage read.
- The same logical message has before/after builds without revision fencing; completion order can overwrite newer state.
- Update/storage exceptions are swallowed, so coroutine completion does not prove persistence acknowledgement.
- Existing evaluation reader fails on real Loguru headers when iterating all records and selects the first repeated task.
- Runtime/ATIF projection receipts live outside this repository and must not be claimed complete in Milestone 1.
