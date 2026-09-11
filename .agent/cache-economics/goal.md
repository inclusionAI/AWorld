# Project Goal

## Problem Statement

AWorld can already identify stable Context prefixes and capture provider usage, but
the cache path is not yet a closed, provider-neutral optimization loop. In
particular, cache hints are not part of the immutable enforce candidate, Amni may
fold stable and dynamic system content together, compaction does not expose a
first-class cache epoch, and historical GLM usage is not uniformly exact.

## Desired Outcome

Deliver a production-quality cache-economics upgrade that preserves Adaptive
Context semantics while reducing uncached prefill work per successful task. The
implementation must use provider-native cache data when it is trustworthy,
remain correct for providers without cache support, and produce auditable
evidence sufficient for the proposed release gates.

## Acceptance Criteria

- [ ] Every successful model call can emit a structured cache receipt with raw
      and normalized usage fidelity, stable/provider-wire identity, cache epoch,
      and explained break reasons.
- [ ] A provider-capability-driven cache preflight validates cold,
      repeated-prefix, suffix-change, prefix-change, streaming, and
      non-streaming behavior without exposing prompts, responses, endpoints, or
      credentials. GLM is the first live conformance target, not a core
      dependency or unique source of truth.
- [ ] Cache hints are frozen in the immutable candidate and consumed only by
      provider adapters; no provider performs a post-final-compile prompt
      transform.
- [ ] Stable system/tool/skill material is separated from turn-dynamic material
      without weakening trust, scope, or instruction semantics.
- [ ] Adaptive and CLI compaction establish an auditable cache epoch and retain
      an immutable checkpoint plus recent complete assistant/tool atomic groups.
- [ ] Deterministic cache/replay tests have 100% request trace match, zero
      unexplained cache breaks, and 100% exact usage on valid cache probes.
- [ ] Frozen paired evaluation across at least two workload kinds has at least
      ten complete pairs, reward/complete-rate 95% CI lower bound no worse than
      -0.01, and uncached-input or cache-adjusted-cost-per-success CI upper bound
      below zero.
- [ ] No benchmark task instruction, environment, verifier, or task-specific
      prompt/tool is modified to obtain a pass.

## Non-Goals

- Server-side opaque compaction or provider-managed conversation state.
- Binding AWorld core Context models to GLM, OpenAI, Anthropic, or another vendor.
- Maximizing `cached_tokens / prompt_tokens` when that retains irrelevant Context
  or increases total cost.
- Changing benchmark prompts, answers, environments, or verifiers.
- Treating missing cache usage as zero or claiming exact provider billing without
  provider request/billing evidence.

## Constraints

- Adaptive Context remains default-on and retains an explicit rollback path.
- Provider-specific cache controls live only in provider adapters.
- Final candidate snapshots are immutable and provider-bound capture remains
  append-only and checksum verifiable.
- Local benchmark concurrency is one by default and never exceeds two.
- Existing unrelated untracked workspace files must not be modified or committed.

## Tech Stack

- Python 3.12, Pydantic, asyncio
- AWorld Context Compiler, Amni Context, aworld-cli
- Provider-neutral cache contracts with GLM-5.2 as one live conformance target
- Pytest and existing Context benchmark/report tooling
