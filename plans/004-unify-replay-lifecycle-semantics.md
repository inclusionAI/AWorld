# Plan 004: Unify replay lifecycle and failure ownership semantics

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan in
> `plans/README.md` unless a reviewer told you they maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat 70cb5c9a..HEAD -- aworld/self_evolve/replay.py aworld/self_evolve/runner.py aworld/self_evolve/types.py tests/self_evolve/test_replay_overlay.py tests/self_evolve/test_runner.py tests/self_evolve/test_framework_contract_matrix.py docs/AWorld\ CLI/Commands/Optimize.md`
> If replay result fields or population-stop behavior changed, stop and report
> before implementing this plan.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: MED
- **Depends on**: `plans/002-establish-self-evolve-contract-matrix.md`
- **Category**: bug
- **Planned at**: commit `70cb5c9a`, 2026-07-21

## Why this matters

The current replay model conflates three different facts: whether a variant
executed, who owns a failure, and whether a baseline/candidate pair is
comparable. As a result, candidate-owned capability failure during the baseline
slot is described as infrastructure failure, an unexecuted candidate is counted
as failed, and the runner aborts the remaining candidate population. A single
cardinality-neutral lifecycle model must drive one-case and multi-member replay,
gate calculation, reporting, and population continuation.

## Current state

- `aworld/self_evolve/replay.py:126-165` stores variant `status` as an arbitrary
  string and failure as an untyped mapping. `succeeded` is the only semantic
  helper.
- `aworld/self_evolve/replay.py:318-381` has separate branches for root-level
  single-case results and `member_results`, and increments
  `candidate_failure_count` whenever `candidate.succeeded` is false, even when
  candidate execution never started.
- `aworld/self_evolve/replay.py:424-447` blocks candidate execution for both
  infrastructure and candidate-owned capability failures, but always creates a
  `status="failed"` candidate with the detail "baseline infrastructure replay
  failed".
- `aworld/self_evolve/runner.py:5481-5521` can later infer that a failure is
  candidate-owned and repairable, producing report fields that contradict the
  replay status text.
- `aworld/self_evolve/runner.py:5800-5812` stops the whole candidate population
  by matching only the string `baseline_preflight_failed`, without examining
  owner or scope.
- `tests/self_evolve/test_replay_overlay.py:3420-3476` and
  `tests/self_evolve/test_runner.py:4002-4095` currently assert the misleading
  infrastructure wording and population-wide stop behavior.

The target model is a normalized sequence of replay members. A one-case run is
one member; a multi-case run is N members. The framework may keep legacy
top-level fields for serialized compatibility, but all logic must consume the
normalized member iterator.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Replay units | `python -m pytest tests/self_evolve/test_replay_overlay.py -k "baseline_preflight or multi_member or failed_case or pair_coverage" -q` | all selected tests pass |
| Runner population | `python -m pytest tests/self_evolve/test_runner.py -k "baseline_preflight or candidate_population or replay_confidence" -q` | all selected tests pass |
| Contract matrix | `python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q` | all pass for one and three cases |
| Full subsystem | `python -m pytest tests/self_evolve -q` | all pass on a supported host |

## Scope

**In scope**

- `aworld/self_evolve/replay.py`
- `aworld/self_evolve/runner.py`
- `aworld/self_evolve/types.py` only if shared enums belong there
- A new `aworld/self_evolve/failure_events.py` if a dedicated typed failure
  value is cleaner than placing it in `replay.py`
- `tests/self_evolve/test_replay_overlay.py`
- `tests/self_evolve/test_runner.py`
- `tests/self_evolve/test_framework_contract_matrix.py`
- `docs/AWorld CLI/Commands/Optimize.md`

**Out of scope**

- Changing evaluator scores or held-out acceptance thresholds.
- Special handling keyed by case ID, target ID, protocol name, or fixture text.
- Retrying deterministic candidate capability failures.
- Changing candidate generation budgets; plan 007 owns scheduling and budget.
- Reclassifying ordinary task-quality failures as infrastructure failures.

## Git workflow

- Branch: `codex/004-replay-lifecycle-semantics`
- Suggested commit: `fix(self-evolve): model replay execution and failure ownership`
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Define typed, orthogonal replay semantics

Introduce bounded enums or validated literals for these dimensions:

- execution status: `succeeded`, `failed`, `blocked`, `not_run`;
- failure owner: `candidate`, `task`, `infrastructure`, `framework`;
- failure stage: at minimum `adaptation`, `capability_compile`,
  `capability_preflight`, `task_rollout`, `evaluation`;
- failure scope: `variant`, `member`, `candidate`, `shared_run`;
- repairability and a stable, sanitized failure code.

Use a typed `FailureEvent`/`ReplayFailure` value with serialization helpers.
Preserve bounded diagnostics and artifact references, but do not store raw
payload content. Provide a legacy mapping parser so replay artifacts written
before this change can still be inspected. New writes must use the typed shape.

Semantics that must be enforced:

- `failed` means execution started and failed.
- `blocked` means execution did not start because another event prevented it.
- `not_run` means the framework intentionally did not schedule the work.
- failure owner is independent of whether the event occurred while preparing
  the baseline or candidate slot.

**Verify**:
add unit tests for serialization, legacy mapping conversion, and impossible
combinations; run
`python -m pytest tests/self_evolve/test_replay_overlay.py -k "failure_event or status" -q`
→ all selected tests pass.

### Step 2: Normalize single and multi-case results before aggregation

Add one helper such as `iter_replay_members(dataset, replay_result)` that always
returns member-shaped `(case, request, baseline, candidate)` records:

- root-level single-case results normalize to exactly one member;
- explicit `member_results` normalize to all members in deterministic dataset
  order;
- missing or duplicate member IDs produce a structured framework event rather
  than silent count arithmetic.

Refactor `candidate_replay_is_comparable`, provenance comparison, pair coverage,
gate detail construction, and failure collection to use this helper. Remove
cardinality-dependent semantic branches from those consumers.

**Verify**:
`python -m pytest tests/self_evolve/test_framework_contract_matrix.py tests/self_evolve/test_replay_overlay.py -k "single_case or multi_member or pair_coverage" -q`
→ one- and three-case expectations pass.

### Step 3: Correct preflight blocking and pair accounting

When a candidate-owned replay capability fails while starting the baseline:

- record a candidate-owned `capability_preflight` failure event;
- mark the candidate variant `blocked`, not `failed`;
- mark later members blocked by the same event when repeating the identical
  candidate capability would be pointless;
- count one candidate capability failure and zero candidate execution failures;
- do not describe the event as baseline infrastructure failure.

When actual shared infrastructure fails:

- owner must be `infrastructure` or `framework` with `scope=shared_run` before
  it can block the remaining candidate population;
- candidate variants and later members are `blocked` with `blocked_by` pointing
  to the shared event.

Keep separate report counters for at least:

- comparable pairs;
- executed baseline failures by owner;
- executed candidate failures by owner;
- blocked variants/members;
- missing members.

If compatibility requires retaining `candidate_failure_count`, document its new
precise meaning and add the more explicit counters alongside it.

**Verify**:
`python -m pytest tests/self_evolve/test_replay_overlay.py -k "baseline_preflight or pair_coverage" -q`
→ candidate-owned and shared-infrastructure fixtures have distinct outcomes.

### Step 4: Continue or stop populations by event scope, not a reason string

Replace `_baseline_preflight_blocks_population` string matching with a policy
that consumes typed events:

- candidate- or variant-scoped failures reject only the current candidate and
  continue to the next candidate;
- member-scoped task failures follow comparison policy but never terminate the
  whole population;
- only explicit `shared_run` infrastructure/framework events terminate the
  population;
- an unknown legacy event fails closed for verified apply but must not be
  promoted to shared infrastructure without evidence.

Update replay-confidence behavior: when candidate replay is blocked before any
candidate execution, do not emit a second misleading statistical-confidence
failure. Either omit that gate as not applicable or emit an additive
`status="not_run"` detail while the causal capability gate remains blocking.

**Verify**:
`python -m pytest tests/self_evolve/test_runner.py -k "baseline_preflight or population" -q`
→ a candidate-owned preflight failure continues to candidate two; a shared-run
infrastructure failure stops before candidate two.

### Step 5: Add the cardinality and mixed-outcome matrix

Extend `test_framework_contract_matrix.py` for both `case_count=1` and
`case_count=3`:

1. all members succeed;
2. candidate capability preflight fails before rollout;
3. candidate task rollout executes and fails for one member;
4. baseline task failure is comparable under current policy;
5. shared infrastructure blocks the run;
6. one member is missing from the backend result.

Assert identical owner/status/scope semantics for the equivalent single and
multi-case event. Multi-case tests must also assert stable member ordering and
bounded blocked-by propagation.

**Verify**:
`python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q`
→ all matrix cells pass.

### Step 6: Preserve artifact compatibility and update docs

Ensure replay artifact readers accept legacy status/failure mappings. New
reports should expose structured lifecycle fields while retaining old fields
only where required for compatibility. Update Optimize documentation with the
meaning of failed, blocked, not-run, owner, stage, and scope, including one
multi-member example that does not mention a specific protocol or target.

**Verify**:
run existing replay artifact round-trip tests in
`tests/self_evolve/test_replay_overlay.py` → all pass.

## Test plan

- Model new tests after the fake executors in
  `tests/self_evolve/test_replay_overlay.py:2769-3004`.
- Replace assertions that hard-code "baseline infrastructure replay failed"
  for candidate-owned failures.
- Replace the runner test that always stops after `baseline_preflight_failed`
  with two owner/scope cases.
- Parameterize all new lifecycle contracts over one and three cases.
- Add legacy artifact deserialization coverage.
- Assert candidate-owned failure in member 1 may block the remaining members of
  that candidate, but never blocks candidate 2.

## Done criteria

- [ ] Execution status, failure owner, stage, and scope are represented
  independently.
- [ ] An unexecuted candidate is never reported as an executed failure.
- [ ] Candidate-owned capability failure is never labeled infrastructure.
- [ ] Population stop occurs only for explicit shared-run framework or
  infrastructure events.
- [ ] All replay aggregation uses one normalized member iterator.
- [ ] Equivalent one- and multi-case events have the same semantics.
- [ ] Replay confidence is not reported as a statistical failure when replay
  never ran.
- [ ] Legacy replay artifacts remain readable.
- [ ] Focused and full subsystem tests pass.
- [ ] No files outside Scope and the plan index are modified.

## STOP conditions

Stop and report if:

- A backend cannot distinguish work that started from work that was blocked and
  there is no safe compatibility inference.
- Existing persisted artifacts require a destructive schema migration.
- The change would treat unknown legacy failures as trusted comparable results.
- Correct owner/scope propagation requires embedding raw replay payloads.
- Multi-member ordering cannot be made deterministic from dataset case IDs.

## Maintenance notes

New replay stages must extend the typed enums and normalized member aggregation;
they must not add another cardinality-specific branch. Reviewers should verify
that failure ownership is determined by the component that failed, not by the
baseline/candidate slot in which the failure surfaced.
