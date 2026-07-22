# Self-Evolve Framework Repair Progress

## Current status

**Phase:** Milestone 4 complete
**Current milestone:** Stage-aware budget ledger and candidate scheduler finalized
**Current task:** Plan 007 complete; no remaining framework-repair task
**Last action:** Plan 007 passed three architecture review cycles and 982/982 self-evolve tests at `517e0cd0`.

## Task status

| Plan | Status | Notes |
|---|---|---|
| 002 contract matrix/CI | complete | Commit `89e1814f`; 7 contract tests, platform-aware CI split, 26 sandbox tests marked. |
| 003 provenance/trust | complete | Finalized at `9b554d3f`; typed total resolution, canonical locality, strict sidecars, authoritative decisions, and adversarial API invariants. |
| 004 replay lifecycle | complete | Finalized at `83f5e0c0`; typed lifecycle, normalized one/N members, v2 artifacts, and typed population policy. |
| 005 conformance | complete | Group-specific shape-complete validation, private/public contract separation, full structural fingerprints, and cardinality-neutral affected-member semantics. |
| 006 causal memory | complete | Exact typed provenance, cross-emission union, fail-closed lesson aggregation, and prompt-safe causal memory. |
| 007 budget scheduler | complete | Run ledger v3, typed attempt lifecycle, cardinality-aware scheduling, per-member replay semantics, atomic persistence, and explicit stored-evidence reuse finalized at `517e0cd0`. |

## Decisions log

### Decision: Preserve prior agent state

- Chose: use `.agent/framework-repair/` instead of replacing existing `.agent/*.md`.
- Rationale: the existing state documents a completed earlier acceptance effort and remains useful history.

### Decision: Treat trajectory cardinality as data

- Chose: normalized member aggregation plus parameterized 1/3-case tests.
- Rationale: avoids single-case and multi-case control-flow drift.

### Decision: Use one typed causal event model for plans 004 and 006

- Chose: add `failure_events.py` with execution status, owner, stage, scope, code, repairability, legacy source, and causal references.
- Rationale: replay policy and downstream diagnostics must not derive ownership from slot names or free-form text.
- Trade-off: existing mapping-shaped artifacts require an additive legacy parser and serializer compatibility layer.

### Decision: Normalize one and multiple cases through an explicit member view

- Chose: new replay writes explicit members even for one case; legacy root artifacts remain readable through a sentinel/normalization boundary.
- Rationale: an empty tuple currently means both legacy single-case and missing members, which prevents exact accounting.
- Trade-off: replay loaders and several tests must be migrated together in plan 004.

### Decision: Treat persisted provenance as audit data, not authorization input

- Chose: recompute authorization from the current workspace inventory and typed selection origin; a sidecar or supplied decision may only agree with or downgrade that result.
- Rationale: malformed, legacy, duplicated, symlinked, or caller-constructed metadata must never elevate mutation authority.
- Review outcome: three independent review/fix cycles closed unknown enum combinations, workspace escapes, resolution state/data mismatches, zero-trace explicit reruns, and malformed public API values.

### Decision: Make normalized replay members the only authoritative behavioral view

- Chose: new backends emit explicit member tuples for one and many cases; root fields remain compatibility aggregates and `None` is reserved for legacy single artifacts.
- Rationale: execution status, comparability, coverage, reuse, reporting, and paired-dataset generation must agree on the same dataset-ordered members.
- Review outcome: three review cycles closed structural anomaly bypasses, non-native shared-run escalation, repetition loss on artifact reload, contradictory unexecuted states, empty-member baseline reuse, and member request identity tampering.

### Decision: Separate private execution contracts from every public persistence boundary

- Chose: keep exact repair assertions only in ephemeral `OptimizerResult.private_context`; prompts, gates, diagnostics, lessons, and reports receive typed public projections containing fingerprints and shapes.
- Rationale: execution fidelity must not require persisting raw fixture-derived values, and persistence sanitization must not mutate the executable contract.
- Review outcome: three review cycles closed group projection gaps, deep-container bypasses, protocol exception leaks, raw identity propagation, and lesson-to-prompt leakage.

### Decision: Preserve exact causal cardinality with complete identity digests

- Chose: aggregate v2 stores complete affected-case and source-observation digest sets while retaining bounded raw-ID samples only for display.
- Rationale: exact counts across more than 64 trajectories and across iterations cannot be reconstructed from bounded samples.
- Review outcome: same-emission conflicts fail closed, different emissions union case/source digests, occurrence counts remain additive, and v1 typed payloads are verified before migration.

### Decision: Separate conservative spend from estimator observations

- Chose: debit incomplete telemetry with `max(reservation, known lower bound)` per dimension, while only complete actual dimensions enter rolling estimates.
- Rationale: missing telemetry must never reduce known spend or acquire false observed confidence; complete token data remains learnable even when cost or wall data is missing.
- Review outcome: partial batches preserve lower bounds, fallback samples do not raise confidence, explicit zero proofs are stage-scoped, and the v3 hash-chained journal derives all summaries.

### Decision: Define replay repetitions per normalized member

- Chose: baseline and candidate repetition settings apply to every normalized replay member; N members execute `N*B` and `N*C`, with proven baseline reuse removing only the baseline portion.
- Rationale: distributing a total repetition count with `ceil(K/N)` made execution non-monotonic as trajectories were added and disagreed with reservations.
- Review outcome: per-member v3 artifacts carry explicit semantics; v1/v2 artifacts migrate for inspection but cannot authorize new execution, evaluation, or reuse.

### Decision: Model stored evaluator reruns as evidence reuse

- Chose: a typed candidate-source disposition permits the fixed stored candidate to bypass only historical canonical dedup, and a separate replay-evidence disposition records reuse without execution callbacks or replay budget activity.
- Rationale: evaluator reruns must execute fresh evaluation while accurately reporting that paired replay evidence came from a prior run.
- Review outcome: fresh evaluator success/failure controls selection, `replay_evidence_reused` is distinct from started/completed/comparable, and strict v3 artifact authority is required.

## Architecture state

- Replay results use explicit normalized members for one and multiple cases; legacy root artifacts are read-only migrations.
- Conformance is shape-complete and separate from optional representative screening.
- A run-wide v3 ledger reserves, conservatively debits, releases, journals, and learns per-stage token/cost/wall usage.
- CI now executes platform-neutral self-evolve tests on Ubuntu and native sandbox tests on macOS.
- `test_framework_contract_matrix.py` provides generic one/three-case datasets with repeated/distinct requirement shapes.
- Target selection now returns a typed decision with one target-level provenance resolution; verified candidates always receive a trust gate.
- Provenance resolution enforces `resolved` if and only if typed provenance is present; unresolved reasons and malformed values fail closed at both model and gate boundaries.
- Explicit target selection is persisted even with no trace evidence, so direct runner and CLI rerun paths share one authorization contract.
- Replay failures now carry typed owner/stage/scope/source and stable causal occurrence IDs; only native shared-run infrastructure/framework events may stop the population.
- Replay lifecycle v2 preserves blocked/not-run states and repetition children; legacy artifacts are converted only at the read boundary.
- Normalization derives every member request from root request plus `EvalCase`, rejects missing/duplicate/unexpected/mismatched members, and keeps coverage partitioned by expected dataset cardinality.
- Candidate generation uses typed semantic frontiers for bounded exploration, focused repair, and optional diversity rather than failure strings.
- Every generation slot has an append-only lifecycle; canonical candidates remain deduplicated while attempt provenance is retained separately.
- Attempt streams are committed through write-all, fsync, atomic replace, and directory fsync so partial/short writes cannot corrupt the canonical lifecycle.
- Authoritative replay artifacts use `per_member_v3`; request, manifest, lifecycle, physical repetition children, and aggregate counts must agree before reuse or evaluation.
- Stored evaluator reruns execute fresh evaluation while reporting prior replay evidence through an explicit reuse disposition with no new replay debit.

## Known issues

- Root worktree contains unrelated tracked deletions and many untracked files; isolated worktrees are required for delegated implementation.

## Verification log

- Baseline: `conda run --no-capture-output -n aworld_env python -m pytest tests/self_evolve -q` → 704 passed in 30.21s.
- Plan 002 worktree: 711 full tests passed; root merge commit `89e1814f` created after `git show --check` and contract-matrix verification.
- Merged plans 002+003: `conda run --no-capture-output -n aworld_env python -m pytest tests/self_evolve -q` → 728 passed in 30.13s.
- Milestone 1 review fix 1 (`96e220b1`): strict provenance allowlist, canonical no-symlink locality, duplicate identity rejection, typed selection origin, and sidecar audit-only semantics; 746 passed.
- Milestone 1 review fix 2 (`bd47d680`): resolution state/data invariants and zero-trace explicit CLI rerun persistence; 755 passed.
- Milestone 1 review fix 3 (`9b554d3f`): typed target validation, defensive trust-gate inputs, and direct runner selection persistence; `conda run --no-capture-output -n aworld_env python -m pytest tests/self_evolve -q` → 767 passed in 29.56s.
- Milestone 1 architectural review exhausted the planned three fix cycles. CI markers remained 26 native sandbox tests with all remaining tests platform-neutral; no named run/target/case branches were introduced.
- Plan 004 initial implementation (`d7b6d71a`): typed failure events, explicit one/N member lifecycle, v2 artifacts, typed population disposition, and expanded contract matrix.
- Plan 004 review fix 1 (`de59071d`): normalization validity, native-only shared-run authority, repetition reload, strict blocked/not-run artifacts, and empty-member reuse; 807 passed.
- Plan 004 review fix 2 (`83f5e0c0`): fully derived member request identity, required v2 source, order-independent duplicate/mismatch handling, and exact coverage partition; `conda run --no-capture-output -n aworld_env python -m pytest tests/self_evolve -q` → 818 passed in 31.44s.
- Plan 004 third architectural review: APPROVE; overlay + runner 299 passed, contract matrix 19 passed, no remaining P1/P2 lifecycle risk found.
- Plans 005–006 initial integration (`dc296aec`): group-specific conformance, private/public repair contracts, exact typed causal aggregation; 855 tests passed.
- Plans 005–006 review cycle 2 (`af6934b7`): source-level raw-free protocol failures, recursive public projection, complete fixture structural digest, aggregate v2 provenance union, and exact group observations; 866 tests passed plus 7 cross-matrix checks.
- Plans 005–006 review cycle 3 (`41ae5be2`): causal lessons retain only identity digests and legacy lesson metrics are public-projected before prompts; `python -m pytest tests/self_evolve -q` → 868 passed.
- Plan 007 initial integration (`53fa845d` through `be294d2a`): pure ledger, config/CLI plumbing, typed lifecycle, cardinality-aware reservations, and semantic candidate scheduler; 917 tests passed.
- Plan 007 review cycle 1 (`0a89f676` through `490146cd`): normalized observation units, terminal aggregate enforcement, shared-stop propagation, run cleanup, and fail-closed infrastructure handling; 948 tests passed.
- Plan 007 review cycle 2 (`2f081f19` through `c85c6dc0`): observed usage priority, explicit zero proof, batch-cursor telemetry, complete-only judge actuals, cost overrun reachability, and append reconciliation; 958 tests passed.
- Plan 007 review cycle 3 (`f192b51e` through `517e0cd0`): atomic lifecycle streams, per-dimension usage provenance, per-member replay v3, strict physical artifact authority, and explicit stored evaluator reruns; `python -m pytest -qq tests/self_evolve` → 982 passed in 36.90s.
- Final independent closure audit at `517e0cd0`: 24 focused cardinality, budget, artifact-tamper, stored-evidence, and evaluator success/failure checks passed; no residual P1/P2 findings.
