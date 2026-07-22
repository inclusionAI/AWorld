# Plan 007: Add a stage-aware budget ledger and candidate scheduler

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan in
> `plans/README.md` unless a reviewer told you they maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat 70cb5c9a..HEAD -- aworld/self_evolve/concurrency.py aworld/self_evolve/evaluation.py aworld/self_evolve/runner.py aworld/self_evolve/store.py aworld/self_evolve/optimizers/llm_mutator.py aworld/self_evolve/config.py tests/self_evolve/test_execution_telemetry.py tests/self_evolve/test_evaluation_backend.py tests/self_evolve/test_optimizer_contract.py tests/self_evolve/test_runner.py tests/self_evolve/test_framework_contract_matrix.py docs/AWorld\ CLI/Commands/Optimize.md`
> If a run-wide budget ledger or explicit candidate-stage record already exists,
> stop and reconcile rather than adding a second implementation.

## Status

- **Status**: DONE — finalized at `517e0cd0` after three architecture review cycles
- **Priority**: P1
- **Effort**: L
- **Risk**: MED
- **Depends on**: `plans/004-unify-replay-lifecycle-semantics.md`, `plans/005-make-conformance-cardinality-independent.md`, `plans/006-propagate-causal-failure-memory.md`
- **Category**: perf
- **Planned at**: commit `70cb5c9a`, 2026-07-21

## Why this matters

The runner records candidate-generation token usage after each batch, but does
not debit it from a run-wide budget before scheduling the next batch. Replay
cost estimation defaults to zero tokens per replay, and population reporting
labels every iteration report as replayed even when a candidate stopped at
adaptation or conformance. A stage-aware ledger and scheduler must reserve cost
according to trajectory cardinality, learn from observed usage, and switch from
exploration to focused repair based on semantic failure progress—not based on a
specific run, case, or error string.

## Current state

- `aworld/self_evolve/runner.py:729-775` can generate up to the configured
  iterations plus six repair extensions, creating a full candidate batch before
  validation feedback from that batch exists.
- `aworld/self_evolve/optimizers/llm_mutator.py:83-95` generates all population
  slots from the same pre-batch context.
- `aworld/self_evolve/runner.py:775-783` records actual candidate-generation
  telemetry only after generation completes.
- `aworld/self_evolve/evaluation.py:814-884` estimates replay tokens with
  `estimated_tokens_per_replay=0` by default.
- `aworld/self_evolve/runner.py:1833-1844` calls the estimator without a non-zero
  estimate, so the token budget gate cannot reject replay on token estimates.
- `aworld/self_evolve/concurrency.py:60-154` already aggregates bounded stage
  telemetry, including generation token usage, and should be reused rather than
  replaced.
- `aworld/self_evolve/runner.py:812-820` persists candidates and lineage before
  cross-iteration duplicate filtering; `store.py:134-139` writes lineage by
  candidate ID, so a repeated candidate can overwrite attempt provenance.
- `aworld/self_evolve/runner.py:7097-7133` calls every iteration candidate
  "replayed" and assigns all absent candidates the reason
  `not_replayed_due_to_budget`, regardless of their actual terminal stage.
- Existing plan 001 addressed broad runtime bounding and concurrency at an older
  commit. Current code already contains concurrency policy and telemetry; this
  plan supersedes its remaining budget/reporting work instead of reimplementing
  concurrency.

The scheduler must scale estimates by the normalized member/probe plan from
plans 004–005. A run with N trajectories must not behave as a one-case run, but
N may be reduced to K distinct conformance shapes where plan 005 proves those
checks equivalent.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Telemetry | `python -m pytest tests/self_evolve/test_execution_telemetry.py -q` | all pass |
| Cost estimator | `python -m pytest tests/self_evolve/test_evaluation_backend.py -k "replay_cost or budget" -q` | all selected tests pass |
| Optimizer scheduling | `python -m pytest tests/self_evolve/test_optimizer_contract.py -k "population or repair or duplicate" -q` | all selected tests pass |
| Runner lifecycle | `python -m pytest tests/self_evolve/test_runner.py -k "budget or population or duplicate or repair" -q` | all selected tests pass |
| Contract matrix | `python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q` | all cardinality cells pass |

## Scope

**In scope**

- A new `aworld/self_evolve/budget.py`
- `aworld/self_evolve/concurrency.py`
- `aworld/self_evolve/evaluation.py`
- `aworld/self_evolve/runner.py`
- `aworld/self_evolve/store.py`
- `aworld/self_evolve/optimizers/llm_mutator.py`
- `aworld/self_evolve/config.py` if budget configuration belongs there
- CLI optimize plumbing only when required to expose distinct total/per-replay
  limits
- `tests/self_evolve/test_execution_telemetry.py`
- `tests/self_evolve/test_evaluation_backend.py`
- `tests/self_evolve/test_optimizer_contract.py`
- `tests/self_evolve/test_runner.py`
- `tests/self_evolve/test_framework_contract_matrix.py`
- `docs/AWorld CLI/Commands/Optimize.md`

**Out of scope**

- Lowering verification repetitions to make estimates fit.
- A failure-code-specific retry count.
- Treating all multiple trajectories as one representative case for cost.
- Replacing the concurrency implementation from plan 001/#933.
- Charging raw character counts as model tokens when actual usage metadata is
  available.
- Deleting historical candidate artifacts to repair reporting.

## Git workflow

- Branch: `codex/007-stage-aware-self-evolve-budget`
- Suggested commit: `feat(self-evolve): schedule candidates against observed run budget`
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Define an append-only candidate lifecycle record

Introduce a typed lifecycle record per generation attempt, keyed by run,
iteration, and slot, with candidate ID as a property rather than the storage
key. Track transitions through:

- generated;
- duplicate-filtered or unique;
- local gates;
- adaptation compile;
- repair conformance;
- representative screening;
- paired replay started/completed/comparable;
- evaluation;
- selected/rejected/blocked/not-run.

Each terminal transition must carry a stable reason code and the typed event ID
from plan 004/006 when applicable. A candidate may have one canonical package,
but repeated generation attempts must retain separate append-only attempt
records. Do not overwrite lineage provenance by candidate ID.

**Verify**:
`python -m pytest tests/self_evolve/test_runner.py -k "lineage or duplicate or population" -q`
→ duplicate attempts retain two attempt records and one canonical candidate.

### Step 2: Derive population reports from lifecycle records

Replace `_population_report`'s iteration-report inference with an aggregation of
the lifecycle records. Report at least:

- generation attempt count;
- unique candidate count;
- duplicate count;
- count reaching each stage;
- paired replay started/completed/comparable counts;
- exact terminal reason counts;
- per-stage token/cost/time usage;
- case count and distinct conformance-shape count used for estimates.

Keep old fields only as additive compatibility aliases with documented
semantics. Never call adaptation-only candidates replayed. Never assign
`not_replayed_due_to_budget` unless the budget ledger actually denied the next
stage.

**Verify**:
`python -m pytest tests/self_evolve/test_runner.py -k "population_report or replayed_candidate" -q`
→ stage counts match fake backend call counts exactly.

### Step 3: Add a run-wide reserve/debit/release budget ledger

Create a pure, unit-tested `RunBudgetLedger` with:

- configured total token, cost, and optional wall-clock ceilings;
- observed usage by stage;
- outstanding reservations by stage/item;
- estimate source and confidence;
- reserve, debit actual, release, and remaining operations;
- a deterministic decision object when work is denied.

Use actual candidate-generation token telemetry from `concurrency.py` to debit
the ledger. For future generation, use a rolling robust estimate from completed
slots/batches with a conservative cold-start configuration. For replay and
judge work, estimate using normalized case count, repetitions, and observed or
configured per-attempt usage. Zero is valid only when the backend proves the
stage consumes no model tokens; unknown must not silently become zero.

If the existing `max_run_tokens` is also used as a per-replay cap, split the
concept into clearly named total-run and per-attempt settings while retaining a
backward-compatible mapping and deprecation path.

**Verify**:
`python -m pytest tests/self_evolve/test_execution_telemetry.py tests/self_evolve/test_evaluation_backend.py -k "token or budget or replay_cost" -q`
→ reservation and actual debit tests pass, including unknown estimates.

### Step 4: Scale reservations by cardinality and conformance shape

Before scheduling each stage:

- candidate generation reserves per requested slot;
- conformance reserves by distinct probe-group count from plan 005;
- representative screening reserves zero or one selected task case per
  candidate, according to screening policy;
- authoritative replay reserves by normalized replay member count and baseline/
  candidate repetition policy;
- evaluation reserves by actual comparable member/variant count and judge
  repetitions.

Test monotonicity: adding a distinct trajectory case cannot lower the estimate;
adding a duplicate requirement shape may leave conformance cost unchanged but
must still increase authoritative replay cost when that case is replayed.

**Verify**:
`python -m pytest tests/self_evolve/test_framework_contract_matrix.py -k "budget or cost" -q`
→ one- and three-case estimates match the declared formulas.

### Step 5: Add stage-aware exploration and focused-repair scheduling

Replace fixed full batches on every iteration with a deterministic scheduler:

1. Initial frontier: allow the configured exploration population.
2. Once a repairable semantic failure event exists, allocate one focused repair
   slot for that event.
3. Allocate an optional diverse exploration slot only when budget remains and
   it addresses a different strategy/failure frontier.
4. Repeated candidate package or unchanged semantic failure/progress does not
   earn another full batch.
5. A newly observed failure stage/code or increased interaction progress may
   open a new bounded repair frontier.
6. Candidate-owned failure continues with other already generated candidates;
   only a shared-run blocking event stops scheduling.

Use semantic event keys from plan 006, not reason strings. Keep deterministic
limits and report every scheduler decision.

**Verify**:
`python -m pytest tests/self_evolve/test_optimizer_contract.py tests/self_evolve/test_runner.py -k "focused_repair or progress or population or duplicate" -q`
→ stable failure frontiers use one focused slot; new frontiers can expand within
budget.

### Step 6: Prevent duplicate persistence from overwriting provenance

Filter or classify repeated canonical candidate packages before canonical
proposal and lineage writes. Persist:

- one canonical candidate package keyed by package fingerprint/candidate ID;
- one append-only attempt record per generation slot;
- lineage references from attempts to the canonical package;
- a duplicate terminal reason when no new validation is scheduled.

If current consumers require `<candidate_id>.json`, keep that canonical file and
add attempt files under a run/iteration/slot hierarchy. Do not discard earlier
lineage silently.

**Verify**:
create a runner test generating the same candidate in two iterations; assert
the canonical package remains stable, both attempts are present, and population
counts report two attempts/one unique candidate.

### Step 7: Add cardinality, budget, and scheduler regression tests

Extend the contract matrix for:

- one vs three replay members with the same per-attempt usage;
- three cases with one vs two distinct conformance shapes;
- budget sufficient for one case but insufficient for three;
- actual generation usage exceeding the reservation;
- repeated failure event that switches to one focused slot;
- a new failure frontier that permits another bounded repair slot;
- duplicate candidate attempts;
- candidate-owned failure followed by a valid second candidate;
- shared infrastructure failure that releases unused reservations and stops.

No assertion may use a specific production error message or target.

**Verify**:
`python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q`
→ all cells pass.

### Step 8: Document budget semantics and migration

Update Optimize documentation with:

- total-run versus per-attempt limits;
- reservation and observed usage fields;
- cardinality/shape scaling;
- candidate lifecycle funnel meanings;
- focused-repair scheduling rules;
- compatibility/deprecation behavior for existing limit names.

Update any report-schema documentation so consumers do not continue treating
iteration reports as paired replay counts.

**Verify**:
`rg -n "reservation|observed|unique candidate|paired replay|focused repair|per-attempt" 'docs/AWorld CLI/Commands/Optimize.md'`
→ all concepts are documented.

## Test plan

- Unit-test the ledger as a pure component: reserve, deny, debit actual, release,
  overrun, unknown estimate, and deterministic serialization.
- Unit-test lifecycle aggregation independently from runner control flow.
- Update BudgetGate tests so the runner path cannot use an implicit zero
  estimate.
- Parameterize cost and scheduling tests over one and three trajectories.
- Add duplicate attempt/canonical package persistence tests.
- Assert report stage counts equal fake backend invocation counts.
- Assert budget denial is the only source of
  `not_replayed_due_to_budget`/equivalent reason.

## Done criteria

- [x] Every generation attempt has an append-only lifecycle record.
- [x] Population reports distinguish attempts, unique candidates, stages, and
  terminal reasons.
- [x] Actual generation usage debits a run-wide budget.
- [x] Unknown replay/judge token cost is not treated as zero.
- [x] Reservations scale with normalized member count and distinct conformance
  shapes.
- [x] Stable repair frontiers switch to focused single-slot repair.
- [x] Candidate-owned failures continue to other candidates.
- [x] Duplicate candidate lineage is not overwritten.
- [x] One- and multi-trajectory budget/scheduler tests pass.
- [x] Explicit verification repetitions are not silently reduced.
- [x] Focused and full subsystem tests pass.
- [x] No unrelated source files outside the framework integration scope were modified.

## STOP conditions

Stop and report if:

- Token/cost usage from a required backend is unavailable and there is no
  configurable conservative estimate.
- Splitting total-run and per-attempt token limits would silently change public
  behavior without a compatibility path.
- Lifecycle attempt records cannot be added without breaking existing artifact
  readers and no additive schema is possible.
- The scheduler would need model interpretation of free-form failure text.
- Budget enforcement would reduce user-explicit verification repetitions rather
  than declining to schedule the candidate.

## Maintenance notes

Plan 001 is superseded for remaining budget/reporting work; do not revive its
older assumptions about missing concurrency. Future stages must integrate with
the ledger and lifecycle record before they are scheduled. Reviewers should
verify two monotonicity properties: more distinct work cannot cost less, and
equivalent conformance shapes may be deduplicated without removing authoritative
behavioral replay cases.
