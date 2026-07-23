# Plan 009: Add bounded self-improvement campaigns with a Goal handoff

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan in
> `plans/README.md` unless a reviewer told you they maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat e803e142..HEAD -- aworld/config/conf.py aworld/self_evolve/campaign.py aworld/self_evolve/store.py aworld/self_evolve/lifecycle.py aworld/self_evolve/runner.py aworld/self_evolve/scheduler.py aworld/self_evolve/__init__.py aworld-cli/src/aworld_cli/commands/optimize_cmd.py aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py aworld-cli/src/aworld_cli/builtin_plugins/goal_session/common.py aworld-cli/src/aworld_cli/builtin_plugins/goal_session/hooks/stop.py aworld-cli/src/aworld_cli/builtin_plugins/goal_session/hooks/task_completed.py tests/self_evolve/test_campaign.py tests/self_evolve/test_config.py tests/self_evolve/test_store.py tests/self_evolve/test_lifecycle.py tests/self_evolve/test_runner.py tests/self_evolve/test_scheduler.py tests/self_evolve/test_framework_contract_matrix.py tests/core/test_optimize_top_level_command.py tests/test_slash_commands.py tests/plugins/test_plugin_commands.py docs/Agents/Self\ Evolve.md docs/AWorld\ CLI/Commands/Optimize.md docs/AWorld\ CLI/Plugins/Goal\ Session.md plans/README.md`
> If any in-scope file changed, compare the live report, budget, scheduler,
> store, CLI, and Goal contracts against the "Current state" section before
> editing. A semantic mismatch is a STOP condition.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: MED
- **Depends on**: `plans/004-unify-replay-lifecycle-semantics.md`,
  `plans/006-propagate-causal-failure-memory.md`,
  `plans/007-add-stage-aware-budget-scheduler.md`,
  `plans/008-enable-inferred-new-skill-evolution.md`
- **Category**: direction
- **Planned at**: commit `e803e142`, 2026-07-22

## Why this matters

`auto_verified` can improve several candidates inside one run, but a terminal
`rejected` result ends the workflow. The next attempt has to be launched by an
operator even when the typed failure says that another bounded candidate repair
or infrastructure retry is safe. Conversely, blindly retrying every rejection
would spend budget on policy denials, unchanged semantic frontiers, and shared
framework defects.

Add one small orchestration layer: a **self-improvement campaign** owns a bounded
sequence of ordinary self-evolve runs. A typed disposition decides whether the
campaign completes, performs another candidate repair, retries infrastructure,
pauses, exhausts its budget, or hands a framework/shared blocker to the existing
Goal capability. The individual run remains the verification transaction; the
Campaign remains self-evolve-specific; Goal remains the agent-turn continuation
contract. Do not build a general workflow engine.

The minimal ownership model is:

| Layer | Owns | Must not own |
|---|---|---|
| self-evolve run | one candidate population, replay/evaluation, gates, apply/rollback, typed causal report | cross-run continuation policy |
| self-improvement Campaign | run lineage, cumulative budget, progress/frontier comparison, bounded next-run decision, resume checkpoint | arbitrary task DAGs or agent implementation details |
| Goal session | bounded agent turns for an explicitly handed-off framework/shared improvement objective | candidate repair/replay scheduling or canonical Campaign state |

This design removes manual relaunches for repairable candidate and transient
infrastructure outcomes while retaining an explicit authority boundary for
framework mutation. A framework handoff is not another candidate retry: it is a
typed, persisted Goal objective that can be imported into a Goal session and
then resume the same Campaign after the framework work is verified.

## Current state

- `aworld/self_evolve/runner.py:1111-1120` defaults one invocation to ten
  candidate iterations for `auto_verified` and one for proposal mode. This is
  the existing **within-run** improvement loop; keep it.
- `aworld/self_evolve/runner.py:2099-2139` records decisions from
  `StageAwareCandidateScheduler`, then breaks when the decision stops or has no
  slots. There is no checkpoint or caller-visible continuation disposition.
- `aworld/self_evolve/runner.py:3330-3590` builds the report, rejection
  attribution, lessons, and harness diagnostics. Harness diagnostics are added
  after the run loop has ended, so they can inform a cross-run Campaign decision
  but cannot currently schedule more work.
- `aworld/self_evolve/runner.py:6229-6337` exposes
  `optimize_from_cli_request`. `--from-run` is reserved for stored evaluator
  reruns; it is not a generic rejected-run resume path and must remain distinct.
- `aworld/self_evolve/runner.py:13312-13324` hashes the source request into a
  deterministic `cli-*` run ID. It has no Campaign generation component, so a
  repeated request cannot safely create an explicit run lineage.
- `aworld/self_evolve/budget.py` already owns `RunBudgetLedger`, typed repair
  frontiers, semantic constraint identities, and the stage-aware candidate
  scheduler. A Campaign must aggregate its serialized usage and pass only the
  remaining ceiling to a later run; it must not introduce a second token/cost
  accounting model.
- `aworld/config/conf.py:201-261` has total-run token/cost/wall-time ceilings,
  per-attempt limits, `max_iterations`, and `max_background_jobs`. It has no
  cross-run cycle bound. Add one cycle limit; do not add a knob for every
  disposition.
- `aworld/self_evolve/scheduler.py:139-168` marks a queued job `succeeded`
  whenever `run_job` returns a mapping, even when
  `framework_result["status"] == "rejected"`. The stored mapping contains the
  framework result, but drain reporting does not connect it to a continuation
  or final Campaign outcome.
- `aworld/self_evolve/diagnostics.py:270-293` already distinguishes shared or
  framework-owned causal events from candidate-owned capability failures.
  Reuse typed owner/stage/scope/repairability. Exact gate names and prose may be
  legacy fallbacks only and may never authorize continuation.
- `aworld/self_evolve/store.py:39-58` stores each run directly under
  `.aworld/self_evolve/<run_id>/`; `_write_json` writes JSON files in place.
  Campaign checkpoints need a separate
  `.aworld/self_evolve/campaigns/<campaign_id>/` namespace and atomic replacement
  so an interrupted write remains resumable.
- `aworld/self_evolve/lifecycle.py:155-162` recognizes a run directory only when
  it contains `run.json` or `report.json`. Campaigns will not accidentally look
  like runs if they stay in a named child namespace, but retention must also
  protect run IDs referenced by active or paused Campaign checkpoints.
- `aworld-cli/src/aworld_cli/builtin_plugins/goal_session/hooks/task_completed.py:56-164`
  already defines a persisted Goal contract with `objective`, `status`,
  `turn_count`, `max_turns`, verification commands, completion promise, and
  `source`. Only `active` goals continue. Reuse this shape instead of moving the
  plugin into `aworld` or inventing a Goal DAG.
- `aworld-cli/src/aworld_cli/builtin_plugins/goal_session/common.py:13-41` starts
  goals only from prompt text. It has no validated Campaign import path.
- `docs/Agents/Self Evolve.md` defines self-evolve as an agent-facing harness
  optimizer and protects framework/runtime/CLI code from candidate mutation.
  Preserve that boundary: only a Goal-authorized agent task may implement a
  framework handoff; the Campaign itself must never write protected framework
  paths.

Repository conventions to preserve:

- Use frozen dataclasses, string enums, stable lower-snake-case reason codes,
  and additive `to_json_dict` reports.
- Execution status, failure ownership, Campaign status, disposition, and Goal
  status are orthogonal fields. Do not call a rejected optimize outcome
  successful merely because the worker process returned normally.
- Aggregate normalized member data by stable semantic/constraint identity.
  One trajectory and multiple trajectories must use the same code path.
- Every continuation decision must be reconstructible from persisted public
  typed fields. Do not depend on raw prompts, private paths, or exception prose.
- Preserve strict target provenance, explicit-target behavior, replay gates,
  post-apply evaluation, rollback, and new-skill publication policy.
- Production code and tests must not branch on a historical run ID, target ID,
  case ID, fixture excerpt, exact failure sentence, or named external domain.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Campaign model/store | `python -m pytest tests/self_evolve/test_campaign.py tests/self_evolve/test_store.py tests/self_evolve/test_lifecycle.py -q` | all pass |
| Config and background scheduling | `python -m pytest tests/self_evolve/test_config.py tests/self_evolve/test_scheduler.py -q` | all pass |
| Runner continuation | `python -m pytest tests/self_evolve/test_runner.py -k "campaign or continuation or historical_feedback or repair_contract or infrastructure" -q` | all selected tests pass |
| Cardinality contract | `python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q` | all one- and multi-trajectory cells pass |
| CLI and Goal bridge | `python -m pytest tests/core/test_optimize_top_level_command.py tests/test_slash_commands.py tests/plugins/test_plugin_commands.py -k "optimize or campaign or goal" -q` | all selected tests pass |
| Self-evolve subsystem | `python -m pytest tests/self_evolve -q` | all pass |
| Syntax | `python -m py_compile aworld/self_evolve/campaign.py aworld/self_evolve/store.py aworld/self_evolve/lifecycle.py aworld/self_evolve/runner.py aworld/self_evolve/scheduler.py aworld-cli/src/aworld_cli/builtin_plugins/goal_session/common.py aworld-cli/src/aworld_cli/builtin_plugins/goal_session/hooks/stop.py aworld-cli/src/aworld_cli/builtin_plugins/goal_session/hooks/task_completed.py` | exit 0 |

## Scope

**In scope**

- `aworld/config/conf.py`
- `aworld/self_evolve/campaign.py` (create)
- `aworld/self_evolve/store.py`
- `aworld/self_evolve/lifecycle.py`
- `aworld/self_evolve/runner.py`
- `aworld/self_evolve/scheduler.py`
- `aworld/self_evolve/__init__.py`
- `aworld-cli/src/aworld_cli/commands/optimize_cmd.py`
- `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py`
- `aworld-cli/src/aworld_cli/builtin_plugins/goal_session/common.py`
- `aworld-cli/src/aworld_cli/builtin_plugins/goal_session/hooks/stop.py`
- `aworld-cli/src/aworld_cli/builtin_plugins/goal_session/hooks/task_completed.py`
- `tests/self_evolve/test_campaign.py` (create)
- `tests/self_evolve/test_config.py`
- `tests/self_evolve/test_store.py`
- `tests/self_evolve/test_lifecycle.py`
- `tests/self_evolve/test_runner.py`
- `tests/self_evolve/test_scheduler.py`
- `tests/self_evolve/test_framework_contract_matrix.py`
- `tests/core/test_optimize_top_level_command.py`
- `tests/test_slash_commands.py`
- `tests/plugins/test_plugin_commands.py`
- `docs/Agents/Self Evolve.md`
- `docs/AWorld CLI/Commands/Optimize.md`
- `docs/AWorld CLI/Plugins/Goal Session.md`
- `plans/README.md`

**Out of scope**

- Any file under `aworld-skills/` or any run-specific skill repair.
- A generic workflow engine, Campaign DSL, Goal DAG, child goals, distributed
  queue, service, daemon, or database.
- Moving the session-scoped Goal plugin into `aworld.core`.
- Allowing a Campaign to edit framework/runtime/CLI protected paths directly.
- Automatically publishing or applying a candidate after a failed gate.
- Retrying policy, provenance, target-selection, permission, evidence-quality,
  or non-repairable task failures.
- Reusing arbitrary shell commands from diagnostics as Goal verification
  commands.
- Treating a novel candidate fingerprint, another LLM response, or a higher
  attempt count as semantic progress by itself.
- Replacing `--from-run --rerun-evaluator`; stored evaluator reruns remain a
  separate lifecycle.
- A per-disposition configuration matrix. The single cycle cap plus existing
  budgets and typed stall detection are sufficient for this plan.

## Git workflow

- Branch: `codex/009-bounded-self-improvement-campaigns`
- Suggested commits:
  - `feat(self-evolve): add bounded improvement campaigns`
  - `feat(cli): bridge self-evolve campaigns into goal sessions`
- Match the repository's imperative conventional-commit style, for example
  `fix(self-evolve): merge causal repair constraints`.
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Define the minimal typed Campaign contract

Create `aworld/self_evolve/campaign.py`. Define frozen, JSON-serializable types
with `schema_version = 1`:

- `SelfImprovementCampaignStatus`: `active`, `paused`, `budget_limited`,
  `exhausted`, `complete`. `budget_limited` is reserved for cycle/resource or
  accounting ceilings; `exhausted` identifies a non-progressing typed frontier.
- `SelfImprovementDispositionKind`: `complete`, `continue_candidate`,
  `retry_infrastructure`, `handoff_goal`, `pause_operator`, `exhausted`.
- `SelfImprovementProgress`: deepest comparable stage reached, sorted unique
  semantic frontier identities, sorted unique repair-constraint identities,
  passed required-gate identities, and comparable score/verification fields
  only when the report marks them comparable.
- `SelfImprovementDisposition`: kind, stable reason code, owner/stage/scope,
  repairable flag, progress delta identities, and public diagnostic references.
- `SelfImprovementCampaign`: Campaign ID, objective, status, source-request
  fingerprint, target fingerprint, dataset/trajectory-set fingerprint,
  verification-contract fingerprint, apply policy, cycle index, maximum cycles,
  ordered run IDs, cumulative serialized usage, latest progress/disposition,
  and optional Goal handoff metadata.

Use one authoritative transition function. It must reject invalid states such
as cycle index above the cap, `complete` without a successful run,
`continue_candidate` without a repairable candidate-owned frontier,
`retry_infrastructure` for candidate ownership, or a changed source/target/
verification fingerprint. Campaign IDs must be path-safe and independent from
run IDs. Run IDs must include Campaign ID plus a monotonically increasing cycle
component so replay and reports never overwrite an earlier generation.

Do not add a generic state machine package. These types and transitions belong
to self-evolve.

**Verify**:
`python -m pytest tests/self_evolve/test_campaign.py -k "model or transition or fingerprint" -q`
→ valid transitions pass; contradictory statuses, changed contracts, unsafe
IDs, and over-budget transitions fail closed.

### Step 2: Derive continuation only from typed causal evidence

In `campaign.py`, implement a pure
`derive_self_improvement_disposition(report, previous_progress)` function. It
must run only after the final report includes gates, scheduler decisions,
causal events, lessons, and harness diagnostics. Apply this precedence:

1. A verified successful run → `complete`.
2. A typed candidate-owned, repairable semantic frontier with a new frontier or
   constraint identity, or a strictly deeper comparable verification stage →
   `continue_candidate`.
3. A typed retryable infrastructure terminal cause → `retry_infrastructure`.
4. A framework-owned or shared-run blocker → `handoff_goal`.
5. Target/provenance/policy/permission/evidence or non-repairable task ownership
   → `pause_operator`.
6. An unchanged frontier, semantic duplicate exhaustion, incomparable score-only
   movement, or no typed repairable work → `exhausted`.

The function must aggregate all normalized members before deciding. Equivalent
events/constraints deduplicate by identity; distinct member constraints remain
in the union. `occurrence_count` and dataset cardinality may affect diagnostics
and cost but not select another disposition branch. A candidate content hash or
candidate count never counts as progress.

For legacy reports that lack typed ownership, return a non-continuable
`pause_operator` with reason `legacy_report_missing_typed_disposition`. Exact
gate/reason strings may explain that result but may not authorize another run.

Attach the derived disposition additively to the completed run report as
`self_improvement_disposition`; do not replace `terminal_cause`,
`rejection_attribution`, or failed-gate reporting. The final CLI rejection line
must use this field to explain whether work will continue, pause, hand off, or
exhaust — it must not report a historical duplicate gate as the primary cause
when a substantive typed cause exists.

**Verify**:
`python -m pytest tests/self_evolve/test_campaign.py -k "disposition or progress or cardinality or legacy" -q`
→ the full precedence matrix passes for one and multiple normalized members,
including several distinct constraints in one multi-trajectory report.

### Step 3: Persist atomic Campaign checkpoints and protect their lineage

Extend `FilesystemSelfEvolveStore` with validated methods to create, read, and
replace:

```text
.aworld/self_evolve/campaigns/<campaign_id>/campaign.json
.aworld/self_evolve/campaigns/<campaign_id>/goal_handoff.json   # only when needed
```

Write Campaign JSON through a same-directory temporary file plus atomic
`os.replace`; validate the reloaded object before returning. Do not make a
partially written Campaign resumable, and do not introduce a database. Reject
symlinks, path traversal, schema-version mismatches, unknown required enum
values, missing referenced runs, and source/target/verification fingerprint
changes.

Update `lifecycle.py` so `campaigns` is explicitly excluded from run discovery.
Scan valid Campaign checkpoints for referenced run IDs and protect those runs
from cleanup while the Campaign is `active` or `paused`. A corrupt Campaign must
not cause broad retention: record a bounded diagnostic and skip only that
Campaign's references. Terminal `complete`/`budget_limited`/`exhausted`
Campaigns follow the
normal latest-run retention policy.

**Verify**:
`python -m pytest tests/self_evolve/test_store.py tests/self_evolve/test_lifecycle.py tests/self_evolve/test_campaign.py -k "campaign or atomic or retention or corrupt" -q`
→ crash-safe round trips, path rejection, active/paused lineage protection, and
terminal cleanup behavior all pass.

### Step 4: Execute a bounded cross-run improvement loop

Add a `SelfImprovementCampaignController` in `campaign.py`. Keep
`SelfEvolveRunner.run` responsible for one run. Expose one `advance_once`
primitive and one synchronous `run_bounded` wrapper that repeatedly calls it.
Top-level/slash optimize uses `run_bounded`; the background worker uses
`advance_once` and requeues. A caller must never use both drivers for the same
Campaign generation. The controller must:

1. create or validate the Campaign checkpoint;
2. compute the remaining Campaign token/cost/wall-time ceilings from the
   existing serialized run usage and original configured ceilings;
3. invoke exactly one ordinary self-evolve run for the next cycle;
4. persist the run ID and cumulative usage before evaluating continuation;
5. derive and persist the disposition after the final report is complete;
6. continue only for `continue_candidate` or `retry_infrastructure` while the
   cycle and cumulative budgets remain;
7. stop immediately for `complete`, `handoff_goal`, `pause_operator`, or
   `exhausted`.

Add only `max_improvement_cycles: int = 3` to `SelfEvolveConfig`, validate it as
positive, and forward it through CLI/background config. It is the hard cap over
all cross-run attempts, including infrastructure retries. `max_iterations`
remains the within-run candidate-population cap. Existing total-run
token/cost/wall-time ceilings become Campaign-total ceilings while a Campaign
is active; per-attempt limits remain per attempt. If required usage telemetry is
missing, complete the current run but fail closed against another cycle.
For the CLI's implicit legacy token default, derive the Campaign-total ceiling
as the unchanged per-run default multiplied by `max_improvement_cycles`; do not
silently turn the old single-run allowance into a three-run shared allowance.
Explicit ceilings remain Campaign total.

Enable the Campaign controller by default only for `auto_verified`.
`proposal` remains one run. Setting `--max-improvement-cycles 1` gives the old
cross-run behavior without a second disable flag.

When starting a later cycle, pass exact Campaign run IDs to the existing
historical feedback/semantic-dedup loader. Merge inherited and newly produced
repair contracts by existing constraint identity, preserve all distinct
schema/fixture constraints, and recompute required branches from the merged
contract. Do not scan unrelated runs or overload evaluator `from_run`.

**Verify**:
`python -m pytest tests/self_evolve/test_runner.py tests/self_evolve/test_campaign.py tests/self_evolve/test_config.py -k "campaign or cumulative_budget or repair_contract or historical_feedback" -q`
→ rejected repairable run → repaired successor → success works without manual
relaunch; unchanged frontier, missing telemetry, and cycle exhaustion stop at a
deterministic bound.

### Step 5: Connect background scheduling to the Campaign outcome

Update `SelfEvolveJobWorker` and `_run_framework_job` to call the Campaign
controller's `advance_once` path for `auto_verified`; the worker must not call
the synchronous `run_bounded` wrapper. Preserve transport execution status
separately from framework and Campaign outcomes:

- `job_execution_status`: whether the worker invocation returned or raised;
- `framework_status`: the latest run's `succeeded`/`failed`/`rejected` status;
- `campaign_status` and `self_improvement_disposition`: the continuation result.

Keep the legacy job `status` readable for compatibility, but CLI/UI summaries
must never use it as the optimize outcome. If a cycle is continuable, persist or
enqueue the next generation with Campaign ID and cycle in the job identity.
Existing duplicate-pending protection must prevent two jobs for the same
Campaign generation. `max_background_jobs` still bounds work drained in one
call; a Campaign may remain pending for a later normal drain without losing its
checkpoint.

Update the scheduler regression that currently expects a returned `rejected`
framework result to look simply `succeeded`. It should instead prove successful
worker execution plus rejected framework status plus one of the typed Campaign
outcomes. A repeated unchanged frontier must not enqueue another job.

**Verify**:
`python -m pytest tests/self_evolve/test_scheduler.py -k "campaign or rejected or drain or duplicate or infrastructure" -q`
→ background continuation is bounded, resumable, duplicate-safe, and displays
the final typed reason.

### Step 6: Add resume and concise Campaign reporting to both optimize surfaces

Add matching options to top-level `aworld-cli optimize` and slash `/optimize`:

- `--max-improvement-cycles N`
- `--resume-campaign <campaign-id>`

Starting an `auto_verified` optimize creates a Campaign unless the maximum is
one. Resume loads the persisted source, target, policy, and verification
fingerprints; callers must not be able to replace them with new source flags.
Reject ambiguous combinations and resume from `complete` or `budget_limited`.
A `paused` Campaign may resume after its Goal/operator action, but resume never
resets cycle counters, accumulated usage, or request fingerprints. Increasing a
terminal Campaign's budget requires a new Campaign rather than mutating its
history.

Both summaries must print: Campaign ID, Campaign status, cycle/max cycles,
latest run/report, disposition reason, and Goal handoff path when present. Keep
selected candidate and rejected-gate output, but label them as the latest run,
not the whole Campaign. A process exit of zero means command execution
completed; the displayed Campaign status remains the authoritative improvement
outcome.

**Verify**:
`python -m pytest tests/core/test_optimize_top_level_command.py tests/test_slash_commands.py -k "campaign or resume or optimize" -q`
→ parser forwarding, conflicting-input rejection, bounded resume, and summary
attribution pass identically for both command surfaces.

### Step 7: Bridge framework/shared blockers into the existing Goal contract

When the disposition is `handoff_goal`, write a public, bounded
`goal_handoff.json` containing:

- Campaign ID and latest run/report references;
- an objective to resolve the typed framework/shared blocker and resume this
  Campaign;
- typed owner/stage/scope, semantic frontier/constraint IDs, and public
  diagnostic references;
- the fixed next action `aworld-cli optimize --resume-campaign <id>`;
- no raw trajectory content, candidate content, secrets, or diagnostic-provided
  shell commands.

Extend the existing Goal command with:

```text
/goal --from-campaign <campaign-id> [--max-turns N]
```

Resolve the Campaign only below the current workspace's
`.aworld/self_evolve/campaigns` directory. Validate the handoff and create the
normal Goal state through `new_goal_contract_state`, with
`source="self_evolve"` plus additive `campaign_id`, `campaign_handoff_path`, and
`latest_run_id` fields. `build_goal_context_prompt` should render those fields
and the fixed resume action. Existing turn counting, pause, clear, completion
promise, error, interruption, and stop hooks remain authoritative.

Do not make Goal own or rewrite the Campaign checkpoint. Do not auto-run a
framework mutation from the background worker. If optimize already runs inside
an active Goal session, the existing `task_completed` continuation can consume
the handoff in the next turn; a standalone CLI run prints the import command.
This is the deliberate protected-code authority boundary, not a retry failure.

**Verify**:
`python -m pytest tests/plugins/test_plugin_commands.py tests/test_slash_commands.py -k "goal and campaign" -q`
→ valid Campaign handoffs create a normal active Goal; missing/corrupt IDs,
workspace escapes, prompt mixing, and terminal Campaigns fail closed; existing
plain `/goal`, status, pause, clear, and budget-limit tests still pass.

### Step 8: Prove anti-loop behavior and document the operating model

Extend `tests/self_evolve/test_framework_contract_matrix.py` with the same
Campaign scenarios for one and multiple trajectories:

- candidate-owned repairable frontier progresses and later succeeds;
- multiple distinct constraints are inherited and identity-deduplicated;
- unchanged frontier exhausts without consuming every possible LLM response;
- typed infrastructure failure retries within the same global cap;
- framework/shared failure creates one Goal handoff and no candidate retry;
- permission/policy/evidence failure pauses with no continuation;
- cumulative token/cost/wall-time denial stops before the next run;
- crash after a run report but before disposition persistence resumes exactly
  once.

Update all three docs. Explain run vs Campaign vs Goal, the default three-cycle
cap for `auto_verified`, `max_iterations` vs `max_improvement_cycles`, cumulative
budgets, disposition meanings, background checkpointing, Goal handoff/resume,
and why framework mutation is never performed by a candidate Campaign.

**Verify**:
`python -m pytest tests/self_evolve/test_framework_contract_matrix.py tests/self_evolve/test_campaign.py tests/plugins/test_plugin_commands.py -q`
→ all scenarios pass without any single-trajectory switch or named fixture.

## Test plan

- Create `tests/self_evolve/test_campaign.py` as the primary pure-contract test
  file. Use synthetic typed reports; do not copy historical report prose.
- Model persistence tests after `tests/self_evolve/test_store.py` and retention
  tests after `tests/self_evolve/test_lifecycle.py`.
- Extend the current stage-aware scheduler and causal-event fixtures instead of
  creating a parallel failure taxonomy.
- For every disposition, run the same parameterized test with one normalized
  member and several members. Add a case where several members contribute both
  equivalent and distinct schema/fixture constraints.
- Include adversarial tests for changed source/target/verification
  fingerprints, unknown schema versions/enums, symlink/path traversal,
  incomplete checkpoints, missing usage, duplicate queued generations, and
  legacy untyped reports.
- Include an integration test where the first run rejects with typed repairable
  feedback and the second succeeds. Assert the runner was invoked exactly twice
  and no operator callback was used.
- Include a stall test that generates different candidate files while retaining
  the same semantic frontier. Assert no third run occurs.
- Include a Goal import test that checks `source=self_evolve`, Campaign metadata,
  the existing max-turn behavior, and the fixed resume action.

Full verification:

```text
python -m pytest tests/self_evolve -q
python -m pytest tests/core/test_optimize_top_level_command.py tests/test_slash_commands.py tests/plugins/test_plugin_commands.py -k "optimize or campaign or goal" -q
python -m py_compile aworld/self_evolve/campaign.py aworld/self_evolve/store.py aworld/self_evolve/lifecycle.py aworld/self_evolve/runner.py aworld/self_evolve/scheduler.py aworld-cli/src/aworld_cli/builtin_plugins/goal_session/common.py aworld-cli/src/aworld_cli/builtin_plugins/goal_session/hooks/stop.py aworld-cli/src/aworld_cli/builtin_plugins/goal_session/hooks/task_completed.py
```

Expected: every command exits 0; all selected tests pass; no test or production
branch contains a historical run/target/case ID or exact rejection sentence.

## Done criteria

- [ ] `auto_verified` starts a Campaign with a default hard maximum of three
  cross-run cycles; `--max-improvement-cycles 1` preserves one-run behavior.
- [ ] Candidate-owned repairable progress and typed retryable infrastructure can
  continue without an operator relaunch.
- [ ] Policy, permission, target/provenance, evidence, non-repairable task,
  unchanged-frontier, and budget outcomes do not retry.
- [ ] Campaign token/cost/wall-time usage is cumulative and uses the existing
  ledger/report fields rather than a second accounting model.
- [ ] Every run in a Campaign has unique lineage and inherits only that
  Campaign's identity-deduplicated repair feedback/contracts.
- [ ] One and multiple trajectories use the same disposition/transition path;
  all distinct constraints survive aggregation.
- [ ] Worker execution, latest-run result, Campaign status, disposition, and
  Goal status are stored and displayed separately.
- [ ] Framework/shared failures create one validated Goal handoff and never
  trigger candidate retry or background framework mutation.
- [ ] `/goal --from-campaign` imports a validated handoff into the existing
  bounded Goal contract without making Goal the Campaign store.
- [ ] Crash/resume, duplicate generation, corrupt checkpoint, lifecycle
  retention, legacy report, and stall tests pass.
- [ ] Existing replay, trust, explicit-target, new-skill, apply/rollback, and
  post-apply gates remain unchanged and pass.
- [ ] `python -m pytest tests/self_evolve -q` passes.
- [ ] The focused CLI/Goal suite and `py_compile` commands pass.
- [ ] No files outside the in-scope list are modified.
- [ ] `plans/README.md` marks Plan 009 DONE with the implementing commit.

## STOP conditions

Stop and report back; do not improvise if:

- The live code no longer exposes typed owner/stage/scope/repairability or stable
  semantic/constraint identities needed for the disposition matrix.
- Continuing a run would require parsing exact rejection prose or making a
  historical gate name an authorization signal.
- Cumulative token/cost/wall-time usage cannot be reconstructed from the final
  report without weakening current budget enforcement.
- Campaign resume cannot validate the original source, target, dataset, and
  verification fingerprints before invoking another run.
- Goal import would require reading a Campaign outside the current workspace or
  accepting shell commands from untrusted diagnostics.
- The implementation appears to require a generic workflow engine, Goal DAG,
  new daemon/service/database, or direct background framework mutation.
- A step requires editing `aworld-skills/`, relaxing a verification gate, or
  changing explicit-target/provenance/publication trust.
- Any focused verification fails twice after a reasonable local fix.
- An in-scope file has drifted so that the stated ownership boundaries are no
  longer true.

## Maintenance notes

- Reviewers should focus on the disposition precedence, cumulative-budget
  subtraction, crash boundary between run completion and Campaign persistence,
  and whether any reason string accidentally authorizes continuation.
- Keep the default cycle count deliberately small. Raising it should require
  usage evidence, not a reaction to one rejected run.
- A future trusted autonomous framework-editing backend would require its own
  authorization and sandbox design. It may consume `goal_handoff.json`, but it
  must not be slipped into this Campaign controller.
- If Goal later gains workspace-scoped state, Campaign remains canonical;
  session Goal state should continue to store only a validated reference and
  agent-turn budget.
- If report schemas evolve, add an explicit Campaign schema migration. Never
  reinterpret an unknown typed disposition as repairable.
