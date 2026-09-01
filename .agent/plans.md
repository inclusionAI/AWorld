# Project Plan

## Architecture Overview

The implementation adds two control planes around existing truth/data planes:

1. A Context Compiler converts typed source items into one immutable provider request plus a decision trace. It starts in
   observe/shadow mode and later becomes enforceable. Existing PromptSection, Amni neurons, memory, Skills, Tools, and
   Steering feed adapters rather than bypassing compilation.
2. A Trajectory Control Plane tracks SAR update work through a finalize barrier and emits a typed build result plus a
   checksummed JSONL v2 projection. It describes build/delivery state without duplicating provider/event truth.

Tool output policy, cache identity, lifecycle, delegation, and evaluation consume these shared contracts.

## Milestones

### Milestone 1: Trajectory Control Plane Foundation
**Goal:** Make trajectory completion, fidelity, checksums, and failure modes deterministic and observable.
**Depends on:** Existing provider request capture and benchmark evidence.

#### Tasks

##### Task 1.1: Typed trajectory build contracts
- **Parallel:** yes
- **Files:** `aworld/core/task.py`, new `aworld/dataset/trajectory_*` module, serialization tests
- **Approach:** Add `TrajectoryFidelity`, `TrajectoryBuildStatus`, `TrajectoryBuildResult`, counts, reason codes, source
  watermark, checksum, and optional artifact ref. Add compatible TaskResponse fields without changing inline trajectory.
- **Tests:** serialization, empty/partial/complete/build-failed states, checksum determinism, TaskResponse compatibility.
- **Acceptance criteria:** every build outcome is representable without synthetic assistant content.
- **Status:** complete

##### Task 1.2: Tracked update registry and finalize barrier
- **Parallel:** yes, after contract shapes are agreed
- **Files:** `aworld/runners/event_runner.py`, trajectory update call sites, runner tests
- **Approach:** replace bare trajectory update tasks with a per-task registry; await a fixed high watermark with bounded
  timeout before storage read/release; collect scheduled/completed/failed/pending counts.
- **Tests:** delayed final update, builder exception, timeout, cancellation, no updates, storage consistency.
- **Acceptance criteria:** pending count is zero for complete results; timeout/failure is typed and cannot masquerade as complete.
- **Status:** complete

##### Task 1.3: JSONL v2 sink and legacy dual-read/write
- **Parallel:** yes
- **Files:** dataset storage/log modules, event runner, trajectory reader tests
- **Approach:** add one-object-per-line JSONL records with schema/build/checksum metadata; keep configurable legacy output;
  read logger headers, Python repr, nested JSON strings, rotations, and v2 records.
- **Tests:** real formatted log files, dual write/read, duplicate revision selection, malformed records, checksum mismatch.
- **Acceptance criteria:** v2 round-trips and legacy consumers remain functional.
- **Status:** complete

##### Task 1.4: Milestone integration and architecture review
- **Parallel:** no
- **Files:** integration tests and `.agent/progress.md`
- **Approach:** bind build result to TaskResponse, validate finalize → storage → JSONL counts/checksum, then perform a
  ruthless architectural review and fix cycle.
- **Tests:** TC-TRAJECTORY-FINALIZE-021, TC-TRAJECTORY-EMPTY-022, TC-TRAJECTORY-IO-023.
- **Acceptance criteria:** milestone review approves; relevant suites pass.
- **Status:** complete

### Milestone 2: Context Models, Adapters, and Observe Mode
**Goal:** Introduce typed Context inputs and decision traces without changing provider requests.
**Depends on:** Milestone 1.

- ContextItem/Source/Scope/Authority/Trust/Stability models and token accounting. **Status: complete**
- Generic occurrence adapters, PromptSection owner adapter, and cleaned-history owner adapter. **Status: complete**
- Skill descriptor/content owner adapters. **Status: complete**
- CLI steering owner adapter. **Status: complete**
- Final Tool Catalog owner adapter. **Status: complete**
- Amni neuron pre-fold output owner adapter. **Status: complete**
- Deterministic occurrence ordering and redacted decision trace. **Status: complete**
- Pure observe compiler freezes and compares finalized legacy requests. **Status: complete**
- Model-boundary observe integration and request-id correlation without request mutation. **Status: complete**
- Acceptance: current requests remain unchanged and request-trace match is measurable for all supported paths.

### Milestone 3: Universal Final Compiler and Rollout Modes
**Goal:** Enforce one immutable final compilation boundary with budgets and cache identity.
**Depends on:** Milestone 2.

**Status:** complete for the reviewed OpenAI enforce slice

#### Task 3.1: Universal final compile contract and deterministic reducers
- **Status:** complete
- Add a provider-neutral `FinalCompileInput/Policy/Result` that consumes finalized owner occurrences, exact messages/Tools,
  versioned token estimates, inference profile and lifecycle epoch.
- Apply scope/trust resolution, atomic budget planning and explicit reducers/offload refs without runtime capabilities.
- Produce one immutable selected request, decision trace, cache identity evidence and typed non-enforceable reasons.

#### Task 3.2: Runtime final-boundary integration
- **Status:** complete
- Move normal Agent prompt transforms, Steering and Tool catalog observation before the final compile call.
- Publish memory/Amni/CLI owner sidecars into the same task-epoch input and reject stale sidecars.
- Keep `off` byte-compatible, `observe/shadow` side-effect-free and `enforce` fail-closed.

#### Task 3.3: Provider lowering and canonical serialization
- **Status:** complete for the declared OpenAI SDK/HTTP slice; unsupported providers remain capability-blocked by design
- Add a registry of reviewed provider adapters, canonical serialized evidence for controlled HTTP paths, cache-prefix receipts,
  and exact candidate/provider request correlation. Azure/other providers remain blocked until implemented.

#### Task 3.4: Entry-point parity
- **Status:** complete through the shared LLM boundary; entry-point-specific metadata remains outside compiler semantics
- Route normal Agent, Amni, CLI and ACP through the same final compiler contract without modifying external repositories.
- Preserve entry-point-only metadata while making request/budget/trace semantics equivalent.

### Milestone 4: Scoped Instructions, Progressive Disclosure, and Lifecycle
**Goal:** Resolve nested instructions/Skills/Tools deterministically and prevent cross-task leakage.
**Depends on:** Milestone 3.

#### Task 4.1: Hierarchical scope and trust resolver
- **Status:** complete
- Nested/path scope matching, authority conflict explanation, untrusted external-content isolation and deterministic ordering.

#### Task 4.2: Progressive Skills and Tool catalog
- **Status:** complete
- Index -> descriptor -> content Skill activation, task-epoch-sticky minimal Tool catalog and explicit cache-impact decisions.

#### Task 4.3: Lifecycle state machine
- **Status:** complete
- Versioned session/task/turn epochs and reset/checkpoint/rewind/resume transitions with stale-sidecar/cache invalidation.

#### Task 4.4: Context inspector
- **Status:** complete
- One read-only redacted projection for CLI/ACP/debug consumers; no duplicate compilation/statistics implementation.

### Milestone 5: Structured Delegation and Completion Contracts
**Goal:** Make subagent Context propagation and task completion explicit and bounded.
**Depends on:** Milestone 3.

#### Task 5.1: Delegation and Context Pack contracts
- **Status:** complete
- Frozen `DelegationSpec`, least-authority Context Pack, inference/budget/deadline/cancel propagation and bounded child result.

#### Task 5.2: Child lifecycle and merge
- **Status:** complete
- Recursion/deadline/cancellation state machine, deterministic merge policy and main/child cost/context attribution.

#### Task 5.3: Completion Contract
- **Status:** complete
- Observe/enforce deterministic artifact/test requirements while keeping Agent completion, self-check and external verifier
  as separate evidence sources.

### Milestone 6: Evaluation, Canary, and Default-On Readiness
**Goal:** Prove generalized framework benefit and safely enable enforcement.
**Depends on:** Milestones 1-5.

#### Task 6.1: Integrated evaluation contract
- **Status:** complete
- Freeze Context-only variant manifests, request/trajectory/artifact provenance, paired repeats, hard gates and confidence
  interval inputs without embedding benchmark answers or verifier policy.

#### Task 6.2: Canary and rollback control plane
- **Status:** complete
- Session-sticky rollout cohorts, provider/entry-point capability gates, rollback bundle and default-on readiness report.

#### Task 6.3: Deferred integrated validation
- **Status:** in progress
- Only after Tasks 3.1-6.2 are code-complete: run unit/integration suites, local Docker Terminal Bench subset and at least
  one non-Terminal Tool/research/delegation workload; repair defects, then evaluate generalized Context benefit.
- Local Docker execution/capture smoke is complete for one real Terminal Bench task; paired reward aggregation and the
  independent second workload remain pending before any benefit/default-on decision.
