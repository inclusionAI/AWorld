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
- **Status:** in_progress

### Milestone 2: Context Models, Adapters, and Observe Mode
**Goal:** Introduce typed Context inputs and decision traces without changing provider requests.
**Depends on:** Milestone 1.

- ContextItem/Source/Scope/Authority/Trust/Stability models and token accounting. **Status: complete**
- Generic occurrence adapters, PromptSection owner adapter, and cleaned-history owner adapter. **Status: complete**
- Remaining adapters for neurons, Skills, Tool catalog owners, and Steering. **Status: pending**
- Deterministic occurrence ordering and redacted decision trace. **Status: complete**
- Pure observe compiler freezes and compares finalized legacy requests. **Status: complete**
- Model-boundary observe integration and request-id correlation without request mutation. **Status: pending**
- Acceptance: current requests remain unchanged and request-trace match is measurable for all supported paths.

### Milestone 3: Universal Final Compiler and Rollout Modes
**Goal:** Enforce one immutable final compilation boundary with budgets and cache identity.
**Depends on:** Milestone 2.

- Budget allocation, required-item handling, Tool pair invariants, reducers/offload decisions.
- Canonical serialization, logical/serialized prefix hashes, CacheIdentity and CacheBreakReason.
- `off`, `observe`, `shadow`, and `enforce`; shadow performs no additional external actions.
- Integrate normal Agent/model boundary first, then Amni/CLI/ACP parity.

### Milestone 4: Scoped Instructions, Progressive Disclosure, and Lifecycle
**Goal:** Resolve nested instructions/Skills/Tools deterministically and prevent cross-task leakage.
**Depends on:** Milestone 3.

- Hierarchical scope/path matching and conflict explanation.
- Skill descriptor/content split and task-sticky minimal Tool catalog.
- Task/session/turn epoch transitions, reset/checkpoint/rewind/resume semantics.
- Prompt-injection/trust tests and Context inspector read model.

### Milestone 5: Structured Delegation and Completion Contracts
**Goal:** Make subagent Context propagation and task completion explicit and bounded.
**Depends on:** Milestone 3.

- DelegationSpec, Context Pack, InferenceProfile, budget/deadline/cancel propagation, output schema and merge policy.
- CompletionContract observe/enforce modes based only on deterministic artifact/test requirements.
- Main/child cost and Context attribution.

### Milestone 6: Evaluation, Canary, and Default-On Readiness
**Goal:** Prove generalized framework benefit and safely enable enforcement.
**Depends on:** Milestones 1-5.

- Fixed cross-workload corpus, paired repeats, confidence intervals, cost model, hard gates.
- Terminal Bench plus at least one research/delegation/tool-heavy non-Terminal workload.
- Release shadow/canary, rollback bundle, entry-point parity, compatibility cleanup plan.
