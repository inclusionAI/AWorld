# Project Plan

## Architecture Overview

The Context Compiler remains provider-neutral. It produces an immutable request
plus a provider-neutral `CachePlan`. Provider adapters lower that frozen plan to
optional native controls and return provider-bound identity evidence. Model
responses produce `CacheUsageReceipt` records that distinguish exact, missing,
conflicting, bounded, and unavailable cache data. Compaction creates a new
cache epoch with an immutable checkpoint; later turns append to that epoch.

## Milestones

### Milestone 1: Cache truth and provider conformance preflight

**Goal:** Make cache measurements trustworthy before changing prompts.
**Depends on:** None

#### Task 1.1: Cache usage fidelity and receipts
- **Parallel:** no
- **Files:** `aworld/models/usage.py`, Context call-record models/helpers,
  trajectory/report adapters, tests
- **Approach:** Add strict non-coercing validation beside compatibility
  normalization. Record exact/missing/conflicting/unavailable status per call and
  preserve raw provider fields.
- **Tests:** aliases, explicit zero, missing detail, conflicting views, invalid
  values, cached greater than prompt, streaming terminal usage.
- **Acceptance criteria:** Valid GLM usage is exact; invalid/missing usage never
  becomes a false zero; receipts round-trip through call journal/trajectory.
- **Status:** complete

#### Task 1.2: Provider-capability cache preflight
- **Parallel:** no
- **Files:** `examples/sandbox/model_preflight.py` or a dedicated adjacent cache
  preflight module, harness integration, tests
- **Approach:** Add a redacted capability-driven cache probe covering
  cold/repeat/suffix change/prefix change in streaming and non-streaming modes.
  Providers declare supported probes and usage aliases. Emit only hashes, usage,
  latency, and typed decisions. Exercise GLM first, while keeping the runner and
  receipt schema provider-neutral.
- **Tests:** fake provider state machine plus parser/gate behavior.
- **Acceptance criteria:** Benchmark cache conclusions are blocked unless exact
  coverage is 100% for the selected cache-capable provider; ordinary quality
  preflight and providers without native cache remain compatible.
- **Status:** complete

#### Task 1.3: CLI/report observability
- **Parallel:** no
- **Files:** `aworld-cli/src/aworld_cli/memory/cache_observability.py`, evaluation
  report helpers, tests
- **Approach:** Report exact coverage, uncached input, cache epochs, explained
  breaks, and confidence separately from heuristic prefix candidates.
- **Tests:** mixed exact/missing/conflicting session logs.
- **Acceptance criteria:** CLI and reports cannot describe incomplete usage as an
  exact hit rate.
- **Status:** complete

### Milestone 2: Immutable CachePlan lowering

**Goal:** Carry cache intent through final compile without post-compile mutation.
**Depends on:** Milestone 1

#### Task 2.1: Provider-neutral CachePlan
- **Parallel:** no
- **Files:** Context compiler models/final/rollout, provider envelope, tests
- **Approach:** Freeze cache identity inputs, epoch, stable prefix evidence, and
  optional routing namespace in the candidate snapshot/envelope.
- **Tests:** deterministic serialization, immutable round-trip, break reasons,
  unknown-provider behavior.
- **Acceptance criteria:** CachePlan is covered by candidate hash and attribution.
- **Status:** complete

#### Task 2.2: Provider adapter lowering
- **Parallel:** no
- **Files:** OpenAI/Anthropic providers and prompt cache adapters, tests
- **Approach:** Consume CachePlan exactly once; retain explicit caller overrides;
  never replay legacy prompt assembly in enforce mode.
- **Tests:** sync/async/stream, HTTP/SDK, explicit opt-out, unsupported provider,
  request trace fidelity.
- **Acceptance criteria:** Native hints reach supported providers and the sent
  candidate remains byte/semantic equivalent to the frozen snapshot.
- **Status:** complete

### Milestone 3: Stable sections and cache epochs

**Goal:** Increase reusable prefix without retaining irrelevant Context.
**Depends on:** Milestone 2

#### Task 3.1: Stable/dynamic Amni system sections
- **Parallel:** no
- **Files:** Amni prompt assembly/adapters, final Context adapter/compiler, tests
- **Approach:** Preserve section ownership and trust before folding; place all
  dynamic or user-controlled content after the stable boundary.
- **Tests:** AWORLD.md, skills, memory, retrieval, time/task variables, injection,
  provider parity.
- **Acceptance criteria:** Stable content stays byte-identical across turns while
  dynamic content never enters the stable prefix.
- **Status:** pending

#### Task 3.2: Compaction CacheEpoch and checkpoint
- **Parallel:** no
- **Files:** adaptive policy/runtime, lifecycle models, Amni WorkingState, CLI
  compact path, tests
- **Approach:** One compaction creates one new epoch and immutable checkpoint;
  subsequent turns append history until the next justified compaction.
- **Tests:** repeated compaction, resume/reset, recent tool atomic group, fail-safe
  preservation, CLI/adaptive parity.
- **Acceptance criteria:** One explained cold rebuild per compaction and stable
  reuse afterward.
- **Status:** pending

### Milestone 4: Verification and release evidence

**Goal:** Prove cache economics improve without quality regression.
**Depends on:** Milestones 1-3

#### Task 4.1: Deterministic replay suite
- **Parallel:** no
- **Files:** cache replay fixtures/runner/tests
- **Approach:** Replay frozen provider requests through exact-prefix simulation and
  validate all break identities.
- **Acceptance criteria:** 100% trace match, zero unexplained breaks.
- **Status:** pending

#### Task 4.2: Frozen real-provider paired evaluation
- **Parallel:** no
- **Files:** evaluation manifests/results only
- **Approach:** Freeze current-main Adaptive baseline versus one-component cache
  candidates, at concurrency one, across at least two workload kinds and three
  seeds per case. Use GLM for the first live matrix and require the same generic
  receipt contract for any additional cache-capable provider.
- **Acceptance criteria:** At least ten complete exact-usage pairs and all goal
  acceptance gates satisfied.
- **Status:** pending

#### Task 4.3: Final review and release decision
- **Parallel:** no
- **Files:** release report, spec, rollback/canary receipt as applicable
- **Approach:** Run full relevant suites, inspect complete diff, and publish a
  machine-verifiable ready/not-ready decision without weakening gates.
- **Acceptance criteria:** No unresolved important review issue and evidence-backed
  release decision.
- **Status:** pending
