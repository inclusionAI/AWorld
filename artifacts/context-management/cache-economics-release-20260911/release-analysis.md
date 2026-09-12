# Provider-neutral cache economics release analysis

Date: 2026-09-11

## Decision

- **Provider-neutral stable-prefix, cache-epoch and usage-observability layer:**
  safe to remain default-on behind existing immutable provider lowering.
- **Native `prompt_cache_key` on the currently configured custom
  OpenAI-compatible endpoint:** not eligible for default-on.
- **Other native provider controls:** capability-scoped; each provider/entry
  point requires its own conformance, paired benefit, rollback and canary
  evidence before a benefit claim.

This is not a GLM-specific implementation decision. Wire compatibility proves
neither OpenAI nor Anthropic optional cache controls. The runtime resolves one
common provider capability declaration: official reviewed endpoints may use
`auto`; custom base URLs default to exact-prefix preservation without a native
hint and must explicitly declare `supported` after validation. OpenAI and
Anthropic adapters both consume that contract; other providers can adopt it
without adding vendor/model branches to the Context Compiler.

## Evidence

### Provider-neutral Adaptive default-on baseline

The preceding frozen release report remains authoritative for the generic
Adaptive/Context layer and must not be replaced by the narrower native-control
ablation. `../default-on-evidence-20260908/combined-adaptive-default-on-v13-release.json`
contains 12 complete
pairs over Terminal Bench, SkillsBench, and Tool Research. Its Reward delta CI
is `[0.0, 0.25]`, its provider-call delta CI is entirely below zero, and its
rollback/canary-bound machine decision is `ready` with no gate failures.

That report establishes the meaningful system-level benefit and releases the
generic layer. The native-hint evidence below answers a different question:
whether one optional provider control should also default on for the currently
configured endpoint.

### Usage conformance

The provider-neutral stream/non-stream preflight produced 8/8 exact usage
observations. Cold and prefix-change requests reported zero cache reads; repeat
and suffix-only changes reported 10,752 cached tokens. This proves usage truth
and automatic prefix caching, not AWorld native-control benefit.

### No-hint safety

The original cache-only pair used `exact_prefix_no_hint`. Its observed Terminal
Reward difference is retained, but the report correctly marks native-cache
attribution unavailable. Three local Tool-workload pairs retained Reward 1/1;
partial usage coverage is no longer exposed as exact uncached-input evidence.

### Explicit native-control ablation

A frozen v2 plan gave Baseline and Candidate the same generic routing namespace;
only `context_cache.enabled` and
`context_cache.allow_provider_native_cache` changed. Provider receipts prove:

- Baseline: `disabled:explicit_opt_out` on every call.
- Candidate: `applied:prompt_cache_key` on every call.
- Provider request trace and finalized Raw trajectory remained complete.

Four complete Terminal pairs produced no Reward regression (all deltas zero),
but did produce a significant resource regression:

- provider-call delta 95% CI: `[+2.25, +15.25]` calls;
- conservative normalized-cost delta 95% CI:
  `[+26.88B, +195.74B]` microunits.

The `db-wal-recovery` pair retained Reward 1/1 while Candidate calls increased
from 9 to 29 and wall time from about 93 to 681 seconds. Therefore the native
hint is rejected for this endpoint rather than being promoted based on isolated
positive runs.

Machine report:
`terminal-native-cache-4pair-report.json`.

### Post-fix real-provider check

A real request against the configured custom endpoint, with a cache namespace
present and capability left at `auto`, completed successfully and recorded:

- lowering status: `unsupported`;
- strategy: `provider_capability_not_declared`;
- `prompt_cache_key` present: false;
- request trace match: true.

This verifies that default-on Context cache management remains active without
silently sending an unqualified provider-native control.

The final compatibility review also verified legacy `ConfigDict` construction
when the new cache/output fields are absent and explicit `max_tokens` propagation
through sync, async, stream and async-stream calls. The final focused regression
is 313/313 passing, with scoped Ruff and whitespace validation clean.

Redacted diagnostic receipt: `post-fix-provider-conformance.json`. It is not
used as Reward or release-benefit evidence.

## Release gate interpretation

The report keeps observed paired outcome, causal lowering evidence, and release
benefit separate. A no-hint provider can prove safety and quality non-regression,
but cannot claim AWorld-controlled native-cache benefit. A native-control claim
requires applied Candidate lowering, explicit Baseline opt-out, exact or
conservative provider usage truth, quality non-regression, at least ten complete
pairs across two workload kinds, complete capture, rollback, and production
canary evidence.

The code-level safe default is complete. A specific provider's native control
remains `NOT_READY` until its provider-scoped evidence meets those gates; this
does not block safe stable-prefix/epoch/observability behavior on other models.
