# Project Progress

## Current Status

**Phase:** Milestone 1
**Current milestone:** Trajectory Control Plane Foundation
**Current task:** Task 1.4 integration and architecture review
**Last action:** Merged finalize barrier and connected JSONL v2 only after finalized snapshot; combined trajectory suite is 70/70.

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
| 1.2 Tracked update/finalize barrier | complete | Root registry, HWM drain, revision fence, typed finalize and cancellation |
| 1.3 JSONL v2 dual-read/write | complete | Finalize-time dual writer, codec, real legacy reader, checksum/revision selection |
| 1.4 Integration and architecture review | in_progress | Cross-task suite passed; independent review running |

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

### Decision: Context compiler dependency and truth boundary
- Options considered: infer provenance from final role/content; let core import Amni/CLI owners; use owner-side adapters into a
  dependency-light core model.
- Chose: stdlib-only models/freeze/trace under `aworld.core.context.compiler`; Amni, memory, Skill, Tool, Steering, CLI, and
  provider owners adapt downward and retain UNKNOWN when provenance cannot be proved.
- Rationale: final folded system messages lose neuron ordering/source semantics, and upward imports would introduce cycles.
- Trade-offs accepted: some legacy sources remain unknown until owner boundaries add provenance sidecars.

### Decision: Observe-mode fidelity
- Options considered: call providers from a reconstructed canonical request; inspect only the model-boundary request; freeze
  a copy of the already-prepared provider request while sending the untouched original.
- Chose: prepare once, snapshot a deep copy immediately before SDK/HTTP send, and send the original object unchanged.
- Rationale: observe must not change types, ordering, defaults, cache hints, hooks, or external call count.
- Trade-offs accepted: SDK providers report `provider_prepared` structural fidelity; only controlled HTTP paths may claim
  serialized-byte fidelity.

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

### Milestone 2 Readiness
- Task 2.1 dependency-light frozen models/trace is in progress at `/tmp/aworld-context-compiler-models`; it has no runtime
  integration and therefore cannot bypass the unfinished Milestone 1 finalize/writer gate.
- Existing PromptAssemblyProvider sees Amni neuron sections only after they were folded into one system message; provenance
  sidecars must be emitted before folding rather than inferred later.
- Memory adapters must follow the exact cleaned replay list, including Tool pair repair and duplicate occurrences.
- Provider-bound capture currently happens after success and correlates by the latest unmatched call; observe mode needs a
  request id before send so failures and concurrent calls bind deterministically.
- Authority, trust, lifetime, stability, source URI, task epoch, and exact token counts remain UNKNOWN unless their owner can
  prove them; role/content heuristics are not acceptable.
