# Project Progress

## Current Status

**Phase:** Milestone 2
**Current milestone:** Immutable CachePlan lowering
**Current task:** Milestone 2 implementation complete; architectural review pending
**Last action:** Wired lifecycle/policy inputs into an immutable CachePlan and
verified provider-capability lowering with 171 focused tests.

## Completed Milestones

- Milestone 1: Cache truth and provider conformance preflight.

## Current Milestone: Immutable CachePlan lowering

### Task Status

| Task | Status | Notes |
|---|---|---|
| 2.1 Provider-neutral CachePlan | complete | Frozen by final compiler, candidate-contract bound, lifecycle/policy inputs carried, redacted inspector output |
| 2.2 Provider adapter lowering | complete | OpenAI-compatible exact prefix/optional key, Anthropic cache-control, unsupported-provider evidence; explicit overrides preserved |

### Review Feedback

Milestone 2 architectural review is pending.

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
- Amni folded system content may collapse stable and dynamic sections.
- Historical GLM release evidence has 451/468 exact calls; seven calls have
  cache-specific missing/conflicting data and ten have incomplete attempt truth.

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
