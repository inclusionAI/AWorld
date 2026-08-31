# Project Progress

## Current Status

**Phase:** Milestone 3
**Current milestone:** Universal Final Compiler and Rollout Modes
**Current task:** Wire owner-side pre-fold observations before implementing enforce mode
**Last action:** Milestone 2 passed independent review at `429fdf39` with P0=0/P1=0. Agent capture is fail-open,
model-boundary fidelity is explicit, correlation is task-local, and full Agent/model suites pass 16/15 tests.

## Completed Foundations

- Built-in Agent portability, optional native capability degradation, and direct-run typed failure/non-zero semantics.
- Provider-bound LLM call capture and compiler/provider snapshot merge.
- DockerSandbox attach implementation, bounded/reversible Tool output artifacts, real Docker capability gate.
- Generic paired benchmark driver, evidence provenance, and Context-benefit evaluation contract.

## Current Milestone: Context Models, Adapters, and Observe Mode

### Task Status

| Task | Status | Notes |
|---|---|---|
| 1.1 Typed trajectory build contracts | complete | Immutable control result, canonical checksum, invariants, TaskResponse projections |
| 1.2 Tracked update/finalize barrier | complete | Root registry, HWM drain, revision fence, typed finalize and cancellation |
| 1.3 JSONL v2 dual-read/write | complete | Finalize-time dual writer, codec, real legacy reader, checksum/revision selection |
| 1.4 Integration and architecture review | complete | Fifth review approved: P0=0, P1=0; focused TC suite 10 passed |

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

### Decision: First enforce-mode budget hard gate
- Options considered: shrink reserved output automatically; use heuristic zero/defaults for unknown estimates; fail with a
  typed budget result before request enforcement.
- Chose: keep output/protocol/safety reserves immutable and reject required overflow or unknown estimates before enforce.
- Rationale: silently changing output capacity or treating unknown as zero makes benchmark gains non-causal and can drop
  required Context. Reducers must run explicitly before the planner and supply a new versioned estimate.
- Trade-offs accepted: shadow can diagnose requests that are not yet enforceable; early enforce coverage is narrower.

### Decision: Atomic budget groups
- Options considered: independently rank every occurrence; reconstruct Tool pairs after pruning; require owner-supplied
  atomic groups and select them as one unit.
- Chose: owner-supplied atomic groups, with any required member making the complete group required.
- Rationale: the budget layer must never create an orphaned Tool call/result or split another owner-defined invariant.
- Trade-offs accepted: owners must emit group evidence before enforce; the planner does not infer pairs from text.

## Architecture State

### Components
- `llm_calls`: provider request truth with provider-bound snapshots.
- Runtime events/TrajectoryDataset: action/result truth and mutable SAR projection.
- Docker Tool output policy: bounded inline view plus checksummed retrievable artifact.
- Benchmark driver: frozen invariant manifests, independent verifier, paired Context metrics.

### Known Issues
- Native streaming still needs a runner-local terminal fallback for failures before EventManager initialization and for
  partial publication failures.
- Cancellation cleanup/finalization must preserve the original `CancelledError` even when secondary operations fail.
- `trajectory.log` remains an unacknowledged legacy logger/Python-repr projection; only JSONL v2 can claim persisted.
- Current compiler/request capture can observe hook drift but does not yet enforce a universal final boundary.

### Audit Findings
- Three event-runner trajectory updates are bare `create_task` calls and can race the final storage read.
- The same logical message has before/after builds without revision fencing; completion order can overwrite newer state.
- Update/storage exceptions are swallowed, so coroutine completion does not prove persistence acknowledgement.
- Existing evaluation reader fails on real Loguru headers when iterating all records and selects the first repeated task.
- Runtime/ATIF projection receipts live outside this repository and must not be claimed complete in Milestone 1.
- Milestone review found `TASK_RESPONSE` could reach streaming consumers before finalization; terminal delivery must be
  deferred until the final registry revision is drained and bound.
- Fixed a dataset lazy-import regression that left TaskConfig subclasses with unresolved Pydantic forward refs; the wider
  runner/evaluation/dataset suite now passes 219 tests.
- Fixed separate/remote child imports so they open, drain, acknowledge, fence, and retain diagnostics in the root registry.
- Envelope invariants, legacy partial fidelity, execution-not-started classification, retry epoch, delivery receipts, and
  finalize-before-response are implemented and in the second review pass.
- Second review found native-stream bootstrap hangs, non-atomic terminal publication, cancellation masking, rewritten
  legacy canonical metadata, a false legacy persistence receipt, and weak float epoch validation.
- Fixed legacy embedded build-result round-trip, remote child import independence from emitted handler-event count,
  strict integer epochs, `emitted/unacknowledged` legacy receipts, and Redis publish failure propagation. Focused tests:
  49 passed after these fixes.
- Added runner-local terminal fallback and bootstrap completion signals so pre-run and partial emit failures terminate native
  streams. Publication is fenced before the first external attempt and cannot be retried into duplicate responses.
- Hardened terminal cleanup so finalize, publish, output, and sandbox cleanup failures cannot replace an original or newly
  arriving `CancelledError`; focused cancellation/publication tests pass.
- Added one TC-TRAJECTORY-IO-023 fixture that uses the real configured trajectory logger in an isolated process, dual v2
  appends, and a second epoch/revision for the same task. It verifies physical JSON lines plus formal legacy/v2/mixed latest
  reads and canonical checksums.
- Post-fix wide regression: 304 passed, four skipped across runners, evaluations, dataset, trajectory contracts, compiler
  adapters/models/trace, PromptSection adapter, and Redis delivery propagation.
- Third review confirmed all second-review findings closed, then reproduced three deeper interleavings not covered by the
  suite. TC-IO-023 is approved; TC-FINALIZE-021 and the generalized TC-EMPTY-022 contract remain blocked until a single
  shielded finalize/delivery attempt and typed projection failure are implemented.
- Fixed those three interleavings: emitter cancellation installs fallback before propagation; a cancelled `to_thread`
  append is joined rather than recreated; pre-run and normal finalization share the same attempt; SAR projection/checksum
  failures bind `FAILED/BUILD_FAILED/TRAJECTORY_BUILD_FAILED` with no inline items. Main-tree trajectory regression:
  104 passed, including real IO-023.
- Fourth review approved the normal-run repeated-cancellation path and all prior fidelity/delivery fixes, but found one P1
  in the execution-not-started path: after two cancellations during a blocked v2 append, the run restores the pre-run
  failure before `_publish_task_response_once`, leaving native streaming without a terminal response or fallback. The
  physical append remains exactly once and its receipt eventually persists, so the remaining defect is terminal-delivery
  ordering rather than trajectory duplication or storage corruption.
- The post-observer/post-third-review wide regression is green: 322 passed and four skipped across runners, evaluations,
  dataset, trajectory contracts, compiler/owner adapters, PromptSection, and Redis delivery propagation.
- Replaced the execution-not-started fixed single compensation wait with an unbounded cancellation join loop also used by
  the normal path. Regression covers two and three cleanup cancellations plus a primary cancellation with two additional
  cleanup cancellations; the focused main-tree run is four passed, with one append/record and one native stream terminal.
- Fifth independent review approved Milestone 1 with no P0/P1. It also stress-tested ten caller cancellations for both a
  primary RuntimeError and primary CancelledError: both retained exactly one persisted record and one stream terminal while
  restoring the primary outcome. The TC-FINALIZE/EMPTY/IO plus repeated-cancel focused suite is ten passed.
- Non-blocking P2 carried forward: the thread-backed JSONL append/flock/write path has no bounded I/O acknowledgement, so a
  permanently stuck exporter intentionally delays cleanup cancellation. A future bounded exporter must remain idempotent
  and confirm persistence; cancelling a file-writing worker thread is not an acceptable fix.

### Milestone 2 Readiness
- Task 2.1 dependency-light frozen models/trace is merged; it has no runtime integration and therefore cannot bypass the
  unfinished Milestone 1 finalize/writer gate.
- Task 2.2a generic occurrence-preserving legacy adapters and the Amni PromptSection owner adapter are implemented and
  merged, tested, and exposed through owner/core public APIs. Their dedicated seven tests pass; a broader context run had
  84 passes and nine unrelated AWORLD-file tests that selected the user's real `~/.aworld/AWORLD.md` instead of fixtures.
- Task 2.2b adds an Agent-owner adapter for the exact replay occurrences after `LLMAgent.async_messages_transform` cleanup.
  It delegates to the generic occurrence adapter, performs no second cleanup/inference/runtime integration, and preserves
  repaired Tool ordering and duplicates; adapter plus owner cleanup regression is 16 passed.
- Task 2.3 adds a pure legacy-request observer: exact immutable request snapshot, one legacy-included decision per
  occurrence, unknown token/hash evidence when unproved, redacted trace IDs, and raw-value-free mismatch paths. It performs
  no resolver/provider/runtime action; compiler plus owner adapter regression is 46 passed.
- Added owner-side Skill descriptor and loaded-content adapters. They preserve caller order, duplicates, and the complete
  owner payload while leaving activation, authority, scope, lifetime, trust, stability, budget, and token semantics unknown.
  They do not load/activate Skills or alter prompt behavior; Skill adapter/provider regression is 18 passed.
- Added a CLI steering owner adapter for already-drained `SteeringInput` occurrences. It preserves coordinator sequence,
  duplicates, text, and timestamp; maps only explicitly supplied session/task scope; and leaves policy semantics unknown.
  It neither drains nor applies steering and does not alter the existing hook; steering regression is 67 passed.
- Added a final Tool Catalog owner adapter that observes the exact schema occurrences after owner filtering/lowering. It
  reuses the generic immutable occurrence adapter, preserves full nested schemas/order/duplicates, and never minimizes,
  authorizes, deduplicates, or infers policy from names/descriptions; related adapter regression is 12 passed.
- Added an Amni neuron pre-fold sidecar contract and owner adapter. It observes exact executed neuron outputs without
  executing neurons or reconstructing them from the folded system message, preserves identity/order/duplicates, and only
  maps explicitly supplied owner evidence; new contract tests are five passed and combined compiler/assembly is 46 passed.
- The combined dependency-light compiler plus all current owner adapters is green at 55 passed. A duplicate pytest module
  basename discovered only in the combined run was removed by giving the Skill adapter test a unique module name.
- Existing PromptAssemblyProvider still sees Amni neuron sections only after folding; the new `NeuronOutputOccurrence`
  contract provides the required pre-fold sidecar shape, but runtime emission remains deliberately unconnected in observe
  adapter scope and must be wired at the owner assembly boundary before enforce mode.
- Memory adapters must follow the exact cleaned replay list, including Tool pair repair and duplicate occurrences.
- Removed the former post-success/latest-unmatched Agent correlation heuristic; request identity is now reserved before the
  provider call so failures and concurrent calls bind deterministically.
- Model-boundary observe now begins the authoritative `llm_calls` record after compatibility transforms and before provider
  invocation, then finishes that exact request-id record on success/failure/cancellation. Agent calls pass a private exact
  call-id that is popped before provider invocation; direct model calls append independently. The nested observe snapshot
  declares only `model_boundary` fidelity, stores redacted trace/hash evidence, and leaves raw extra kwargs/secrets out.
- Provider failure, caller cancellation, sync/async stream failure, and early stream close retain one terminal snapshot;
  same-agent concurrent requests do not cross-correlate. Main-tree model-boundary/capture/Agent/hook regression: 31 passed.
- Adversarial review reproduced a fail-open violation outside the pure observer: if `llm_calls` storage raised during begin,
  the provider was never called; if it raised during finish, it replaced provider success or the primary provider error.
  Begin/finish storage are now guarded by redacted type-only warnings, and focused regressions prove provider success/error
  semantics survive capture-storage failure (two passed).
- The next adversarial pass reproduced four further model-boundary P1s: helper-level early async-stream close did not close
  the underlying model generator; a reused Agent call id overwrote the previous provider attempt; a child `ContextState`
  mutated an inherited parent `llm_calls` list; and stream response folding could leak capture exceptions into provider
  delivery. The implementation now closes delegated streams, appends one attempt record per provider invocation, applies
  child copy-on-write isolation, and contains response-folding failures. All four focused regressions pass.
- Model-boundary records no longer claim `provider_bound` fidelity: they now identify the exact AWorld standard projection,
  explicitly leave provider-prepared matching unknown, and scope compiler matching to that projection. Match-observation
  failures retain unknown (`None`) mismatch evidence instead of falsely reporting zero differences. Async process-control
  exceptions now also close the request record before the original `KeyboardInterrupt`/`SystemExit` is re-raised.
- `ContextDecisionTrace.build` now redacts item identifiers by default, closing a path/URI disclosure risk for owner-derived
  ids while retaining an explicit opt-out for trusted local diagnostics. Model boundary, legacy capture, and trace regression
  is green at 30 passed.
- `AdapterDiagnostic` now tuple-freezes caller-supplied `unknown_fields`, so its frozen dataclass contract cannot retain a
  mutable list alias. The dedicated legacy adapter suite is green at five passed.
- Agent-side compiled-request, assembly-metadata, and response capture now use fail-open wrappers, so their serialization or
  `ContextState` failures cannot skip `invoke_model`, replace a successful response, or mask provider failure/cancellation.
  The invoke `finally` only performs response business logic after a normal return; active failures are re-raised unchanged
  after best-effort capture. Seven focused Agent regressions pass, including the pre-existing interrupted-task path.
- Agent/model correlation now travels through a task-local `ContextVar` for the duration of `invoke_model`. Custom Agent
  overrides no longer receive the private `_aworld_context_call_id` extension kwarg, while the standard model boundary still
  resolves the exact compiled call id; the historical direct-call kwarg remains accepted and is popped before providers.
- Full post-fix Agent call-record and model-boundary suites are green at 16 and 15 tests respectively.
- Independent final review approved Milestone 2 with no P0/P1. Accepted P2s: a failed best-effort finish may retain an
  `in_progress` record; ContextVar correlation does not cross a caller-created non-context-propagating thread; synchronous
  capture wrappers deliberately do not swallow process-control `BaseException`. None changes provider/task semantics.
- Milestone 3 first owner integration now publishes exact Amni neuron outputs before the legacy dict sort/fold into an
  immutable `ContextObservationSidecar`. Sidecars live outside serialized `ContextState`, survive Context copies, expose
  only redacted refs/diagnostics to `llm_calls` assembly observability, and are never passed into prompt assembly. Capture
  errors are fail-open, while the existing prompt messages/order/content remain unchanged. Sidecar/Amni/Agent regression:
  29 passed; compiler files and affected modules also pass `py_compile` and diff checks.
- Added the first deterministic input budget planner. It computes the spec reserve formula, requires versioned token
  estimates, keeps required/atomic groups intact, selects optional groups by explicit priority, and returns selections in
  original request order with one decision per occurrence. Required overflow, unknown estimates, and required item-cap
  violations are typed failures; no reducer, offload, reserve mutation, or payload rewrite occurs implicitly. Dedicated
  budget tests are 6/6 and the combined compiler/PromptSection/neuron adapter suite is 48/48.
- Authority, trust, lifetime, stability, source URI, task epoch, and exact token counts remain UNKNOWN unless their owner can
  prove them; role/content heuristics are not acceptable.
