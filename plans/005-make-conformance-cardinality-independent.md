# Plan 005: Make candidate conformance cardinality-independent

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan in
> `plans/README.md` unless a reviewer told you they maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat 70cb5c9a..HEAD -- aworld/self_evolve/runner.py aworld/self_evolve/repair_conformance.py aworld/self_evolve/replay_capability.py tests/self_evolve/test_runner.py tests/self_evolve/test_repair_conformance.py tests/self_evolve/test_replay_capability.py tests/self_evolve/test_framework_contract_matrix.py docs/AWorld\ CLI/Commands/Optimize.md`
> If `_screen_candidate_population` no longer owns repair-conformance preflight,
> stop and compare the live pipeline before editing.

## Status

- **Implementation**: DONE at `957943e9`; causal/public-boundary integration verified at `af6934b7`
- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: `plans/004-unify-replay-lifecycle-semantics.md`
- **Category**: bug
- **Planned at**: commit `70cb5c9a`, 2026-07-21

## Why this matters

Repair conformance is a candidate capability contract, while representative
screening is an optional task-quality optimization. Today both live in the same
function, so a dataset with one replayable case returns before source,
compile/freeze, and runtime probe validation. The framework must always validate
candidate-owned capability behavior and cover every distinct requirement shape;
trajectory count may affect deduplication and cost, but must never switch the
validation capability off.

## Current state

- `aworld/self_evolve/runner.py:1416-1432` computes a representative screening
  dataset and returns immediately when it is `None`.
- `aworld/self_evolve/runner.py:1457-1500` performs source conformance and frozen
  runtime preflight only after that early return.
- `aworld/self_evolve/runner.py:7143-7152` returns no screening dataset when
  there is one or fewer replayable cases.
- `aworld/self_evolve/runner.py:1609-1665` already has a bounded
  `_preflight_candidate_repair_conformance` implementation that compiles and
  freezes the candidate package against the authoritative dataset.
- `aworld/self_evolve/replay.py:3201-3226` exposes
  `preflight_frozen_replay_capability`, which starts the isolated service,
  executes declared probes, and stops it.
- `aworld/self_evolve/repair_conformance.py:34-76` carries required branch,
  operation, exact-probe, and fixture-derived probe information.
- `docs/AWorld CLI/Commands/Optimize.md:97-105` already promises the desired
  generic order: conformance, then representative screening, then authoritative
  paired replay.

The required pipeline is:

`local/package gates → source conformance → compile/freeze → protocol probes → optional representative task screening → authoritative paired replay`

The first four stages are candidate conformance and must not depend on dataset
cardinality. Representative screening remains optional.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Repair contracts | `python -m pytest tests/self_evolve/test_repair_conformance.py -q` | all pass |
| Runner screening/preflight | `python -m pytest tests/self_evolve/test_runner.py -k "candidate_screening or repair_conformance" -q` | all selected tests pass |
| Runtime capability probes | `python -m pytest tests/self_evolve/test_replay_capability.py -k "preflight or probe or recorded" -q` | all selected tests pass on a supported sandbox host |
| Contract matrix | `python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q` | all one- and multi-case cells pass |

## Scope

**In scope**

- `aworld/self_evolve/runner.py`
- `aworld/self_evolve/repair_conformance.py`
- `aworld/self_evolve/replay_capability.py` only if a generic probe-plan helper
  is needed
- `tests/self_evolve/test_runner.py`
- `tests/self_evolve/test_repair_conformance.py`
- `tests/self_evolve/test_replay_capability.py`
- `tests/self_evolve/test_framework_contract_matrix.py`
- `docs/AWorld CLI/Commands/Optimize.md`

**Out of scope**

- A special validator for HTTP response excerpts, a named operation, a target,
  or a fixture from one run.
- Treating the first trajectory as representative of conformance requirements.
- Running the full task rollout once per duplicate fixture shape.
- Weakening exact probes or structure-preserving recorded-response checks.
- Changing replay sandbox policy or allowing unsandboxed candidate runtime.

## Git workflow

- Branch: `codex/005-cardinality-independent-conformance`
- Suggested commit:
  `fix(self-evolve): run repair conformance for every dataset cardinality`
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Split conformance from representative screening

Extract a candidate validation stage such as
`_validate_candidate_repair_conformance_population`. It must execute for every
candidate with a `RepairConformanceContract` whenever replay is enabled and a
backend is available, before `_candidate_screening_dataset` is consulted.

Return a typed per-candidate stage result compatible with plan 004:

- passed conformance;
- failed candidate-owned conformance with stage/code;
- blocked because shared infrastructure is unavailable;
- not applicable because the candidate has no repair contract.

Only conformance-passing or not-applicable candidates may enter representative
task screening. A candidate-owned conformance failure must not stop validation
of the next candidate.

**Verify**:
`python -m pytest tests/self_evolve/test_runner.py -k "repair_conformance and screening" -q`
→ all selected tests pass.

### Step 2: Compile a dataset-wide, deduplicated probe plan

Build the conformance probe plan from all replayable cases and capability
requirements in the authoritative dataset. Group only semantically equivalent
checks, using stable framework fingerprints such as:

- capability/service identity;
- operation and transport kind;
- fixture-selector or recorded-response shape fingerprint;
- exact-probe contract when present;
- required non-empty/recorded-response assertion set.

Do not group by list position or assume the same operation name means the same
fixture shape. Preserve deterministic order by stable fingerprint.

Required behavior:

- one case → all its distinct probes run;
- N cases with one repeated requirement shape → the equivalent probe may run
  once, with all affected case IDs recorded;
- N cases with K distinct shapes → all K probe groups run;
- any failed group fails candidate conformance and reports bounded affected-case
  metadata.

The probe plan must never include raw recorded response content in report JSON.

**Verify**:
`python -m pytest tests/self_evolve/test_repair_conformance.py -k "probe or fixture or operation" -q`
→ same-shape deduplication and distinct-shape coverage tests pass.

### Step 3: Execute frozen runtime probes before task screening

Use the existing compile/freeze and
`preflight_frozen_replay_capability` path for each distinct probe group. Reuse a
frozen candidate package when safe, but ensure every group executes with the
correct fixture/probe declarations. Persist one bounded conformance report per
candidate containing:

- total case count;
- distinct probe-group count;
- covered case count;
- pass/fail per group by fingerprint and stable code;
- artifact references;
- no raw fixture values.

If candidate capability compilation or protocol probes fail, emit the typed
candidate-owned event from plan 004. Do not run representative screening or
paired replay for that candidate.

**Verify**:
`python -m pytest tests/self_evolve/test_replay_capability.py -k "preflight or probe" -q`
→ all selected tests pass on a supported sandbox host.

### Step 4: Keep representative screening optional and separate

After conformance:

- for one replayable case, skipping representative task screening is allowed
  because authoritative replay will exercise the case;
- for multiple cases, keep `_candidate_screening_dataset` as a cost-control
  selection step;
- screening may rank or reject a candidate on task behavior, but must not be
  treated as proof that unselected conformance shapes work;
- the authoritative paired replay remains responsible for final dataset-wide
  behavioral evidence.

Rename report fields if needed so `conformance` and `screening` are visibly
separate stages. Do not use `screening_dataset is None` as a reason to skip
conformance.

**Verify**:
`python -m pytest tests/self_evolve/test_runner.py -k "candidate_screening or repair_conformance" -q`
→ single-case conformance runs without task screening; multi-case flow runs
conformance before screening.

### Step 5: Add the cardinality × shape regression matrix

Extend the framework contract matrix with at least these cases:

| Cases | Requirement shapes | Candidate runtime | Expected |
|---:|---:|---|---|
| 1 | 1 | valid | conformance passes before paired replay |
| 1 | 1 | invalid | rejected before task replay |
| 3 | 1 repeated | valid | one equivalent probe group covers all 3 cases |
| 3 | 2 distinct | valid | both groups execute |
| 3 | 2 distinct | one invalid | candidate rejected; failed group identifies affected cases |
| 3 | 2 distinct | candidate 1 invalid, candidate 2 valid | candidate 2 still reaches screening/replay |

Use generic fake transports/operations where a pure unit test suffices. Use the
real frozen runtime sandbox only for the smallest end-to-end cases.

**Verify**:
`python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q`
→ all matrix rows pass.

### Step 6: Align reports and documentation

Expose separate report sections or stage entries for conformance and task
screening. Update documentation to state explicitly that:

- conformance always runs when a repair contract applies;
- multiple trajectories are reduced by requirement fingerprint, not by taking
  one representative case;
- representative screening is not a substitute for conformance coverage;
- authoritative paired replay still evaluates dataset behavior.

**Verify**:
`rg -n "conformance|representative|distinct.*shape|trajectory" 'docs/AWorld CLI/Commands/Optimize.md'`
→ all concepts are documented.

## Test plan

- Model contract construction tests after
  `tests/self_evolve/test_repair_conformance.py` existing fixture and operation
  cases.
- Model runner sequencing tests after
  `tests/self_evolve/test_runner.py:4552-4783`.
- Add one real runtime probe regression with a generic structure-preserving
  response contract; do not copy a production fixture.
- Parameterize one and three cases and verify all distinct shapes are covered.
- Assert conformance is invoked before any task replay backend call.
- Assert candidate-owned conformance failure continues to the next candidate.

## Done criteria

- [ ] Repair conformance has no dependency on
  `_candidate_screening_dataset` returning a case.
- [ ] One-case datasets execute full source/compile/runtime conformance.
- [ ] Multi-case datasets execute every distinct requirement/fixture shape.
- [ ] Equivalent shapes can be deduplicated without losing affected-case
  accounting.
- [ ] Representative screening remains optional and follows conformance.
- [ ] Candidate-owned conformance failure rejects only that candidate.
- [ ] Reports clearly separate conformance, screening, and paired replay.
- [ ] One- and multi-case contract matrices pass.
- [ ] Full `tests/self_evolve` passes on a supported sandbox host.
- [ ] No files outside Scope and the plan index are modified.

## STOP conditions

Stop and report if:

- A distinct requirement shape cannot be represented without copying raw
  fixture content into a report or prompt.
- Probe deduplication lacks a stable semantic fingerprint.
- Frozen capability reuse would permit one candidate package to observe another
  candidate's writable artifacts.
- Correct coverage would require unsandboxed candidate code.
- Representative task screening is currently relied upon to mutate conformance
  artifacts or contracts.

## Maintenance notes

Any new replay transport or requirement type must define its stable conformance
fingerprint and tests for repeated/distinct shapes. Reviewers should reject code
that gates conformance on `len(cases)`, `member_results`, or a representative
case. Cardinality is a cost input; requirement shape is the coverage unit.
