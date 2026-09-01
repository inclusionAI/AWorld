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
- **Status:** complete for smoke/mechanism validation; benefit gate remains not-ready
- Only after Tasks 3.1-6.2 are code-complete: run unit/integration suites, local Docker Terminal Bench subset and at least
  one non-Terminal Tool/research/delegation workload; repair defects, then evaluate generalized Context benefit.
- One Terminal Bench and one non-Terminal Tool/research workload now have complete legacy/candidate pairs with independent
  reward, provider-owned capture, exact request trace, TaskResponse/live-Context continuity, Raw trajectory and checksum
  evidence. Both pairs are reward 1/1, but candidate request cost regressed and only 2/10 required pairs exist. This proves
  the mechanism and evaluation data plane, not generalized benefit or default-on readiness.

### Milestone 7: Candidate Overhead Attribution and Elimination
**Goal:** Explain every candidate-only provider-bound byte/token and remove general fixed overhead without task-specific policy.
**Depends on:** Milestone 6 smoke evidence.

#### Task 7.1: Provider-bound section attribution
- **Parallel:** no
- **Files:** compiler trace/models/final/runtime, provider capture, benefit report, focused tests.
- **Approach:** produce a privacy-safe, occurrence-preserving attribution from final provider messages/Tools to owner section,
  including bytes, estimated tokens, stable/dynamic residency and legacy/candidate delta. Unknown ownership remains explicit.
- **Tests:** duplicate content, repeated roles, Tool schemas, redaction, unmatched provider transforms, deterministic totals.
- **Acceptance criteria:** per-section totals reconcile exactly with provider-bound canonical payload counts and never rely on
  prompt-text heuristics.
- **Status:** complete; approved after three architecture review iterations

#### Task 7.2: Eliminate duplicate and non-resident candidate context
- **Parallel:** no; depends on 7.1
- **Files:** final compiler, sidecar/runtime integration, progressive catalog/skills, reducers.
- **Approach:** use attribution evidence to remove legacy/candidate double inclusion and Context metadata accidentally made
  model-visible; retain semantic equivalence, authority and Tool atomicity.
- **Tests:** semantic-equivalent request fixtures, off/observe compatibility, cache continuity, no required-context loss.
- **Acceptance criteria:** deterministic fixtures show no unexplained fixed candidate overhead and no hard-gate regression.
- **Status:** complete; observe residency and progressive Skill/Tool atomicity passed independent review

#### Task 7.3: Turn and artifact economics
- **Parallel:** yes after 7.1
- **Files:** evaluation report, Tool output runtime, generic noisy workload.
- **Approach:** attribute extra model/Tool turns and validate real model artifact retrieval when output crosses the same generic
  policy threshold; report inline/offloaded/read bytes and owner retrieval without benchmark-specific prompting.
- **Tests:** deterministic retrieval, real Docker opt-in, paired report reconciliation.
- **Acceptance criteria:** extra turns have typed causes; offload benefit and retrieval correctness are independently measurable.
- **Status:** complete for mechanism/economics truth; real paired benefit remains Milestone 9 evidence

### Milestone 8: Provider and Entry-Point Production Parity
**Goal:** Extend enforce only where the real send boundary can prove immutable candidate fidelity.
**Depends on:** Milestone 7 attribution contract.

- Add reviewed Azure lowering/canonical request evidence or keep it explicitly unsupported with complete parity tests.
- Define a safe registration contract for additional built-in providers; custom/self-declared providers remain fail-closed.
- Complete sync/async/stream/direct/Agent/Amni/CLI/ACP/resume parity matrices and error-path receipts.
- Verify external runtime/Scheduler projection through artifacts/contracts without modifying mcpgateway or
  lingguang-bench-runtime-dsh.
- **Status:** mechanism complete; Azure is explicitly unsupported across all call shapes, exact OpenAI parity receipts,
  lifecycle/trajectory binding and required capability-matrix gating are implemented. Local Amni/async evidence is verified;
  production entry-point canary evidence remains operationally pending.

### Milestone 9: Cross-Workload Benefit Evidence
**Goal:** Reach statistically valid quality or cost benefit evidence without benchmark-specific optimization.
**Depends on:** Milestones 7-8.

- Freeze at least 10 complete paired cases spanning Terminal, Tool/research, long-history, lifecycle, injection and delegation.
- Include 0/0, 0/1, 1/0 and 1/1 outcome classes when naturally present; never select policy after seeing answers.
- Run real-model variants at least five times where quality variance is measured, randomly interleaved.
- Add provider billing or a frozen/versioned normalized-cost model; report paired bootstrap confidence intervals.
- **Status:** evidence collection in progress; two complete cross-workload observe-baseline pairs have full attribution and a
  smoke-level normalized-cost efficiency path, but 2/10 pairs, workload breadth and stochastic repeats remain insufficient.

### Milestone 10: Canary, Default-On, and Legacy Cleanup
**Goal:** Ship only after all hard gates and one quality-or-cost benefit path pass.
**Depends on:** Milestone 9.

- Execute 100% shadow request-diff audit, then session-sticky 5% low-risk enforce with automatic rollback bundles.
- Validate all hard, quality, efficiency, latency, cache and security gates in the Spec.
- Default-on only for reviewed provider/entry-point capabilities; preserve one stable-version rollback switch.
- Remove duplicate legacy assembly paths only after parity and rollback acceptance.
- **Status:** control-plane mechanism complete; operational 100% shadow, 5% session-sticky enforce, external rollback bundle,
  canary health and post-gate legacy cleanup remain pending.
