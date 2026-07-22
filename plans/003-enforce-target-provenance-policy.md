# Plan 003: Enforce target provenance policy for every verified mutation

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan in
> `plans/README.md` unless a reviewer told you they maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat 70cb5c9a..HEAD -- aworld/self_evolve/provenance.py aworld/self_evolve/credit_assignment.py aworld/self_evolve/gates.py aworld/self_evolve/runner.py tests/self_evolve/test_credit_assignment.py tests/self_evolve/test_gates.py tests/self_evolve/test_runner.py docs/AWorld\ CLI/Commands/Optimize.md`
> If current target-selection or trust-gate behavior differs from the excerpts
> below, stop and report the drift before editing.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: `plans/002-establish-self-evolve-contract-matrix.md`
- **Category**: security
- **Planned at**: commit `70cb5c9a`, 2026-07-21

## Why this matters

`auto_verified` is allowed to mutate a target only after behavioral gates pass,
but behavioral correctness does not answer whether that target is trusted or
authorized for mutation. Today a selected target with no `TargetProvenance`
skips `TrustProvenanceGate`, and inferred new skills receive a special
low-confidence auto-apply exemption. The framework must resolve provenance and
apply one policy for explicit and inferred targets, regardless of whether the
selection came from one trajectory or an aggregated trajectory set.

## Current state

- `aworld/self_evolve/provenance.py:8-15` defines `TargetProvenance` with target,
  source kind, write origin, trust level, protection bit, and reason.
- `aworld/self_evolve/credit_assignment.py:209-250` creates a new draft skill
  selection with confidence `0.85`, but does not create an inventory entry or
  provenance.
- `aworld/self_evolve/runner.py:2974-3015` resolves provenance only when the
  selected target already exists in the inventory.
- `aworld/self_evolve/runner.py:3035-3059` creates an explicit CLI target without
  resolving provenance at all.
- `aworld/self_evolve/runner.py:7926-7929` adds `TrustProvenanceGate` only when
  provenance is non-null. Missing provenance therefore means no trust decision.
- `aworld/self_evolve/runner.py:8248-8251` allows any selected skill carrying
  `new_skill_candidate` to bypass the normal `confidence >= 0.9` and
  `low_confidence` checks.
- `aworld/self_evolve/gates.py:629-666` already rejects protected, generated,
  and external provenance unless the caller supplies explicit policy.
- `docs/AWorld CLI/Commands/Optimize.md:65` states that low-confidence inferred
  targets are blocked for `auto_verified`; the current new-skill exemption
  contradicts this contract.

The target-selection result may be aggregated from many trajectories, but
provenance belongs to the selected mutation target, not to an individual
trajectory. Do not create one provenance decision per case.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Provenance unit tests | `python -m pytest tests/self_evolve/test_provenance.py tests/self_evolve/test_gates.py -q` | all pass |
| Credit assignment | `python -m pytest tests/self_evolve/test_credit_assignment.py -q` | all pass |
| Runner target policy | `python -m pytest tests/self_evolve/test_runner.py -k "target and (provenance or confidence or inferred or explicit)" -q` | all selected tests pass |
| Contract matrix | `python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q` | all pass |

## Scope

**In scope**

- `aworld/self_evolve/provenance.py`
- `aworld/self_evolve/credit_assignment.py`
- `aworld/self_evolve/gates.py`
- `aworld/self_evolve/runner.py`
- `tests/self_evolve/test_provenance.py`
- `tests/self_evolve/test_credit_assignment.py`
- `tests/self_evolve/test_gates.py`
- `tests/self_evolve/test_runner.py`
- `tests/self_evolve/test_framework_contract_matrix.py`
- `docs/AWorld CLI/Commands/Optimize.md`

**Out of scope**

- Changing how target relevance is scored.
- Adding target-specific confidence thresholds.
- Automatically trusting generated or external artifacts because replay passed.
- Adding an implicit allow-list for any named target or skill.
- Changing protected-path semantics or auto-apply target-type registration.

## Git workflow

- Branch: `codex/003-target-provenance-policy`
- Suggested commit: `fix(self-evolve): require provenance for verified targets`
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Introduce a total provenance-resolution boundary

Add a framework helper, preferably in `provenance.py`, that resolves a
`TargetProvenance` for every `SelfEvolveTargetRef` together with a selection
origin. It must use these general rules:

1. An inventory target uses its inventory provenance unchanged.
2. An operator-explicit local target receives explicit, persisted provenance
   whose origin records operator selection; it is not inferred from trajectory
   contents.
3. An inferred target absent from inventory receives generated provenance and
   is fail-closed for verified mutation unless an explicit trust policy allows
   generated targets.
4. External and protected targets remain rejected by the existing gate.
5. If the framework cannot classify a target, return a structured unresolved
   result; never return `None` and silently skip policy.

Do not inspect the number of trace packs in this helper. A target selected from
one or many trajectories must resolve identically when target identity and
selection origin are the same.

**Verify**:
`python -m pytest tests/self_evolve/test_provenance.py tests/self_evolve/test_gates.py -q`
→ all pass, including new unresolved/generated cases.

### Step 2: Make target selection return provenance with the decision

Replace the fragile `(TargetSelectionReport, TargetInventoryEntry | None)`
handoff with a small typed decision object, or an equivalent total return type,
containing:

- the selection report;
- resolved target provenance when a target exists;
- a structured reason when provenance is unresolved.

Use the same decision type for deterministic selection, LLM-assisted selection,
new draft targets, and the final aggregation over multiple trace packs. Do not
attach a different provenance record to each evidence step. Preserve the
existing target report JSON shape unless an additive `provenance_status` field
is needed.

**Verify**:
`python -m pytest tests/self_evolve/test_credit_assignment.py -q`
→ all pass; new tests cover existing inventory target and generated draft
target.

### Step 3: Apply trust policy unconditionally in verified mode

Update CLI runner construction and `_candidate_gate_results` so that:

- every `auto_verified` target has a provenance gate result;
- unresolved provenance produces a failed `trust_provenance` gate or an
  earlier rejected target-selection result;
- proposal mode may preserve a proposal artifact, but must report unresolved
  or generated provenance explicitly;
- generated and external targets require a named, explicit policy input to
  pass; do not infer authorization from `auto_apply_target_types`;
- explicit local targets and inventory-local targets continue to work.

Remove the `new_skill_candidate` special return from
`_inferred_target_confident_for_auto_apply`. Apply the same confidence rule to
all inferred targets. If product owners later want a lower threshold for a
class of targets, that must be a separate named policy with tests and docs.

**Verify**:
`python -m pytest tests/self_evolve/test_runner.py -k "provenance or low_confidence or new_skill_candidate or explicit_target" -q`
→ all selected tests pass.

### Step 4: Add a trajectory-cardinality policy matrix

Extend `test_framework_contract_matrix.py` with parameterized tests for one and
three trace packs. For both cardinalities assert:

- the same existing local target resolves to the same provenance and may enter
  verified evaluation;
- a generated inferred target is proposal-only by default;
- a low-confidence inferred target is rejected before candidate generation;
- explicit target selection cannot erase an inventory `protected=True` flag;
- missing provenance never removes the trust gate from the report.

The three-trajectory case should include multiple evidence IDs contributing to
one selected target. It must still persist one target-level provenance record.

**Verify**:
`python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q`
→ all pass for both cardinalities.

### Step 5: Align documentation and reports

Update the Optimize documentation to state:

- provenance and confidence are independent gates;
- inferred generated targets default to proposal-only;
- trajectory aggregation can strengthen target evidence but cannot grant write
  trust;
- behavioral replay never substitutes for mutation authorization.

Ensure `report.json` and CLI summary expose the provenance artifact path or a
structured unresolved reason for any selected target.

**Verify**:
`rg -n "provenance|generated target|low-confidence" 'docs/AWorld CLI/Commands/Optimize.md'`
→ documents all three concepts.

## Test plan

- Unit-test provenance resolution for inventory-local, explicit-local,
  inferred-generated, external, protected, and unresolved targets.
- Update the existing test at `tests/self_evolve/test_runner.py:9460-9615`:
  low-confidence generated inferred skill must no longer auto-apply.
- Retain a positive auto-verified test for a sufficiently confident, trusted
  local target.
- Parameterize the policy tests over one and three trajectories.
- Assert a multi-trajectory selection produces one target provenance sidecar,
  not one per member.

## Done criteria

- [ ] No selected target reaches `auto_verified` gates with
  `target_provenance=None`.
- [ ] `TrustProvenanceGate` is fail-closed for unresolved provenance.
- [ ] `new_skill_candidate` no longer bypasses confidence or trust policy.
- [ ] Explicit target selection does not bypass inventory protection.
- [ ] One- and multi-trajectory policy tests pass with identical target-level
  semantics.
- [ ] Proposal mode remains able to persist a non-applied generated proposal
  with clear provenance status.
- [ ] Focused tests and `python -m pytest tests/self_evolve -q` pass on a
  supported host.
- [ ] No files outside Scope and the plan index are modified.

## STOP conditions

Stop and report if:

- Existing public API consumers require `TargetSelectionReport` to remain
  binary-compatible and no additive decision wrapper is possible.
- The implementation would need to infer trust from trajectory text or model
  output.
- A protected inventory target can be addressed through a different path or
  alias that loses its provenance.
- Passing generated targets requires silently changing the existing
  `TrustProvenanceGate` defaults.

## Maintenance notes

Reviewers should treat any future `if signal == <target name>` trust exemption
as a regression. Target evidence, target identity, and mutation authorization
are separate concepts. Multi-trajectory aggregation may affect evidence and
confidence, but provenance must remain a single target-level policy decision.
