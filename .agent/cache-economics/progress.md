# Project Progress

## Current Status

**Phase:** Milestone 4 complete
**Current milestone:** Capability-aware release decision
**Current task:** Complete
**Last action:** Final cross-provider and compatibility review passed. The
provider-neutral Adaptive/Context layer reuses the existing 12-pair machine
`ready` decision and remains default-on; native controls share one capability
contract, and the configured custom endpoint remains safely ineligible after
directional regression evidence.

## Completed Milestones

- Milestone 1: Cache truth and provider conformance preflight.
- Milestone 2: Immutable CachePlan lowering.
- Milestone 3: Stable sections and cache epochs.

## Current Milestone: Verification and release evidence

### Task Status

| Task | Status | Notes |
|---|---|---|
| 4.1 Deterministic cache/replay suite | complete | Recomputes provider payload/usage and validates candidate/plan/lowering binding plus epoch transitions |
| 4.2 Frozen real-provider paired evaluation | complete | No-hint safety proven; explicit native hint rejected early after four causal pairs showed directional regression |
| 4.3 Final review and release decision | complete | Safe generic layer default-on; custom native controls capability-gated and current endpoint not eligible |

### Review Feedback

Milestones 2-3 architectural review: APPROVE after lifecycle fixes.

## Completed Milestone Details: Cache truth and provider conformance preflight

### Task Status

| Task | Status | Notes |
|---|---|---|
| 1.1 Cache usage fidelity and receipts | complete | Receipt is stored in the append-only call record and round-trips through trajectory JSONL |
| 1.2 Provider-capability cache preflight | complete | Generic stream/nonstream runner, fake-provider tests, live GLM conformance, and optional benchmark fail-closed gate are complete |
| 1.3 CLI/report observability | complete | CLI and benefit report expose exact coverage, fidelity counts, and exact uncached input; cache epochs/breaks are intentionally Milestone 3 |

### Review Feedback

Milestone 1 review: APPROVE after fixes.

- Important issue fixed: componentized usage schemas report base input, cache read,
  and cache creation separately, unlike inclusive prompt-token schemas.  The
  receipt now infers accounting semantics from fields rather than provider name,
  records the basis explicitly, and computes one total logical input denominator.
- Important issue fixed: CLI and reports now share one reconciliation helper and
  cannot trust a modified captured receipt over raw provider usage.
- Important issue fixed: Anthropic response normalization includes read/write
  cache components in total logical input and preserves streaming usage events.
- Minor issue fixed: async preflight no longer swallows cancellation/system-exit
  via `BaseException`.

## Decisions Log

### Decision: Optimize cache economics, not raw hit ratio
- Options considered: maximize hit ratio; minimize prompt tokens; minimize
  cache-adjusted/uncached work per successful task.
- Chose: cache-adjusted/uncached work per successful task with quality guardrails.
- Rationale: Existing 12-pair evidence reduced uncached input by about 72.7% even
  though aggregate cache-read ratio fell from about 55.8% to 53.0%.
- Trade-offs accepted: A compaction may intentionally create one cold request.

### Decision: Provider-neutral core with optional native adapters
- Options considered: GLM-specific core, server opaque compaction, neutral CachePlan.
- Chose: neutral CachePlan and provider adapter lowering.
- Rationale: AWorld is a harness and must not bind Context semantics to one model.
- Trade-offs accepted: Providers without authoritative usage produce unavailable or
  bounded cache evidence rather than an exact result.

### Decision: GLM is a conformance target, never a core dependency
- Options considered: GLM-specific schema and gate; one generic schema with
  provider-declared aliases/capabilities; no live cache validation.
- Chose: one generic schema with provider-declared aliases/capabilities.
- Rationale: The optimization must adapt to every model while still using the
  currently available GLM endpoint to establish real provider truth.
- Trade-offs accepted: Actual cache-benefit claims are made per cache-capable
  provider; unsupported providers retain correctness and report unavailable
  cache economics rather than fabricated hits.

### Decision: Use current main Adaptive as the evaluation baseline
- Options considered: legacy Context baseline; full Adaptive bundle contrast;
  same Adaptive runtime with one cache component changed.
- Chose: same Adaptive runtime with one cache component changed.
- Rationale: This is required to attribute effects to cache work.
- Trade-offs accepted: Cache changes are evaluated primarily for efficiency rather
  than re-proving the complete Adaptive bundle.

## Architecture State

### Components
- Existing Context Compiler: stable/dynamic logical partition and final candidate.
- Existing provider lowering: immutable enforce envelope and provider-wire evidence.
- Existing usage normalization: preserves raw usage but compatibility-normalizes
  aliases and zeros.
- Existing evaluation bounds: rejects missing/conflicting cache truth for exact
  cost and emits conservative bounds.

### Connections
- Prompt assembly → Context Compiler → immutable candidate → provider adapter.
- Provider response → ModelResponse raw/normalized usage → call journal → Raw
  trajectory → evaluation report.

### Patterns Established
- Exact cache truth and compatibility summaries must be separate APIs.
- Every cache claim carries explicit fidelity.
- Compaction cache breaks are allowed only when explained and followed by a stable
  append-only epoch.

### Known Issues
- No provider-native control currently has the ten complete positive pairs over
  two workload kinds required for its own default-on release. The configured
  custom endpoint has stronger negative evidence and is deliberately ineligible;
  this is not an unresolved gate for the provider-neutral stable-prefix,
  cache-epoch, usage-receipt and fail-safe capability layer.
- Local disk has about 5 GiB free. A dedicated 2 CPU / 2 GiB Colima profile is
  used serially; large SkillsBench images are excluded from this local pass.
- Historical GLM release evidence has 451/468 exact calls and remains diagnostic
  only; new release claims require 100% exact cache usage per complete pair.

## Action Log

### 2026-09-11 — Task 1.1 core receipt
- Added a provider-neutral cache usage fidelity model that preserves explicit zero
  and distinguishes exact, bounded, conflicting, invalid, and unavailable data.
- Supports OpenAI-compatible nested `cached_tokens` and Anthropic-style
  `cache_read_input_tokens` / `cache_creation_input_tokens` without a
  provider-specific core branch.
- Added the receipt to the append-only Context LLM call record.
- Focused validation: 29 tests passed.
- Repository already contained unrelated root `.agent/` state; restored it and
  isolated this project's state under `.agent/cache-economics/`.

### 2026-09-11 — Task 1.2 generic preflight and Task 1.3 reporting
- Added a provider-neutral cold/repeat/suffix-change/prefix-change cache probe for
  streaming and non-streaming model calls.  It branches on capabilities, not on
  provider or model identity, and redacts prompt, response, endpoint, and secret
  material from its output.
- Live GLM conformance was 8/8 exact: cold and changed-prefix requests reported
  zero cache reads, while exact repeats and suffix-only changes reused 10,752
  tokens in both stream and non-stream modes.  This is one conformance result for
  the generic contract, not a GLM dependency.
- CLI cache observability now separates exact coverage and uncached input from
  heuristic stable-prefix candidates.
- Benefit reporting recomputes receipts from raw/normalized usage, verifies any
  captured receipt, counts bounded/conflicting/invalid/unavailable calls, and
  sums cache/uncached totals only for exact calls.
- Focused validation: 155 tests passed; scoped Ruff and `git diff --check` passed.
- Both benchmark harnesses now support `--require-cache-usage-preflight`; when
  selected, no rollout starts unless the provider reports exact usage for all
  probes and exhibits repeat/suffix reuse plus changed-prefix invalidation.
- Harness validation: 115 tests passed; scoped Ruff and `git diff --check` passed.

### 2026-09-11 — Milestone 1 architectural review
- Confirmed from provider usage semantics that inclusive prompt totals and
  componentized input/cache totals require different arithmetic.  Implemented
  schema-driven accounting (`inclusive` or `exclusive_cache_components`) with
  no provider-name branch in the core receipt.
- Added shared captured/recomputed receipt reconciliation used by both CLI and
  release reporting.
- Corrected Anthropic adapter response normalization and preserved usage-bearing
  stream events, so a second provider family exercises the generic contract.
- Review validation: 233 focused tests passed; scoped Ruff and `git diff --check`
  passed.  Verdict: APPROVE.
- Expanded regression: 339/343 selected tests passed.  The four failures are an
  existing contradictory OpenAI `ModelResponse.usage` expectation in
  `test_model_response_llm_calls.py`: those tests require cache detail to be
  stripped while the unchanged OpenAI path and `test_model_response_usage.py`
  require it preserved.  This milestone does not alter that OpenAI path; all 233
  directly relevant tests remain green.

### 2026-09-11 — Milestone 2 implementation
- Added a provider-neutral immutable CachePlan to the universal final compiler.
  It binds inference identity, stable prefix, Tool/Skill hashes, cache epoch,
  lifecycle break reasons, optional routing namespace, and native-cache policy
  to a candidate contract hash.
- Removed the runtime's post-compile reconstruction of cache material. Provider
  receipts must now bind the exact compiler-produced plan before any request is
  attempted.
- OpenAI-compatible adapters preserve exact prefixes without claiming cache
  support, optionally lower an explicit namespace to `prompt_cache_key`, and
  preserve caller override/opt-out. Anthropic lowers the stable boundary to
  native `cache_control`; reviewed providers without native controls report
  `unsupported` and retain request correctness.
- Runtime Context checkpoint revision and pending invalidation reasons now enter
  the frozen plan. Inspector output hashes routing namespaces.
- Focused validation: 171 tests passed; compiler-file Ruff and diff whitespace
  checks passed. Existing broad-file Ruff debt remains unrelated.

### 2026-09-11 — Milestone 2 architectural review
- Verified the core plan and compiler contain no provider/model-name branch.
  OpenAI-compatible and Anthropic adapters lower the same immutable plan;
  reviewed unsupported providers preserve correctness and report unsupported.
- Provider attempts now consume pending invalidations from the exact attempted
  plan even where provider-wire cache identity is unavailable. Stale-epoch
  attempts cannot clear newer invalidations.
- Focused provider/compiler validation was included in the Milestone 3 suite.
  Verdict: APPROVE.

### 2026-09-11 — Milestone 3 stable sections and cache epochs
- Reused Amni prompt assembly ownership instead of adding another prompt path.
  Ordered system sections are published only when independently formatted
  sections reproduce the exact folded content. Unknown augment sources default
  dynamic; stable sections retain owner-proved trust/scope/lifetime semantics.
- Final compilation overlays those semantics only on one exact leading occurrence
  match. The provider request remains unchanged while the stable prefix stops
  before memory/task/retrieval sections.
- Adaptive compaction persists bounded WorkingState and recent complete Tool
  groups before snapshot. One real history rewrite creates one cache epoch;
  recovery-only checkpoints preserve the current epoch, and later compacted
  turns append until another justified rewrite.
- Manual CLI `/compact` now creates the same provider-neutral lifecycle boundary
  and restores that checkpoint once on the next task. Amni snapshots preserve
  pending cache-break evidence and reject malformed lifecycle restoration.
- OpenAI-compatible and Anthropic sync/async/stream paths consume the same epoch
  evidence. No GLM/model/task-specific branch was introduced.
- Validation: 158 focused tests passed; `git diff --check` passed. Verdict:
  APPROVE.

### 2026-09-11 — Task 4.1 deterministic cache replay
- Added a provider-neutral replay evaluator that recomputes provider request
  hashes and usage receipts, verifies candidate-contract/CachePlan/lowering
  bindings, simulates exact stable-prefix reuse, and rejects stale/unexplained
  lifecycle breaks or epoch regressions.
- Added a redacted portfolio CLI for one or more `provider_calls.json` files;
  source paths, prompts, responses, endpoints, and credentials are not emitted.
- A six-stage arbitrary-provider trace (cold/repeat/suffix/prefix/compaction/
  append) achieved 100% trace match, 100% exact usage, and zero unexplained
  breaks. A real AWorld OpenAI-compatible runtime record sequence passed the
  same evaluator; Anthropic and unsupported-provider lowering remain covered by
  the shared provider suite.
- Validation: 81 replay/preflight/usage/provider tests passed; scoped Ruff and
  `git diff --check` passed.

### 2026-09-11 — Task 4.2 cache-only causal gate started
- Registered `context_cache` as a dedicated evaluation component and added a
  frozen `adaptive-cache-off` versus `adaptive-cache-on` plan. The plan changes
  exactly two generic policy paths and rejects model/provider fields.
- Both sides explicitly use the default-on Adaptive enforce runtime; instruction,
  model/provider, Tool surface, verifier, environment, seed, and step budget are
  invariant.
- The harness now derives cache totals through the shared usage receipt rather
  than provider-name aliases. Exact uncached input can prove execution efficiency
  only when every call in both paired trials has exact cache usage.
- Release reporting revalidates the ablation-plan hash plus an 8-observation
  stream/non-stream preflight before accepting cache economics.
- Live preflight: 8/8 exact, with cold/prefix-change at 0 cache reads and
  repeat/suffix-only change at 10,752 tokens for both call shapes.
- Validation: 128 focused control-plane/harness/report tests passed; scoped Ruff
  and `git diff --check` passed.

### 2026-09-11 — Capability-aware causal correction
- A resource-bounded, serial Terminal Bench pair on the randomly selected
  `cancel-async-tasks` case completed with checksum-valid provider calls, Raw
  trajectories and request traces. Candidate observed Reward 1 versus baseline
  0, but its 23 calls all recorded `preserved:exact_prefix_no_hint`; baseline's
  20 calls recorded `disabled:explicit_opt_out`.
- Because the provider-bound payload carried no AWorld native cache hint, the
  Reward difference is retained as a quality observation but cannot be
  attributed to cache control. The report now emits typed
  `cache_causal_evidence`, classifies this path `safety_only`, and hard-fails
  native benefit with `cache_causal_evidence_incomplete`.
- Partial exact-usage coverage no longer leaks a provisional
  `uncached_input_tokens_exact` into paired metrics. Execution output directories
  are immutable after a real run starts, preventing accidental evidence reuse.
- A formal optional `ModelConfig.max_tokens` now binds an explicit provider
  output cap to the compiler reserve. The compiler's default reserve is not
  silently sent as `max_tokens`, because doing so can truncate long-reasoning
  models and change task quality.
- The release contract is now layered without weakening gates: every provider
  proves safety/quality/capture; only explicitly applied and baseline-opted-out
  native controls may claim cache benefit. The existing GLM endpoint remains a
  conformance target, never a provider-specific implementation dependency.
- Validation: report/control-plane tests 56/56 passed; the wider focused
  provider/compiler/harness suite completed without an observed new failure.

### 2026-09-11 — Native hint negative evidence and fail-safe
- Added a second frozen plan with one shared generic routing namespace and only
  the same two cache-policy paths changed. Real provider receipts proved every
  Candidate request carried `prompt_cache_key` and every Baseline request was an
  explicit opt-out; the current endpoint accepts the field.
- `cancel-async-tasks` produced three complete Python-sidecar pairs, all Reward
  0→0. Two seeds reduced work, but one Candidate long tail made both the combined
  provider-call CI and conservative-cost CI cross zero.
- `db-wal-recovery` retained Reward 1→1 but Candidate calls increased 9→29 and
  wall time about 93→681 seconds. Native hint benefit is therefore not proven
  and is not eligible for current-endpoint default-on.
- Across all four complete pairs, Reward delta remained 0, while the Candidate
  provider-call 95% CI was `[+2.25, +15.25]` and conservative normalized-cost
  CI was `[+26.88B, +195.74B]` microunits. This is statistically directional
  regression for this endpoint, not merely missing positive evidence.
- Fixed the architectural cause: arbitrary OpenAI-compatible base URLs no longer
  inherit optional OpenAI native cache controls. `auto` enables the reviewed
  official endpoint; custom endpoints preserve the exact prefix and emit typed
  unsupported evidence unless deployment explicitly declares `supported` after
  conformance/canary. No provider/model name enters the Context Compiler.
- The benchmark harness exposes this provider capability as an invariant option,
  never a variant or task-specific field. The final focused
  Context/provider/harness/report suite is 265/265 green, with an additional
  23/23 cache usage/compiler/CLI tests green after the fail-safe.
- A real post-fix GLM request with a configured namespace completed successfully,
  recorded `unsupported:provider_capability_not_declared`, omitted
  `prompt_cache_key`, and retained request-trace match. This establishes the
  intended safe default on the currently configured custom endpoint.
- All benchmark containers were removed by the harness and the dedicated
  `cache-eval` Colima profile was stopped after validation.

### 2026-09-12 — Final compatibility and cross-provider review
- Fixed legacy `ConfigDict` compatibility: missing newly introduced
  `context_cache`, `max_tokens`, and response-parser keys no longer raise
  `KeyError`. Explicit output limits now have a regression test across sync,
  async, stream, and async-stream model entry points.
- Moved native cache capability resolution into the common provider contract.
  Both OpenAI and Anthropic adapters now distinguish reviewed official endpoints
  from arbitrary compatible base URLs; custom endpoints preserve request
  correctness and emit `unsupported:provider_capability_not_declared` unless a
  deployment explicitly qualifies them.
- Final focused regression: 313/313 passed. Scoped Ruff and `git diff --check`
  passed. No task prompt, Tool, environment, verifier, or benchmark answer was
  changed.
- Final review verdict: APPROVE for the provider-neutral safe default layer.
  Provider-native cache controls remain independently gated and the current GLM
  endpoint remains rejected by real causal regression evidence.
- Revalidated the preceding default-on release rather than discarding it: the
  frozen 12-pair report spans `terminal_bench`, `skills_bench`, and
  `tool_research`, has Reward CI `[0.0, 0.25]`, provider-call CI entirely below
  zero, complete rollback/canary bindings, and machine status `ready` with no
  gate failures. That evidence releases the generic Adaptive/Context layer; the
  four-pair native-hint regression scopes only to the optional control on the
  configured endpoint.
