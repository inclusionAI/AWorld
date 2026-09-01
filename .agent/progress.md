# Project Progress

## Current Status

**Phase:** Milestone 7 implementation
**Current milestone:** Candidate Overhead Attribution and Elimination
**Current task:** Task 7.2 — eliminate duplicate/non-resident context using verified attribution
**Last action:** Milestone 7.1 provider-bound attribution was approved after three independent review iterations. Compiler
provenance is ordinal-bound, provider shapes and canonical bytes are fail-closed, provider prepared/attempted state rolls
back on pre-send mutation/cache failure, report dimensions are bound to an independent compiler-plan fingerprint, and
manifest-complete paired attribution is a default-on hard gate. Main commits: `87aceab7`, `173f31dc`, `2c30f9f5`; focused
main-tree verification is 32/32 and the implementation worktree wider regression is 130/130.

### Task 7.2a: Legacy observe attribution baseline

**Status:** complete in the implementation branch

- Added a frozen `legacy_observed` provider attribution envelope and receipt. Observe derives its plan by request-bound
  message/Tool ordinals, never invokes candidate compilation, never replaces the legacy request, and degrades attribution
  failures without adding a second provider call. Off mode creates no attribution state.
- Exact reviewed OpenAI lowering now binds the observed plan to the actual SDK-prepared or controlled HTTP serialized
  payload. Candidate evidence is also dual-written to the provider-neutral attribution sibling for compatible reporting.
- Paired attribution reporting independently revalidates subject, provider raw payload, plan fingerprint, canonical bytes,
  and manifest completeness before computing section deltas.
- Added explicit sidecar residency/emission intent. Unspecified sidecars are evidence-only, preventing accidental duplicate
  model-visible insertion; intentional non-resident instruction/context-pack owners opt in structurally.
- Affected compiler, provider, reporting, progressive Skill, and delegation suites pass (177 tests).

## Completed Foundations

- Built-in Agent portability, optional native capability degradation, and direct-run typed failure/non-zero semantics.
- Provider-bound LLM call capture and compiler/provider snapshot merge.
- DockerSandbox attach implementation, bounded/reversible Tool output artifacts, real Docker capability gate.
- Generic paired benchmark driver, evidence provenance, and Context-benefit evaluation contract.
- Cross-workload paired smoke, actual-provider metric recomputation, dual artifact-ownership interoperability and real Docker
  artifact recovery gate.

## Current Milestone: Candidate Overhead Attribution and Elimination

### Task Status

| Task | Status | Notes |
|---|---|---|
| 7.1 Provider-bound section attribution | complete | Third review APPROVE; ordinal plan, provider receipt, raw revalidation, paired hard gate |
| 7.2 Eliminate duplicate/non-resident context | in_progress | Evidence-driven only; preserve semantic equivalence |
| 7.3 Turn and artifact economics | in_progress | Truth-plane audit complete; typed causal/retrieval receipts in implementation |

### Milestone 7 Evidence Audit (2026-09-01)
- The first provider request in both paired workloads is byte-equivalent after removing `prompt_cache_key`; system/user
  messages, all 15 Tool schemas, model and temperature are the same. The current evidence therefore does **not** support a
  compiler fixed-prompt-injection hypothesis.
- Terminal total request delta `+6,310` decomposes into messages `+13,332`, repeated Tool schemas `-6,148` from one fewer
  provider call, and other/provider structure `-874`. Candidate made fewer provider calls but more Tool calls inside turns.
- Non-Terminal total request delta `+52,313` decomposes into messages `+46,787`, repeated Tool schemas `+6,148` from one
  extra provider call, and other/provider structure `-622`. One run per variant cannot attribute the extra turn or Tool
  behavior to Context policy.
- Every call sends the same 15-Tool catalog (`6,148` canonical bytes per call); this is a general progressive-catalog/cache
  opportunity, not a candidate regression proven by these samples.
- The only directly attributable request difference is that legacy carries `provider_request.payload.prompt_cache_key`
  while candidate does not; candidate cache continuity is therefore `unavailable`. This is not yet classified as a defect:
  the sampled Amni folded system is dynamic and the candidate logical stable prefix is empty, so copying the legacy key may
  be unsafe. The provider boundary must first prove whether the legacy key represents the exact stable wire prefix.
- All four real-model runs report zero offloaded artifacts, so the mechanism's deterministic Docker receipt proof is not yet
  a behavioral benefit proof.

### Task 7.2 Progressive Tool Catalog Audit (2026-09-01)
- `progressive_tools=true` currently controls only sticky catalog transitions. The Agent builds the first
  `TaskCatalogSnapshot` from the complete permission-filtered catalog and never calls `compile_minimal_tool_catalog`, so all
  15 schemas in the paired runs are expected and do not prove a candidate-only regression.
- Safe minimization must be explicit opt-in. Missing/`None` base-Tool configuration preserves the complete filtered catalog;
  an explicit list selects only exact model-visible ids; an explicit empty list permits only Tools requested by activated
  Skills. No task-text, Tool-description, history or benchmark-specific inference is allowed.
- The result of `_filter_tools` is the sole permission upper bound. Skill configuration may request Tools but can never
  restore a Tool removed by server/workspace/user policy. Selection must preserve the filtered catalog order for cache
  continuity.
- Current Skill `tool_list` keys are commonly MCP server ids while the provider catalog uses model-visible function ids.
  These identities cannot be intersected or fuzzily matched. Unknown/ambiguous mappings require typed unavailable evidence;
  Skill content and its required Tools must remain atomic under enforce.
- Existing transition evidence conflates requested, applied and deferred additions, reports a cache break even when a
  deferred expansion leaves the actual snapshot unchanged, and gives `CHILD_CONTEXT` no isolation semantics. These contract
  defects must be fixed before real minimization is enabled.

### Task 7.3 Turn and Artifact Economics Audit (2026-09-01)
- The four real paired runs did not cross either output boundary. Candidate Docker output was at most 15,041 bytes versus a
  65,536-byte cap, and the largest Context ActionResult was 3,915 estimated tokens versus a 4,096-token cap. The 322,505-byte
  noisy stream was redirected into a workspace file, so it never became a Tool result. Zero offload is therefore valid
  mechanism evidence, not a runtime failure.
- Current `offloaded_artifact_count` scans only `run_dir/tool-output-artifacts/*.bin`; it does not prove all owner receipts,
  successful retrieval, later provider consumption or absence of double offload. These metrics must be recomputed from Raw
  trajectory, provider calls and checksum-bound artifacts, with unavailable used whenever the evidence chain is incomplete.
- A generic noisy-output validation fixture must return deterministic 128–256 KiB content directly through the Tool boundary,
  place required evidence outside the inline head/tail, and require receipt-bound retrieval. The paired prompt, Tool surface,
  fixture checksum and verifier remain invariant; variants may change only generic Context/Tool-output policy.
- Extra model/Tool turns require typed causal receipts written at scheduling boundaries. Supported causes are model choice,
  validation repair, framework retry, deferred catalog expansion, deferred Skill expansion and artifact retrieval. Counts
  cannot be inferred from text, Tool names or aggregate call differences.
- Existing run-level `cache_read_tokens` summaries are stale zeros while provider-call truth recomputes non-zero values;
  all new economics must continue to use the lowest available truth plane instead of trusting cached aggregates.

### Milestone 7.1 Architecture Review — Iteration 1
- Verdict: REQUEST CHANGES; P0/critical=0, important=4, minor=2.
- Important fixes required: bind absent/null/empty collection shape; atomically commit lowering + provider snapshot without
  premature `provider_invoked`/cache mutation; recompute attribution from raw provider payload in the benefit report; emit
  variant-paired section deltas and require complete coverage before claiming byte conservation.
- Minor follow-up: replace source-identity suffix correlation with typed sidecar request binding; preserve additive/versioned
  compatibility for exported frozen contracts or clearly make them internal.
- Positive result retained: emitted provenance is ordinal-selected then hash-validated; non-empty reorder/add/drop is
  fail-closed; SDK/HTTP fidelity and serialized checksum semantics are correct.

### Milestone 7.1 Architecture Review — Iteration 2
- Verdict: REQUEST CHANGES; no critical, two important remain.
- Closed: collection absent/null/array binding; provider prepared/attempted/cache rollback; typed sidecar request binding;
  additive constructor compatibility.
- Remaining: bind report dimensions to an independent compiler attribution-plan fingerprint so a valid-enum owner rewrite is
  rejected; derive expected attribution pairs from manifest cases × repeats × variants so a missing run cannot disappear
  from the hard gate.

### Milestone 7.1 Architecture Review — Iteration 3
- Verdict: APPROVE; no critical or important findings, focused adversarial suite 32/32.
- Independent compiler plan evidence and provider receipt now reject legal-enum owner rewrites; receipt cannot self-certify.
- Manifest Cartesian expected-run validation rejects missing, duplicate, unexpected or unavailable attribution pairs and
  emits `provider_attribution_pairing_incomplete` even when ten other pairs are complete.

## Architecture State — Milestone 7.1
- Final compiler emits an immutable, privacy-safe ordinal attribution plan in the same loop that emits messages/Tools.
- Reviewed OpenAI lowering validates collection shape, ordinal and hash, then records canonical provider bytes plus an
  explicit provider-envelope/params bucket; SDK and HTTP fidelity remain distinct.
- Provider execution records transition `prepared` → `attempted`; `provider_invoked` means the external invocation attempt
  started, while pre-send Context/cache failures roll back and prevent external action.
- Benefit reports recompute attribution from provider request truth, compare independent plan/receipt fingerprints, and only
  expose paired section deltas when every manifest-declared run has valid evidence.
- Known limitation: legacy/off runs created before attribution do not have comparable receipts; they remain explicitly
  unsupported rather than being heuristically classified.

### Decision: Continue from evidence, not benchmark outcomes
- Options considered: tune Terminal prompts; reduce arbitrary sections; first add exact provider-bound attribution.
- Chose: exact attribution first.
- Rationale: current aggregate deltas cannot distinguish duplicate assembly, stable-prefix cost, Tool schema growth or model
  variance. Removing context without owner/request evidence would violate the framework hypothesis and risk score tuning.
- Trade-offs accepted: another compiler/reporting slice precedes broader paired runs.

### Decision: Do not treat single-sample Tool behavior as compiler overhead
- Options considered: remove Context based on aggregate prompt deltas; tune Tool-use prompts; isolate request components and
  repeat model runs before causal changes.
- Chose: exact component attribution, restore cache-key parity only where provider-wire stability is proven, then repeat
  paired runs.
- Rationale: initial request semantics are equal and later deltas are history accumulated from different stochastic Tool
  paths. A single sample cannot establish causality.
- Trade-offs accepted: no immediate token-reduction claim from the current two pairs.

### Decision: Progressive Tool selection is explicit and permission-bounded
- Options considered: infer a minimal catalog from the task/prompt; make `progressive_tools=true` immediately minimize;
  introduce an explicit base set and combine it with precisely resolved activated-Skill requirements.
- Chose: explicit base set with `None` preserving the current full catalog, exact identity resolution, permission
  intersection, original-order retention and task-sticky expansion semantics.
- Rationale: a Context framework must reduce generic repeated schema cost without learning benchmark answers or silently
  changing existing applications. Exact owner evidence and ACL intersection make the reduction causal and auditable.
- Trade-offs accepted: applications without an explicit base set see no schema reduction; unavailable Skill identity
  mappings fail closed instead of receiving a guessed Tool.

### Decision: Scope of provider parity
- Options considered: authorize OpenAI-compatible subclasses generically; modify external runtimes; review each built-in send
  boundary and keep unsupported providers fail-closed.
- Chose: per-boundary reviewed built-in parity with artifact-level external integration only.
- Rationale: self-declared compatibility does not prove immutable lowering, and the user explicitly excludes mcpgateway and
  lingguang-bench-runtime-dsh code changes.
- Trade-offs accepted: default-on can initially cover only a subset of providers/entry points.

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

### Decision: Runtime candidate compiler boundary
- Options considered: inject an arbitrary compiler Protocol; run third-party compilers with timeouts; use one sealed,
  framework-owned pure function with frozen declarative input/policy.
- Chose: a sealed pure function and exact frozen policy type; runtime APIs expose no callable, Context, provider, Tool,
  workspace, artifact repository, or action executor to candidate compilation.
- Rationale: a capability-free input does not constrain capabilities retained by an injected object, and a synchronous
  arbitrary compiler can both perform hidden external actions and indefinitely block shadow's legacy request.
- Trade-offs accepted: third-party compiler extensions require a future isolated serialized boundary; the current runtime
  policy supports only framework-reviewed deterministic compilation.

### Decision: Enforce readiness at the current model boundary
- Options considered: thaw and send a model-boundary candidate; label a compiler-ready snapshot enforceable; block until a
  provider owns immutable lowering and execution of the same snapshot.
- Chose: fail closed before provider invocation and persist one `blocked_before_provider` llm-call attempt with
  `provider_invoked=false`, candidate/legacy hashes, structural fidelity, projection, direction, compiler identity/version,
  overhead, and a typed reason.
- Rationale: the current projection omits provider-specific kwargs/serialization and cannot prove that the candidate is the
  exact provider-bound request. Persisting the block distinguishes execution policy failure from missing trajectory capture.
- Trade-offs accepted: normal-model enforce is intentionally unavailable until provider lowering supplies immutable
  provider-prepared/serialized evidence; shadow remains the supported runtime rollout mode.

### Decision: First provider-owned lowering slice
- Options considered: trust any provider capability declaration; enable all OpenAI-compatible subclasses; authorize only a
  reviewed exact built-in provider class and add providers after their real send boundaries are tested.
- Chose: exact built-in `OpenAIProvider` registration with a frozen candidate envelope and versioned
  `ProviderLoweringReceipt`; Azure and custom providers remain blocked even if they self-declare the same interface.
- Rationale: a declaration alone cannot prove the candidate was applied. The OpenAI adapter now owns candidate projection,
  final parameter freezing, unique request-id receipt binding, and the immediately following SDK/HTTP invocation.
- Trade-offs accepted: evidence is `PROVIDER_PREPARED` structural fidelity, not HTTP serialized bytes; enforce requires a
  writable Context receipt and initially covers only OpenAI Chat Completions.

## Architecture State

### Components
- `llm_calls`: provider request truth with provider-bound snapshots.
- Provider lowering: exact registered adapters consume frozen candidates and persist provider-prepared hash receipts before
  sending the same structure; the top-level raw request remains truthfully labeled model-boundary fidelity.
- Runtime events/TrajectoryDataset: action/result truth and mutable SAR projection.
- Docker Tool output policy: bounded inline view plus checksummed retrievable artifact.
- Benchmark driver: frozen invariant manifests, independent verifier, paired Context metrics.

### Known Issues
- Native streaming still needs a runner-local terminal fallback for failures before EventManager initialization and for
  partial publication failures.
- Cancellation cleanup/finalization must preserve the original `CancelledError` even when secondary operations fail.
- `trajectory.log` remains an unacknowledged legacy logger/Python-repr projection; only JSONL v2 can claim persisted.
- Current compiler/request capture can observe hook drift but does not yet enforce a universal final boundary.
- Provider-owned enforce currently covers only exact built-in `OpenAIProvider`; Azure/custom providers remain fail-closed,
  and HTTP serialized-byte fidelity still requires a transport-owned serialization receipt.

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
- Hardened sidecar redaction after adversarial review: owner-controlled diagnostic codes are hashed and unknown fields are
  reduced to counts, so default observability cannot leak arbitrary diagnostic strings. Child construction and root task-id
  transitions now clear request observations instead of silently inheriting an epoch-less prior-task sidecar; same-task deep
  copies still retain the immutable observation. Checkpoint stale/unavailable diagnostics remain part of Milestone 4.
- Hardened the budget planner after adversarial review. Atomic identities are now typed and owner/namespace-scoped, internal
  singleton keys cannot collide with caller groups, and optional allocation uses explicit global tiers while comparing
  item priority only within the same authority/scope domain. Complete reserve evidence is fingerprinted in
  `TokenAccounting`, item-cap group mates receive a distinct reason, and enforce refuses unversioned estimator identities.
  Independent budget re-review approved P0/P1=0 at 33 tests. The broader compiler/Amni/TaskRunner regression is green at
  67 tests, including cross-authority Tool pairs and real root Context reuse. Independent sidecar/lifecycle re-review also
  approved P0/P1=0; both Base Context and ApplicationContext task-transition behavior now have persistent regressions.
- Added the first pure cache layer: contiguous stable-prefix partitioning without reordering, logical stable/dynamic hashes,
  complete `InferenceProfile`/`CacheBreakReason` comparison, and exact byte checksums. A serialized prefix can create a
  provider-verified identity only through typed HTTP-serialized evidence bound to provider, adapter/version, request id,
  request checksum, capture stage, and fidelity; logical canonical JSON bytes are rejected. The durable `CacheIdentity`
  alone deliberately does not regain runtime verification when deserialized.
- Added side-effect-free `off/observe/shadow/enforce` request selection. Off/observe preserve the exact legacy snapshot,
  shadow compares an immutable candidate without applying it, and enforce alone selects the candidate for the existing
  provider call. Comparison paths hash all non-allowlisted mapping keys so params/Tool/message keys cannot leak raw owner
  strings. Runtime integration and counted fake provider/Tool/offload tests remain required before shadow/enforce rollout.
  The complete dependency-light compiler suite is green at 58 tests. Independent cache and rollout/privacy re-reviews both
  approved P0/P1=0; the cache evidence review additionally verified that repr/asdict retain only hashes and lengths.
- Wired rollout selection into async, sync, sync-stream, and async-stream model paths. Off preserves the pre-existing llm-call
  record shape; observe never compiles; shadow compiles once with a sealed framework pure function, sends the exact original
  legacy messages/Tools once, and records only redacted structural evidence. Candidate compilation consumes immutable
  model-boundary snapshots plus owner observation sidecars and cannot receive runtime action capabilities.
- Removed the arbitrary in-process compiler Protocol after adversarial tests proved it could retain provider/Tool/offload
  capabilities and block the async event loop. Candidate policy and mode are exact-typed, private, and exposed read-only;
  compiler identity/version are bounded identifiers and raw diagnostics/errors never enter rollout metadata.
- Enforce now records a pre-provider blocked attempt instead of disappearing before llm-call capture. The receipt explicitly
  says `provider_invoked=false`, retains candidate/legacy logical hashes and MODEL_BOUNDARY fidelity without claiming
  provider bytes, and round-trips through the real JSONL v2 sink/reader. Provider-lowered immutable execution remains the
  next gate. Focused compiler/model/trajectory regression is 140 passed; Agent/hook regression is 19 passed. Two independent
  re-reviews approved P0/P1=0 after reproducing the original ambient-capability and missing-evidence counterexamples.
- Added provider-owned immutable lowering for the exact built-in OpenAI Chat Completions adapter. A frozen envelope binds
  compiler/candidate identity to a versioned provider capability; sync/async/stream paths freeze the final params, commit a
  redacted `PROVIDER_PREPARED` receipt to the unique llm-call, then invoke SDK/HTTP with the same structure. Missing Context,
  receipt failures, unsupported schemas/transforms, unsnapshotable lowering, non-ready candidates, Azure, and self-
  authorizing custom providers all block before send. Focused lowering/runtime tests are 15 passed; the wider selected run
  is 180 passed with four pre-existing `ModelResponse.usage` expectation failures in an untouched test/module pair.
- Local final review kept the raw/capture fidelity boundaries distinct, labeled the legacy observer as pre-rollout baseline,
  verified that private envelopes never reach SDK/HTTP params, and reran the focused compiler/OpenAI/trajectory/Agent/hook
  suite at 108 passed. No reviewed issue remains open in this slice.
- Integrated Milestones 3-6 validation is open. Focused scope/progressive/offload/delegation/completion/evaluation/canary,
  OpenAI lowering, HTTP serialization, Amni, runner trajectory and Docker adapter regression reached 170 passed before
  real-workload smoke; subsequent integration fixes have dedicated regressions and the latest affected set is 31 passed.
- Real local Docker `prove-plus-comm` validation exposed five framework integration defects and fixed them without task-
  specific policy: blank optional trace-id normalization, known low-trust Amni folded system semantics, no legacy prompt-
  plan replay after final compilation, atomic latest Tool-turn tiering, and append-first diagnostic capture with live-Context
  fallback/provider-lowering receipt recognition.
- The repaired unified-context-enforce rollout completed with TaskResponse success, eight raw trajectory items, eight
  successful provider calls and eight provider-prepared receipts. TaskResponse carried zero call records while the live
  Context carried eight, and the manifest now records that continuity mismatch instead of dropping evidence. The local
  Docker daemon exited before independent verifier/result aggregation, so this is an execution/capture proof only, not a
  benchmark reward or Context-benefit claim.
- Authority, trust, lifetime, stability, source URI, task epoch, and exact token counts remain UNKNOWN unless their owner can
  prove them; role/content heuristics are not acceptable.
