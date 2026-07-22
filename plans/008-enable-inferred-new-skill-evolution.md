# Plan 008: Enable inferred new-skill evolution without weakening explicit target trust

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan in
> `plans/README.md` unless a reviewer told you they maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat 9407fb85..HEAD -- aworld/config/conf.py aworld/self_evolve/credit_assignment.py aworld/self_evolve/provenance.py aworld/self_evolve/gates.py aworld/self_evolve/runner.py aworld/self_evolve/scheduler.py aworld/self_evolve/targets.py aworld/self_evolve/__init__.py aworld-cli/src/aworld_cli/commands/optimize_cmd.py aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py tests/self_evolve/test_config.py tests/self_evolve/test_credit_assignment.py tests/self_evolve/test_provenance.py tests/self_evolve/test_gates.py tests/self_evolve/test_runner.py tests/self_evolve/test_scheduler.py tests/self_evolve/test_framework_contract_matrix.py tests/core/test_optimize_top_level_command.py tests/test_slash_commands.py docs/AWorld\ CLI/Commands/Optimize.md`
> If any in-scope file changed, compare the live target-selection, provenance,
> draft-target, and auto-apply paths against the "Current state" section before
> editing. A semantic mismatch is a STOP condition.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: MED
- **Depends on**: `plans/003-enforce-target-provenance-policy.md`, `plans/004-unify-replay-lifecycle-semantics.md`, `plans/007-add-stage-aware-budget-scheduler.md`
- **Category**: bug
- **Planned at**: commit `9407fb85`, 2026-07-22
- **Completed**: 2026-07-22; verified with full self-evolve, replay sandbox,
  and CLI test suites

## Why this matters

Automatic target inference currently recognizes that a trajectory needs a new
skill and constructs a draft target, but `auto_verified` then applies the
existing-target confidence rule and rejects the run before candidate generation.
The framework therefore has a target adapter, generated provenance, replay, and
promotion machinery that cannot be reached by the normal inferred-new-skill
path. The fix must distinguish safe evolution inside a run-owned draft from
authorization to mutate an existing target or publish a new skill. It must also
remove the domain-specific new-skill identifier shortcut so the behavior applies
to generic capability gaps and to one or multiple trajectories.

The intended behavior is:

| Selection mode | Target state | Required behavior |
|---|---|---|
| inferred | one unprotected inventory match | optimize that existing target |
| inferred | no inventory match and one validated reusable capability intent | create a run-owned draft and execute candidate/replay/evaluation |
| inferred | ambiguous or insufficient capability intent | return typed `no_target`; create nothing |
| operator-explicit | target exists | optimize only that target |
| operator-explicit | target is absent | fail with `explicit_target_not_found`; never create or infer a replacement |

`proposal` may select and persist a draft candidate but never publish it.
`auto_verified` may publish an inferred new skill only after a named new-skill
policy, target authorization, replay/evaluation, package, apply, registry-refresh,
and post-apply gates all pass.

## Current state

- `aworld/self_evolve/credit_assignment.py:78-143` couples the selection report
  to provenance but has no orthogonal mutation intent. A generated draft and an
  inferred attempt to mutate an existing target are both simply `INFERRED`.
- `aworld/self_evolve/credit_assignment.py:312-354` can return a draft skill at
  confidence `0.85`, but `credit_assignment.py:675-695` derives that target from
  named domain markers and always assigns a fixed domain-specific ID. This is
  not a general new-skill framework contract.
- `aworld/self_evolve/credit_assignment.py:439-520` rejects an LLM diagnosis
  when its target is absent from inventory, even when the intended operation is
  creation rather than mutation. The `LLMTargetDiagnosis` seam is only exercised
  by unit tests; the normal CLI does not use it.
- `aworld/self_evolve/provenance.py:249-326` correctly classifies an inferred
  absent skill as generated provenance. That classification should remain
  fail-closed for existing-target mutation, but it is sufficient identity
  metadata for isolated draft evolution when combined with a typed creation
  intent and canonical path.
- `aworld/self_evolve/runner.py:6039-6080` applies
  `_inferred_target_confident_for_auto_apply` before loading the target adapter.
  `runner.py:12193-12223` blocks every inferred report below `0.9` or carrying
  `low_confidence`, so an otherwise valid generated draft never reaches candidate
  generation, replay, or evaluation.
- `aworld/self_evolve/gates.py:644-714` has one trust decision for generated
  provenance. It cannot express “may evolve in an isolated draft, may not yet
  publish.” Proposal mode works only because `runner.py:2615-2645` ignores the
  failed trust gate; verified mode cannot do so.
- `aworld/self_evolve/targets.py:308-386` already provides
  `DraftSkillTextTarget`, a skeleton baseline, release path, and rollback. Its
  current draft path is selected before a run exists and may be shared across
  runs; the new creation flow needs a run-owned path and collision checks.
- `aworld/self_evolve/runner.py:11820-11865` already keeps explicit
  `skill:<id>` strict: `_skill_target_from_id` raises `FileNotFoundError` if the
  skill does not exist. Preserve this behavior and add tests that prevent any
  fallback into draft creation.
- `aworld/config/conf.py:201-290` and `aworld/self_evolve/scheduler.py:220-250`
  expose no named inferred-new-skill policy. The broad
  `allow_generated_target_mutation` programmatic flag is not wired through the
  normal CLI and must not become an implicit global bypass.
- `docs/AWorld CLI/Commands/Optimize.md:59-78` says inferred generated targets
  default to proposal-only and that provenance is independent of confidence.
  It does not describe a verified new-skill creation lifecycle or the strict
  explicit-target behavior.
- `tests/self_evolve/test_runner.py:12360-12655` characterizes proposal draft
  evolution and explicitly expects low-confidence inferred drafts to be rejected
  before target loading in `auto_verified`. These tests must be replaced by an
  intent-aware matrix, not deleted without equivalent coverage.

Repository conventions to preserve:

- Use frozen dataclasses and string enums for typed public decisions, following
  `TargetSelectionDecision` and the enums in `provenance.py`.
- Resolve and validate filesystem paths at one boundary; do not authorize based
  on a caller-supplied path. Follow `canonical_local_target_path` and the strict
  artifact/path checks already used by target provenance.
- Persist additive typed fields and stable lower-snake-case reason codes. Keep
  existing report fields readable; do not repurpose `selection_origin`.
- Candidate lifecycle, execution status, target intent, provenance, and
  promotion status are orthogonal. Do not infer one from another.
- Production code and tests must not branch on a historical run ID, case ID,
  target name, URL, podcast keyword, or exact failure sentence.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Selection/provenance | `python -m pytest tests/self_evolve/test_credit_assignment.py tests/self_evolve/test_provenance.py tests/self_evolve/test_gates.py -q` | all pass |
| Runner intent paths | `python -m pytest tests/self_evolve/test_runner.py -k "draft or inferred or explicit_target or new_skill or provenance" -q` | all selected tests pass |
| Config/background wiring | `python -m pytest tests/self_evolve/test_config.py tests/self_evolve/test_scheduler.py -q` | all pass |
| Cardinality contract | `python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q` | all pass for one- and multi-trajectory cells |
| CLI forwarding/reporting | `python -m pytest tests/core/test_optimize_top_level_command.py tests/test_slash_commands.py -k optimize -q` | all selected tests pass |
| Self-evolve subsystem | `python -m pytest tests/self_evolve -q` | all pass |
| Syntax | `python -m py_compile aworld/self_evolve/credit_assignment.py aworld/self_evolve/provenance.py aworld/self_evolve/gates.py aworld/self_evolve/runner.py aworld/self_evolve/targets.py` | exit 0 |

Current focused characterization baseline at the planned commit:

`python -m pytest -q tests/self_evolve/test_credit_assignment.py tests/self_evolve/test_provenance.py tests/self_evolve/test_gates.py tests/self_evolve/test_framework_contract_matrix.py tests/self_evolve/test_runner.py -k 'draft or inferred or explicit_target or provenance or new_skill or confidence'`
→ `63 passed, 229 deselected`.

## Scope

**In scope**

- `aworld/config/conf.py`
- `aworld/self_evolve/credit_assignment.py`
- `aworld/self_evolve/provenance.py`
- `aworld/self_evolve/replay.py`
- `aworld/self_evolve/gates.py`
- `aworld/self_evolve/runner.py`
- `aworld/self_evolve/scheduler.py`
- `aworld/self_evolve/targets.py`
- `aworld/self_evolve/__init__.py`
- `aworld-cli/src/aworld_cli/commands/optimize_cmd.py`
- `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py`
- `tests/self_evolve/test_config.py`
- `tests/self_evolve/test_credit_assignment.py`
- `tests/self_evolve/test_provenance.py`
- `tests/self_evolve/test_gates.py`
- `tests/self_evolve/test_runner.py`
- `tests/self_evolve/test_replay_overlay.py`
- `tests/self_evolve/test_scheduler.py`
- `tests/self_evolve/test_framework_contract_matrix.py`
- `tests/core/test_optimize_top_level_command.py`
- `tests/test_slash_commands.py`
- `docs/AWorld CLI/Commands/Optimize.md`
- `plans/README.md`

**Out of scope**

- Any file under `aworld-skills/`; tests must create synthetic skills under
  `tmp_path`.
- Reintroducing or special-casing any removed skill.
- Lowering the existing-target confidence threshold globally.
- Treating `auto_apply_target_types` or `allow_generated_target_mutation` as an
  implicit new-skill creation policy.
- Silently creating a skill when the operator supplied `--target`.
- Installing a draft before replay/evaluation and post-apply gates pass.
- Using one representative trajectory as proof for all members.
- Adding a model call before target selection unless it is bounded, observable,
  budgeted, and covered by infrastructure-failure tests. Prefer the existing
  typed diagnoser seam and structured trace features; do not add an unaccounted
  LLM call as a shortcut.

## Git workflow

- Branch: `codex/008-inferred-new-skill-evolution`
- Suggested commits:
  - `feat(self-evolve): model inferred draft creation intent`
  - `feat(self-evolve): verify and promote inferred new skills`
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Add an orthogonal target mutation intent

Introduce a typed enum, preferably in `provenance.py`, with at least:

- `existing_target_mutation`
- `inferred_draft_creation`

Add the intent to `TargetSelectionDecision` and persist it additively in
`TargetSelectionReport`. Keep `TargetSelectionOrigin` unchanged:
`INFERRED`/`OPERATOR_EXPLICIT` says who selected the target; mutation intent says
whether the operation modifies an inventory target or creates a new isolated
draft.

Enforce these invariants in constructors/builders:

1. `inferred_draft_creation` requires `selection_origin=INFERRED`.
2. It requires target type `skill`, no matching inventory entry, generated
   provenance, and a non-empty validated evidence set.
3. `existing_target_mutation` requires exactly one authoritative inventory
   entry for inferred selection, or a valid existing local target for explicit
   selection.
4. Operator-explicit selection may never carry `inferred_draft_creation`.
5. Unknown enum values, contradictory intent/provenance, duplicate inventory
   identities, and path mismatches fail closed.

Do not derive intent from signal strings such as `new_skill_candidate`; signals
remain diagnostics only.

**Verify**:
`python -m pytest tests/self_evolve/test_credit_assignment.py tests/self_evolve/test_provenance.py -q`
→ new constructor/adversarial tests pass for every valid and contradictory
origin × intent × inventory combination.

### Step 2: Compile generic new-skill intents instead of named cases

Replace `_reusable_skill_target_id` and the domain-specific
`_assign_new_skill_candidate` branch with a generic typed capability-intent
compiler. It must consume bounded structured trace features, for example:

- normalized tool/action operation identifiers;
- typed failure category/code and repairability;
- external dependency kinds;
- evidence IDs that belong to the contributing trace packs;
- an optional validated suggested slug from the existing `LLMTargetDiagnosis`
  seam.

The compiler must return either one `NewSkillIntent` or a typed no-target reason.
The intent should contain a canonical capability fingerprint, a sanitized skill
ID, bounded public capability summary, confidence, and exact evidence IDs. Skill
IDs must be lower-kebab-case, length-bounded, path-free, non-reserved, and stable
for the same normalized capability fingerprint. When no trustworthy readable
slug exists, use a neutral fingerprint-derived ID; never inspect a historical
target name to choose it.

Extend `LLMTargetDiagnosis` so it can explicitly request `new_skill` rather than
having an absent inventory ID interpreted as an error. Validate its evidence
IDs and suggested ID before creating an intent; the model may suggest identity
metadata but never a filesystem path or trust policy. Existing-target diagnoses
must still resolve exactly one inventory entry.

If the normal CLI cannot supply a generic diagnosis without a new model call,
use the structured deterministic compiler for the first implementation and
retain the injectable diagnoser for programmatic enrichment. Do not introduce
an unbudgeted model call before the run ledger exists.

**Verify**:
`python -m pytest tests/self_evolve/test_credit_assignment.py -q`
→ generic synthetic HTTP, filesystem, and tool-orchestration capability gaps
produce stable new-skill intents without named production targets; ambiguous,
low-evidence, invalid-slug, and inventory-collision cases return typed no-target
results.

### Step 3: Materialize drafts only after the run owns their path

Stop putting authorization weight on the pre-run global path
`.aworld/self_evolve/drafts/skills/<id>/SKILL.md`. Keep the new-skill intent
path-free until `optimize_from_cli_request` has created the run ID, then derive a
canonical run-owned draft path such as:

`.aworld/self_evolve/<run_id>/draft_target/<target_id>/SKILL.md`

Build the final `SelfEvolveTargetRef`, generated provenance, and
`DraftSkillTextTarget` from that canonical path. Validate that:

- the draft path is inside the current run directory and no component is a
  symlink;
- the release path is exactly `aworld-skills/<target_id>/SKILL.md` under the
  workspace;
- neither selection nor apply may overwrite an inventory target that appeared
  after inference;
- stale drafts from another run are never loaded as the baseline;
- the skeleton and candidate package identify the same target ID.

Keep explicit target resolution strict. `--target skill:missing` must raise
`FileNotFoundError`/`explicit_target_not_found` before any draft target or
candidate is created.

**Verify**:
`python -m pytest tests/self_evolve/test_runner.py -k "draft or explicit_target" -q`
→ run-owned path, stale-draft isolation, symlink/path-escape rejection,
release-collision rejection, and explicit-missing tests pass.

### Step 4: Separate draft evolution authorization from promotion authorization

Make `TrustProvenanceGate` intent-aware without weakening its existing-target
rules:

- generated provenance plus `inferred_draft_creation` may pass the local gate
  only for evolution inside the canonical run-owned draft;
- generated provenance plus `existing_target_mutation` remains denied unless
  the existing named generated-mutation policy explicitly allows it;
- protected, external, unresolved, contradictory, or non-local targets remain
  denied;
- proposal mode no longer needs to ignore a failed trust gate for a valid draft
  creation intent—the gate should report the precise authorized scope.

Add a separate typed promotion gate/policy. Introduce a configuration value such
as:

`inferred_new_skill_policy = disabled | draft_only | auto_verified`

Recommended default: `auto_verified`, because omitting `--target` delegates
target choice and `--apply auto_verified` already delegates verified apply.
Semantics:

- `disabled`: do not create a draft; return typed no-target/policy-disabled.
- `draft_only`: run generation/replay/evaluation and persist the selected draft,
  but never write `aworld-skills/`.
- `auto_verified`: publish only after every ordinary verified gate plus the
  new-skill promotion checks passes.

The promotion gate must re-check canonical release locality, absence of an
inventory/release collision, candidate target/package identity, verified replay
and evaluation, target type policy, runtime registry refresh, and post-apply
evaluation. A failed promotion must leave the release path absent and preserve
the draft/candidate artifacts for diagnosis. Keep
`allow_generated_target_mutation` for its existing programmatic meaning; do not
reuse it as this policy.

**Verify**:
`python -m pytest tests/self_evolve/test_gates.py tests/self_evolve/test_runner.py -k "new_skill or draft or promotion or provenance" -q`
→ all policy values and all fail-closed gates pass.

### Step 5: Replace the global confidence block with intent-aware admission

Replace `_inferred_target_confident_for_auto_apply` with a typed admission
decision:

- inferred existing-target mutation keeps the current `>=0.9` and no
  `low_confidence` rule;
- inferred draft creation is admitted when its `NewSkillIntent` passed the
  generic evidence/identity compiler and policy is not disabled;
- confidence remains reportable evidence quality, but a legacy
  `low_confidence` signal must not by itself turn an authorized isolated draft
  into `no_target`;
- ambiguous new-skill identity or insufficient evidence remains a pre-generation
  rejection;
- behavioral gates may authorize candidate quality and promotion, but may not
  rewrite target intent or provenance.

Do not lower a numeric threshold globally and do not add a target-specific
exception. Replace the existing test that asserts early rejection of every
low-confidence generated draft with separate existing-mutation and draft-
creation assertions.

**Verify**:
`python -m pytest tests/self_evolve/test_runner.py -k "inferred and (confidence or draft or new_skill)" -q`
→ existing low-confidence mutation is still blocked; validated new-skill intent
reaches candidate generation for both proposal and auto-verified policies.

### Step 6: Preserve cardinality-neutral aggregation and full replay authority

Update `_aggregate_target_selection_decisions` and trajectory auto-grouping so
one and multiple trajectories use the same target-intent path:

- reports with the same capability fingerprint and target intent aggregate
  evidence IDs into one new-skill intent;
- conflicting intents, conflicting fingerprints for the same ID, or mixed
  create/mutate decisions fail closed or form separate auto-groups;
- N contributing trajectories remain N normalized replay members;
- target aggregation may strengthen evidence but cannot grant promotion trust;
- conformance may deduplicate K equivalent shapes, while authoritative replay
  still covers N members using the per-member repetition contract from Plan 007.

Add one- and three-trajectory cells for existing mutation, new draft creation,
ambiguous creation, disabled policy, verified promotion, and failed promotion.
No test may branch on a production target name or use only repeated copies of
the same object; include distinct/equivalent shape combinations.

**Verify**:
`python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q`
→ all cardinality cells pass with identical intent semantics and declared N/K
cost behavior.

### Step 7: Wire configuration, background jobs, CLI, reports, and docs

Add `inferred_new_skill_policy` to `SelfEvolveConfig`, direct framework APIs,
background scheduler payloads, top-level CLI, and slash command. Expose a CLI
option such as `--new-skill-policy` with the three explicit values. The default
must be identical across direct, background, top-level, and slash-command paths.

Add report fields without removing existing ones:

- `target_intent`
- `capability_fingerprint`
- `draft_target_path`
- `draft_status`
- `promotion_policy`
- `promotion_status`
- stable promotion reason code

CLI output should distinguish:

- target-selection rejection;
- draft evolved but publication withheld;
- verified new skill published;
- explicit target not found.

Update Optimize documentation with the decision matrix, isolation/promotion
boundary, explicit-target rule, policy values, single/multi-trajectory behavior,
and the fact that new-skill replay/evaluation does not weaken target provenance.

**Verify**:
`python -m pytest tests/self_evolve/test_config.py tests/self_evolve/test_scheduler.py tests/core/test_optimize_top_level_command.py tests/test_slash_commands.py -k "self_evolve or optimize or new_skill" -q`
→ configuration round-trip and every forwarding/reporting surface pass.

`rg -n "new skill|draft|promotion|explicit target|new-skill-policy" 'docs/AWorld CLI/Commands/Optimize.md'`
→ all concepts are documented.

### Step 8: Run subsystem verification and inspect scope

Run:

1. `python -m py_compile aworld/self_evolve/credit_assignment.py aworld/self_evolve/provenance.py aworld/self_evolve/gates.py aworld/self_evolve/runner.py aworld/self_evolve/targets.py`
2. `python -m pytest tests/self_evolve -q`
3. `python -m pytest tests/core/test_optimize_top_level_command.py tests/test_slash_commands.py -k optimize -q`
4. `git diff --check`
5. `git status --short`

Expected: every command exits 0; only in-scope files and the Plan 008 status row
are modified. Do not stage unrelated workspace artifacts.

## Test plan

Use existing test structure in:

- `tests/self_evolve/test_credit_assignment.py` for typed diagnosis and evidence
  validation;
- `tests/self_evolve/test_provenance.py` and `test_gates.py` for adversarial
  intent/provenance combinations;
- `tests/self_evolve/test_runner.py` for end-to-end draft evolution, verified
  promotion, rollback, and explicit target behavior;
- `tests/self_evolve/test_framework_contract_matrix.py` for one/three trajectory
  parity;
- `tests/self_evolve/test_config.py` and `test_scheduler.py` for policy wiring;
- `tests/core/test_optimize_top_level_command.py` and `tests/test_slash_commands.py`
  for CLI forwarding and summaries.

Required cases:

1. Inferred existing unprotected skill: unchanged mutation path.
2. Inferred protected/external skill: rejected; no draft fallback.
3. Inferred absent skill with validated generic intent: run-owned draft evolves.
4. Proposal new skill: candidate persists, release path stays absent.
5. Auto-verified new skill with all gates passing: atomic release, registry
   refresh, post-apply success, report says promoted.
6. Any replay/evaluation/package/promotion/post-apply failure: release absent or
   rolled back, report retains draft and exact typed reason.
7. Release target appears between inference and apply: collision rejection.
8. Invalid/escaping/reserved suggested ID, symlinked draft root, mismatched
   candidate target, missing evidence, or contradictory intent: fail closed.
9. Explicit existing target: unchanged path.
10. Explicit missing target: `explicit_target_not_found`; no inferred draft,
    even when trajectory supports a reusable capability.
11. One and three trajectories: same target-intent semantics; N replay members,
    K conformance shapes, no representative-only authorization.
12. Conflicting multi-trajectory capability fingerprints: separate group or
    typed ambiguous no-target; never silently merge.
13. Policy `disabled`, `draft_only`, and `auto_verified`: exact behavior and
    report status for direct CLI, slash command, and background scheduler.

Tests must use generic synthetic target IDs and trace features. They must not
mention a historical domain-specific target ID, rejected run ID, domain fixture,
or exact historical failure sentence.

## Done criteria

- [x] `TargetSelectionDecision` carries a validated target mutation intent
  orthogonal to origin and provenance.
- [x] No production new-skill branch uses a named domain target, URL, case ID,
  or historical failure string.
- [x] Inferred absent reusable capabilities enter a run-owned draft evolution
  path for proposal and permitted auto-verified policies.
- [x] Low-confidence existing-target mutation remains blocked; no global
  confidence threshold is lowered.
- [x] Explicit missing targets fail without inference or draft creation.
- [x] Generated draft evolution and verified promotion use separate typed gates.
- [x] Proposal and `draft_only` never write `aworld-skills/`.
- [x] Auto-verified promotion writes only after all ordinary and new-skill gates
  pass, and rolls back on registry/post-apply failure.
- [x] Inventory/release collisions, path escapes, symlinks, contradictory
  intent/provenance, and invalid IDs fail closed.
- [x] One- and three-trajectory tests have identical intent semantics and retain
  N-member authoritative replay.
- [x] Direct framework, background scheduler, top-level CLI, and slash command
  share one policy default and report schema.
- [x] Focused tests, CLI tests, `python -m pytest tests/self_evolve -q`, syntax,
  and `git diff --check` pass.
- [x] No files under `aworld-skills/` and no files outside Scope are modified.

## STOP conditions

Stop and report instead of improvising if:

- Generic new-skill identity cannot be derived from structured trace evidence
  without adding an unbudgeted model call.
- A caller requires a model-suggested filesystem path or direct trust decision.
- Existing public consumers cannot accept additive target-intent/report fields
  and no compatibility wrapper is possible.
- Run-owned draft materialization requires weakening canonical path, symlink, or
  inventory-collision checks.
- Verified promotion would need to skip replay, evaluation, registry refresh,
  or post-apply rollback.
- Explicit missing-target behavior cannot remain strict.
- Multi-trajectory aggregation would need to select one representative case as
  behavioral authorization.
- The implementation would require editing a production skill or adding a
  target-specific exception.

## Maintenance notes

- Review origin, intent, provenance, evolution authorization, and promotion
  authorization as five separate dimensions. A future shortcut that derives one
  from another reintroduces this bug.
- The broad generated-target mutation flag is not a substitute for the named
  inferred-new-skill policy; keep their reports and tests separate.
- If a bounded target-diagnosis model stage is added later, integrate it with
  the Plan 007 ledger before enabling it by default.
- Future target types may adopt the same create-vs-mutate contract, but this
  plan enables creation only for skills because `DraftSkillTextTarget` is the
  only implemented creation adapter.
- Reviewers should reject target-name conditionals even if framed as a
  deterministic optimization. Capability fingerprints and typed evidence are
  the reusable boundary.
