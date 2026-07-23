# Optimize

`aworld-cli optimize` is the phase-1 command entrypoint for manual self-evolve runs. The CLI parses the request and prints run artifacts; `aworld.self_evolve` owns target inference, candidate generation, replay, evaluator integration, gates, release checks, and apply/rollback semantics.

Use this command to create a proposal, debug target inference, run verified replay, or drain framework-owned pending self-evolve jobs.

For normal task execution with background self-evolve enabled, use the runtime flag instead:

```bash
aworld-cli --evolve
aworld-cli --evolve=online --judge-agent ~/Documents/agent.md --judge-model-profile judge
aworld-cli run --task "complete this task" --evolve=shadow
```

`--evolve` configures the selected local runtime agent for post-run self-evolve scheduling. Pair it with `--judge-agent`, `--judge-agent-name`, or `--judge-backend-ref` to configure the background evaluator. Use `--judge-model-profile` when the evaluator should use a different named model profile from the main task agent. `aworld-cli optimize` remains the manual/debug entrypoint for the same framework runner.

## Basic Usage

Proposal-only runs:

```bash
aworld-cli optimize --target skill:demo --dataset eval.jsonl
aworld-cli optimize --target skill:login --from-trajectory trajectory.log --apply proposal
aworld-cli optimize --task "improve login retry guidance" --from-trajectory trajectory.log
aworld-cli optimize --from-trajectory trajectory.log --include-prior-runs --apply proposal
aworld-cli optimize --from-trajectory-set trajectory-set.json --include-prior-runs --apply proposal
```

Verified apply for an allowlisted skill target:

```bash
aworld-cli optimize \
  --from-trajectory ~/Documents/trajectory1.log \
  --apply auto_verified \
  --new-skill-policy auto_verified \
  --judge-agent ~/Documents/agent.md \
  --judge-timeout 600 \
  --judge-model-profile gpt-5.5
```

`--judge-model-profile` is the name of a configured CLI model profile. It does not directly set a provider model id. Add `--target skill:<name>` when you want to bypass target inference.

### Bounded self-improvement campaigns

`--apply auto_verified` starts a bounded self-improvement Campaign. A Campaign
keeps the original source, target-selection policy, verification contract,
cumulative budget, and exact run lineage. Its default maximum is three
cross-run cycles; override it with `--max-improvement-cycles N`. Set the value
to `1` to preserve single-run behavior. `--iterations` remains the separate
within-run candidate-population budget.

When the caller does not configure a token ceiling, the CLI preserves the
existing 500,000-token allowance for each permitted cycle and derives the
Campaign hard ceiling as that allowance multiplied by the cycle cap. Each run
is still capped at 500,000 tokens. An explicitly supplied
`total_run_token_budget` or legacy `max_run_tokens` remains a Campaign-total
ceiling and is reduced by every completed run's usage.

After a rejected run, the framework continues only when typed causal evidence
identifies either a newly progressed candidate-owned repair frontier or a
retryable infrastructure failure. It stops on an unchanged frontier,
non-retryable failure, policy/permission/evidence denial, or cumulative budget
exhaustion. A framework-owned or shared blocker creates a validated Goal
handoff instead of allowing a candidate to modify protected framework code.

Resume an active or paused Campaign without replacing its source, target, or
verification contract:

```bash
aworld-cli optimize --resume-campaign <campaign-id>
```

`--resume-campaign` is distinct from `--from-run --rerun-evaluator`: Campaign
resume may start the next bounded improvement run, while evaluator resume only
reuses already verified replay artifacts from one run. Complete,
budget-limited, and exhausted Campaigns cannot be resumed.

Drain pending post-run jobs:

```bash
aworld-cli optimize --drain-pending
```

Resume evaluator/gates from a previous run:

```bash
aworld-cli optimize --from-run <run_id> --rerun-evaluator
```

Evaluator-only reruns preserve the original candidate package fingerprint and therefore
cannot rebind an inferred new-skill candidate to another run-owned draft path. Rerun the
full optimize command for an inferred draft; existing-target runs can use
`--rerun-evaluator` normally.

## Data Sources

Exactly one evaluation source is normally provided:

- `--dataset <path>`: JSONL eval dataset. Rows may include `input`, `expected_output`, and `verification_command`.
- `--from-trajectory <path>`: trajectory log used to build trace packs and infer failure patterns. When the log contains multiple task trajectories, the framework automatically groups them by inferred target/task family before candidate generation so unrelated tasks do not pollute a single candidate.
- `--from-trajectory-set <path>`: advanced explicit-control input for baseline trajectory collections. Most manual optimize workflows should use `--from-trajectory`; user-authored set files may contain `baseline` and `operator_added` members only. Framework-owned accepted, rejected, and replay members are imported from self-evolve run history rather than hand-authored by users.
- `--from-session <id>`: session-backed dataset construction.
- `--batch-config <path>`: batch config for a larger request.
- `--from-run <run_id>`: previous run artifacts, usually with `--rerun-evaluator`.

When `--target` is omitted, the CLI sets `infer_target=True` and the framework performs credit assignment. The inference inventory is filtered to target types with a registered CLI adapter before scoring. Phase 1 registers the `skill` adapter only, so automatic inference cannot select an unsupported `prompt-section`, `tool-description`, `config`, or `workspace-artifact` target. Low-confidence inference remains blocked when it would mutate an existing skill. A validated capability gap may instead create an isolated run-owned draft because draft evolution does not authorize mutation of an existing target.

Skill matching ignores generic action tokens such as `search`, `read`, `open`, and
`write` when they appear only in tool names. Such tokens do not prove that an
installed skill owns the failed behavior; target selection requires a specific skill
identity, a non-generic anchored alias, or a validated capability fingerprint.

Target confidence and target provenance are independent gates. Confidence answers
whether the trajectory evidence identifies the right capability; provenance answers
whether that capability is authorized for mutation. Target intent independently records
whether the run mutates an inventory target or creates a new draft. Inferred drafts are
stored under `.aworld/self_evolve/<run_id>/draft_target/<skill_id>/SKILL.md`; they never
use another run's draft as a baseline.

`--new-skill-policy` controls inferred capability gaps:

- `disabled`: return a typed no-target result without creating a draft.
- `draft_only`: generate, replay, and evaluate a run-owned draft, but never publish it.
- `auto_verified` (default): permit publication to `aworld-skills/<skill_id>/SKILL.md`
  only after the ordinary verified gates, collision checks, and post-apply checks pass.

The apply policy is still authoritative: `--apply proposal` never publishes, regardless
of the new-skill policy. `allow_generated_target_mutation` retains its separate SDK
meaning and does not authorize new-skill publication. Adding `skill` to
`auto_apply_target_types` is also insufficient by itself.

Supplying `--target skill:<name>` is strict operator selection. If that skill does not
exist, optimize fails instead of falling back to inferred creation.

Aggregating multiple trajectories can strengthen target evidence and add evidence IDs,
but it produces one provenance decision for the selected target. Aggregation cannot
erase inventory protection or grant write trust. Behavioral replay and evaluator success
never substitute for mutation authorization. Reports record the single provenance
sidecar path, or a structured unresolved reason when classification is impossible.

## Replay Adaptation and Portability

`trajectory.log` and the other data sources are dataset inputs. They may come from the
current workspace or from a completely different rollout machine. The framework does
not execute historical file paths or compare a candidate directly with the historical
answer.

Before replay, the framework compiles each replayable case into a portable plan. It
abstracts workspace paths, snapshots bounded local inputs and non-secret environment
metadata, records external prerequisites, and creates one content-addressed workspace
seed. Baseline and candidate repetitions each start from a separate copy of that same
seed. This isolates skill changes from workspace mutations and host drift.

Multi-record trajectory logs preserve their conversation boundary by session id.
When a later record is a natural follow-up, replay receives a bounded chain of prior
user/assistant turns, with early user-provided URLs and artifact anchors protected
from eviction by long recent answers. A follow-up with no recoverable same-session
context fails adaptation as `context_incomplete` rather than entering a misleading
candidate replay.

Stateful external resources require a deterministic registered adapter. An unbound
live URL, local endpoint, stateful browser/tool name observed in the source trace,
missing continuation context, secret-like file, or unknown external path fails the
`replay_adaptation` gate before rollout. The candidate remains available in
`proposal` mode, but cannot pass `auto_verified`.

Strict baseline reuse requires the same target, current-skill fingerprint, dataset
fingerprint, adaptation fingerprint, workspace-seed fingerprint, cases, and requested
repetition count (an exact match). Legacy replay artifacts can still be opened for
inspection, but an evaluator-resume that could authorize verified apply requires adapted
replay provenance. Rerun the full optimize flow for legacy artifacts; they are not reused
as a new strict baseline.

## Candidate Generation, Repair, and Preflight

The CLI builds a typed, bounded `EvolutionContext` and runs each candidate-generation slot as an isolated AWorld task. The context contains only trainable cases, bounded trace evidence, reusable lessons, capability requirements, prior validation feedback, and acceptance constraints. Candidate output must match the framework JSON package contract; a skill candidate may contain `SKILL.md`, a bounded patch intent, and candidate-owned replay files such as `replay/capability.json`, a compiler, and a runtime.

The framework ranks the generated population and validates repair candidates before an expensive task rollout:

1. `candidate_repair_conformance` first proves that the candidate materially changed the failed source branch and that the request operation participates in the response data flow.
2. The candidate-owned replay capability is compiled and frozen with immutable fixture and package fingerprints. Recorded operation responses are exposed through `AWORLD_REPLAY_RESPONSE_INDEX`.
3. Compiled probe declarations must cover the observed operation and assert a non-empty value derived from the recorded response payload.
4. The frozen runtime is started in the replay subprocess sandbox and every declared HTTP/TCP/WebSocket readiness and protocol probe is executed. Required WebSocket probes validate handshake, ping/text exchange, operation correlation, non-empty result, and recorded-response binding.
5. Conformance always runs when a repair contract applies, including for a dataset with one replayable trajectory. Multiple trajectories are grouped by a stable semantic fingerprint covering the capability, service, operation, transport, recorded-response structure, exact probe, and assertion set. Equivalent shapes may execute once with every affected case ID recorded; every distinct shape is validated.
6. Only a candidate that passes conformance proceeds to optional representative task screening and then the authoritative paired baseline/candidate rollout. Representative screening is a cost-control signal, not evidence that unselected conformance shapes work; the final paired replay remains the authoritative dataset-wide behavioral check.

This sequence is generic: contracts are compiled from observed operations, protocol traces, fixture provenance, and the candidate package. There is no target-specific repair adapter for one trajectory case. A source-conformant candidate can still fail at runtime preflight; that result is intentionally reported before the longer task rollout and fed into the next focused repair iteration.

Candidate repair gate diagnostics are summarized in a separate population `conformance` section in `report.json`; optional task sampling remains in `screening`. Probe-group reports contain stable fingerprints, status codes, and bounded affected-case IDs, never raw recorded-response values. When execution preflight is reached, bounded service/probe artifacts are stored under `.aworld/self_evolve/<run_id>/repair_conformance/<candidate_id>/`. Increasing `--replay-timeout` affects task rollouts but does not weaken or bypass exact repair probes.

## Options

- `--agent`: agent name or id used by replay/evaluator request context.
- `--task`: task text used by framework target inference and dataset context.
- `--target`: explicit target reference. Phase 1 CLI runs support `skill:<name>` end to end. Automatic inference uses the same adapter registry and therefore considers skills only. Other target forms remain framework/SDK types until general CLI adapters are implemented.
- `--iterations`: maximum candidate optimization iterations.
- `--apply`: `proposal` or `auto_verified`. The default is `proposal`.
- `--max-improvement-cycles`: hard cross-run Campaign cap for `auto_verified`;
  defaults to `3`.
- `--resume-campaign`: resume a persisted active or paused Campaign using its
  immutable source/target/verification contract and remaining cumulative budget.
- `--new-skill-policy`: `disabled`, `draft_only`, or `auto_verified`. The default is `auto_verified`; it affects inferred missing capabilities only.
- `--judge-agent`: markdown judge agent path.
- `--judge-agent-name`: configured custom judge agent id/name.
- `--judge-backend-ref`: evaluator backend reference.
- `--judge-model-profile`: named model profile for the judge. This is useful when the task agent uses the default `.env` model but the evaluator needs a separate model. It applies to markdown judges and local named judge agents. For `--judge-agent <agent.md>`, the same profile can also be declared in markdown front matter as `model_profile: judge`; the CLI option takes precedence.
- `--replay-timeout`: timeout in seconds for each replay rollout.
- `--replay-max-runs`: maximum `aworld-cli run` iterations per replay rollout.
- `--judge-repetitions`: successful judge samples to aggregate per evaluator call.
- `--judge-timeout`: timeout in seconds for each judge attempt.
- `--baseline-replay-repetitions`: number of baseline replay rollouts.
- `--candidate-replay-repetitions`: number of candidate replay rollouts.
- `--from-run`: reuse artifacts from a previous self-evolve run.
- `--rerun-evaluator`: reuse replay artifacts from `--from-run` and rerun evaluator/gates only.
- `--include-prior-runs`: include prior self-evolve reports for the same target as advisory trainable cases. This does not replay old runs or expose raw trajectories; framework lesson memory still controls what feedback enters candidate generation.
- `--drain-pending`: drain pending framework-owned post-run self-evolve jobs.

`--apply write` and `--apply branch` are intentionally unsupported in phase 1. Proposal artifacts are written by the framework store; direct file writes and branch management are outside the CLI contract.

## Auto-Verified Defaults

When `--apply auto_verified` is used and the caller does not override values, the CLI supplies stricter defaults:

- `--judge-repetitions 1`
- `--judge-timeout 120`
- `--baseline-replay-repetitions 2`
- `--candidate-replay-repetitions 3`
- `--iterations 10`

Proposal runs default to one iteration. `auto_verified` uses the larger budget because each verified runtime repair can expose a new protocol frontier. The runner stops early when verification succeeds or progress stalls, and it may grant up to six bounded extension iterations only for newly observed repairable failure families. Duplicate candidates and repeated failure families do not consume unbounded retries.

The CLI also enables framework replay for `auto_verified`. Skill candidates must have candidate replay evidence, evaluator evidence, deterministic or objective verification signals, passing gates, and a post-apply runtime-loader check before they can remain applied.

For a single original trajectory, the default two baseline and three candidate repetitions
form a strict sparse-data verification path. A stable native task/candidate failure across
all baseline repetitions is a conclusive negative control when the candidate succeeds in
all three repetitions and evaluation supplies a deterministic signal. Infrastructure,
blocked, not-run, mixed, and inconsistent baseline outcomes remain inconclusive. A
multi-trajectory set is validated by its distinct members; repetition counts never replace
independent member coverage.

## Stage-Aware Budgets and Candidate Lifecycle

`SelfEvolveConfig.total_run_token_budget` is the authoritative token ceiling for a
complete optimize run. `per_attempt_replay_token_limit` is a separate hard ceiling
for one replay attempt; it does not divide or replace the total-run budget. The same
run ledger can enforce `max_run_cost_usd` and `max_run_wall_seconds` when those
ceilings are configured.

Before an expensive stage starts, the framework reserves its estimated token, cost,
and wall usage. Completion debits observed usage and releases the reservation.
Observed samples then replace cold-start assumptions with a conservative robust
estimate for later units. An unknown token estimate is not zero: if a token ceiling
is active, the budget gate denies the work until the backend proves zero usage or a
positive cold estimate is configured. The per-unit cold-start knobs are:

- `candidate_generation_tokens_per_unit`
- `candidate_screening_tokens_per_unit`
- `replay_tokens_per_unit`
- `evaluation_tokens_per_unit`

Each has optional matching `_cost_usd_per_unit` and `_wall_seconds_per_unit` fields.
Token cold estimates must be positive; cost and wall estimates may be zero but cannot
be negative.

Budget units follow actual workload cardinality rather than the number of input
files. Generation and local stages count per candidate attempt. Conformance counts
distinct executable contract shapes, so equivalent cases share one unit while
different response/probe shapes remain separate. Representative screening uses at
most one case per candidate. Paired replay, evaluation, and judge usage scale with
`case_count * repetitions`; therefore a three-trajectory dataset cannot be priced as
a one-trajectory run.

Every candidate slot has an explicit funnel record:
`generated -> unique` (or `duplicate_filtered`) -> local gates -> adaptation ->
repair conformance -> representative screening -> paired replay started/completed/
comparable -> evaluation -> `selected`, `rejected`, `blocked`, or `not_run`.
Terminal reasons and observed usage remain attached to the attempt, including work
that was denied before replay.

The scheduler begins with bounded exploration. After a candidate-owned repairable
failure establishes a semantic frontier, the next iteration reserves one focused
repair slot for that frontier. It may add one diverse exploration slot only for a new
frontier and only when a separate reservation is available. Repeated failures do not
silently recreate a full population.

`max_run_tokens` remains readable for compatibility. When the new fields are
omitted, both `SelfEvolveConfig` and direct `SelfEvolveRunner` construction map the
legacy value to `total_run_token_budget` and `per_attempt_replay_token_limit`.
Reports expose those decisions as `max_run_tokens_to_total_run_token_budget` and
`max_run_tokens_to_per_attempt_replay_token_limit` in
`deprecated_config_mappings`. New callers should set both fields directly; these
mappings are deprecated compatibility paths, not additional independent budgets.

## Output

The command prints the stable artifact paths returned by the framework:

```text
Optimize run submitted.
Status: succeeded
Campaign: campaign-...
Campaign status: complete
Campaign cycle: 2/3
Self-improvement: complete (verified_run_succeeded)
Report: .aworld/self_evolve/<run_id>/report.json
Target selection: .aworld/self_evolve/<run_id>/target_selection.json
Replay: .aworld/self_evolve/<run_id>/replay.json
Evaluator report: .aworld/self_evolve/<run_id>/evaluator_report.json
Best candidate: cand-1
```

For rejected runs, the summary includes rejected gate names. If no candidate was produced, replay/evaluation/apply are skipped. If judge evaluation timed out after replay completed, the summary prints a resume command:

```text
Resume evaluator: aworld-cli optimize --from-run <run_id> --rerun-evaluator
```

For Campaign runs, `Status` is the latest ordinary run result and
`Campaign status` is the authoritative cross-run outcome. `active` means
another bounded generation is eligible, `paused` requires an operator or Goal
handoff, `budget_limited` means a cycle/resource/telemetry ceiling stopped the
Campaign, `exhausted` means its typed repair frontier stopped progressing, and
`complete` means the latest run passed the unchanged verified-apply gates. All
three terminal states are non-resumable. Worker execution success is
reported separately and never converts a rejected framework result into
success.

If replay repetitions are missing, rerun full optimize with a larger `--replay-timeout`; evaluator-only resume cannot add new replay rollouts.

For an `auto_verified` release, treat the run as successful only when `report.json` has `status: "succeeded"`, no blocking failed gate, a selected candidate, and `post_apply.status: "accepted"` with `release_state: "verified"`. `status: "rejected"`, `post_apply.status: "rolled_back"`, or a missing post-apply record is not a successful verified release even if candidate generation or source conformance passed.

## Report Files

Open `.aworld/self_evolve/<run_id>/report.json` for the release-facing result:

- `status`: `succeeded`, `rejected`, or `failed`.
- `apply_policy`: `proposal` or `auto_verified`.
- `selected_candidate_id`: selected candidate when one exists.
- `gate_results`: low-level gate decisions.
- `target_provenance`: resolved sidecar path and reason, or an explicit unresolved status.
- `release_checklist`: grouped release checks derived from gates.
- `content_quality_diagnostics`: non-blocking publication/content quality diagnostics when evaluator metrics provide them.
- `population`: generated candidates, representative screening attempts, selection reason, focused repair telemetry, concurrency, and candidate-generation token usage.
- `optimizer_lineage`: candidate lineage artifact links, including content, semantic, and lesson-set fingerprints when available.
- `lessons`: normalized lesson artifact links and counts.
- `harness_diagnostics`: advisory framework diagnostic artifact links and counts.
- `release_normalization`: normalized release fingerprint, pre-normalization fingerprint, preserved runtime constraints, and verification status.
- `replay_path`: candidate replay artifact path.
- `replay.adaptation`: adaptation readiness plus workspace, environment, task, dataset, and current-skill fingerprints for completed paired replay.
- `evaluator_report_paths`: evaluator output artifacts.
- `post_apply`: accepted or rolled-back apply details, backup path, journal path, runtime activation, and registry refresh status.
- `artifact_retention`: merged startup/terminal cleanup result, including policy, protected runs, skipped runs, and removed paths.

Candidate files and diffs live under `.aworld/self_evolve/<run_id>/candidates/`. For skill candidates, proposal content is marked with `self_evolve.release_state: candidate`; verified content is marked with `self_evolve.release_state: verified` only after post-apply checks pass. `<candidate_id>.json` is the durable normalized record; `<candidate_id>/` is the expanded multi-file materialization. After a run leaves the complete-artifact retention window, redundant Markdown, diff, and expanded package copies for unselected candidates may be reclaimed while selected, applied, and lineage-parent candidates remain intact.

Repair preflight artifacts live under `.aworld/self_evolve/<run_id>/repair_conformance/<candidate_id>/`. They contain bounded frozen-capability and service/probe diagnostics for failures that occurred before task rollout.

Replay adaptation artifacts live under
`.aworld/self_evolve/<run_id>/replay_adaptation/<dataset_fingerprint>/`. The directory
contains `bundle.json`, `workspace_manifest.json`, `environment_snapshot.json`, and
`workspace_seed/`. Each executed repetition stores its disposable workspace under the
corresponding replay repetition directory.

Replay lifecycle records are cardinality-neutral: one task produces one member and a
trajectory set produces one member per replayable task, always in dataset order. New
artifacts use `members/manifest.json` v2 and a `lifecycle.json` v2 for every baseline
and candidate variant, including work that did not execute:

- `succeeded`: execution started and completed with usable trajectory evidence.
- `failed`: execution started and produced a typed failure event.
- `blocked`: execution did not start because the event in `blocked_by` prevented it.
- `not_run`: the framework intentionally did not schedule the variant.

A failure event records `owner` (`candidate`, `task`, `infrastructure`, or
`framework`), `stage`, `scope`, a stable code, and repairability independently from
execution status. For example, if a candidate capability fails preflight on the first
member of a three-member dataset, that baseline member is `failed` with
`owner: candidate`, `stage: capability_preflight`, and `scope: candidate`; all three
candidate variants and the remaining baseline variants are `blocked` by the same
event. The next candidate in the population is still evaluated. Only an explicit
native `shared_run` event owned by infrastructure or the framework stops the population.
Legacy status and failure files remain readable, but unknown legacy failures are not
promoted to run-wide infrastructure failures.

Member manifests fail closed when members are missing, duplicated, unexpected, empty,
or carry a request that changes root-level run, candidate, target, overlay, agent, or
provenance fields. Task identity/input, task-input fingerprint, member baseline path,
and repetition counts must exactly match the values deterministically derived for that
dataset member; every other request field must equal the root request. Numeric
repetition child directories are reconstructed when lifecycle v2 artifacts are loaded,
so paired evaluation has the same repetition cardinality before and after persistence.

Artifact GC runs at optimize startup and terminal completion. It preserves the two
newest runs, lineage and apply recovery dependencies, and runs with a live process
lease. Raw replay, replay-adaptation, repair-conformance, evaluator, overlay, and
temporary-workspace artifacts from older eligible runs are reclaimed.

The default store is `<workspace>/.aworld/self_evolve`. The framework does not create or manage a top-level `self_evolve_artifacts/` directory.

## Runtime Skill Management

Verified skill candidates are made visible to runtime discovery only after the framework marks them verified and the CLI activation hook enables the skill. Draft and candidate skills remain hidden.

Useful commands:

```bash
aworld-cli skill list
aworld-cli skill disable <skill-name>
aworld-cli skill enable <skill-name>
aworld-cli skill remove <skill-name>
```

`skill remove` can remove a runtime skill directory when it is discovered from a local runtime skill source and is not a symlink or package install. If an installed package with the same id exists, package removal takes precedence.

## Boundaries

The CLI does not score targets itself. `--task` without `--target` delegates target selection to framework credit assignment after the framework filters the inventory through the CLI adapter registry. This prevents an unsupported target type from winning inference. An explicitly requested unsupported target is rejected; adding a general tested adapter is the extension path, not adding a trajectory-specific exception.

The CLI also does not own scheduler behavior, evaluator behavior, optimizer semantics, durable job formats, or agent opt-in configuration. Configure opt-in with `AgentConfig.self_evolve_config`, then use this command as a manual/debug entrypoint for the same framework APIs.

See [Self Evolve](../../Agents/Self%20Evolve.md) for the framework design, safety model, SDK example, and release checklist details.
