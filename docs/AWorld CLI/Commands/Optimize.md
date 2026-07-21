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
  --target skill:login \
  --from-trajectory trajectory.log \
  --apply auto_verified \
  --judge-agent judges/login_quality.md \
  --judge-model-profile judge \
  --replay-timeout 900
```

Drain pending post-run jobs:

```bash
aworld-cli optimize --drain-pending
```

Resume evaluator/gates from a previous run:

```bash
aworld-cli optimize --from-run <run_id> --rerun-evaluator
```

## Data Sources

Exactly one evaluation source is normally provided:

- `--dataset <path>`: JSONL eval dataset. Rows may include `input`, `expected_output`, and `verification_command`.
- `--from-trajectory <path>`: trajectory log used to build trace packs and infer failure patterns. When the log contains multiple task trajectories, the framework automatically groups them by inferred target/task family before candidate generation so unrelated tasks do not pollute a single candidate.
- `--from-trajectory-set <path>`: advanced explicit-control input for baseline trajectory collections. Most manual optimize workflows should use `--from-trajectory`; user-authored set files may contain `baseline` and `operator_added` members only. Framework-owned accepted, rejected, and replay members are imported from self-evolve run history rather than hand-authored by users.
- `--from-session <id>`: session-backed dataset construction.
- `--batch-config <path>`: batch config for a larger request.
- `--from-run <run_id>`: previous run artifacts, usually with `--rerun-evaluator`.

When `--target` is omitted, the CLI sets `infer_target=True` and the framework performs credit assignment. Low-confidence inferred targets are blocked for `auto_verified` apply.

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

## Options

- `--agent`: agent name or id used by replay/evaluator request context.
- `--task`: task text used by framework target inference and dataset context.
- `--target`: explicit target reference. Phase 1 CLI runs support `skill:<name>` end to end. Other target forms are framework-level roadmap types until their CLI adapters are implemented.
- `--iterations`: maximum candidate optimization iterations.
- `--apply`: `proposal` or `auto_verified`. The default is `proposal`.
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
- `--iterations 1`

The CLI also enables framework replay for `auto_verified`. Skill candidates must have candidate replay evidence, evaluator evidence, deterministic or objective verification signals, passing gates, and a post-apply runtime-loader check before they can remain applied.

## Output

The command prints the stable artifact paths returned by the framework:

```text
Optimize run submitted.
Status: succeeded
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

If replay repetitions are missing, rerun full optimize with a larger `--replay-timeout`; evaluator-only resume cannot add new replay rollouts.

## Report Files

Open `.aworld/self_evolve/<run_id>/report.json` for the release-facing result:

- `status`: `succeeded`, `rejected`, or `failed`.
- `apply_policy`: `proposal` or `auto_verified`.
- `selected_candidate_id`: selected candidate when one exists.
- `gate_results`: low-level gate decisions.
- `release_checklist`: grouped release checks derived from gates.
- `content_quality_diagnostics`: non-blocking publication/content quality diagnostics when evaluator metrics provide them.
- `optimizer_lineage`: candidate lineage artifact links, including content, semantic, and lesson-set fingerprints when available.
- `lessons`: normalized lesson artifact links and counts.
- `harness_diagnostics`: advisory framework diagnostic artifact links and counts.
- `release_normalization`: normalized release fingerprint, pre-normalization fingerprint, preserved runtime constraints, and verification status.
- `replay_path`: candidate replay artifact path.
- `replay.adaptation`: adaptation readiness plus workspace, environment, task, dataset, and current-skill fingerprints for completed paired replay.
- `evaluator_report_paths`: evaluator output artifacts.
- `post_apply`: accepted or rolled-back apply details, backup path, journal path, runtime activation, and registry refresh status.

Candidate files and diffs live under `.aworld/self_evolve/<run_id>/candidates/`. For skill candidates, proposal content is marked with `self_evolve.release_state: candidate`; verified content is marked with `self_evolve.release_state: verified` only after post-apply checks pass. Candidate JSON records are durable. After a run leaves the complete-artifact retention window, redundant Markdown, diff, and expanded package copies for unselected candidates may be reclaimed while selected, applied, and lineage-parent candidates remain intact.

Replay adaptation artifacts live under
`.aworld/self_evolve/<run_id>/replay_adaptation/<dataset_fingerprint>/`. The directory
contains `bundle.json`, `workspace_manifest.json`, `environment_snapshot.json`, and
`workspace_seed/`. Each executed repetition stores its disposable workspace under the
corresponding replay repetition directory.

Artifact GC runs at optimize startup and terminal completion. It preserves the two
newest runs, lineage and apply recovery dependencies, and runs with a live process
lease. Raw replay, replay-adaptation, repair-conformance, evaluator, overlay, and
temporary-workspace artifacts from older eligible runs are reclaimed.

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

The CLI does not infer targets itself. `--task` without `--target` delegates target selection to framework credit assignment. If inference selects a target type without a phase-1 CLI adapter, the request is persisted as an unsupported-target result rather than applied.

The CLI also does not own scheduler behavior, evaluator behavior, optimizer semantics, durable job formats, or agent opt-in configuration. Configure opt-in with `AgentConfig.self_evolve_config`, then use this command as a manual/debug entrypoint for the same framework APIs.

See [Self Evolve](../../Agents/Self%20Evolve.md) for the framework design, safety model, SDK example, and release checklist details.
