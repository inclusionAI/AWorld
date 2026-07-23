# Self Evolve

Self-evolve is a framework-owned capability for improving agent-facing harness artifacts from observed task trajectories. Phase 1 is intentionally narrow: it proposes and verifies changes to skills and other harness surfaces. A skill candidate may be a multi-file package containing `SKILL.md` plus skill-owned replay compiler/runtime files, but self-evolve does not mutate AWorld framework/runtime/CLI product code, dependency manifests, secrets, or unrelated repository logic.

The capability is disabled by default. Agents opt in through `AgentConfig.self_evolve_config`, `aworld-cli --evolve` can enable it for the current CLI runtime session, and `aworld-cli optimize` provides an explicit manual/debug entrypoint for release operators and developers.

For verified optimization, three small ownership layers cooperate:

| Layer | Responsibility |
|---|---|
| Run | Generate/evaluate one candidate population and preserve all gates, apply, rollback, and report semantics. |
| Campaign | Compare typed causal progress across an exact bounded run lineage and account for cumulative budget. |
| Goal session | Accept an explicit framework/shared-code handoff; it does not own candidate scheduling or Campaign state. |

The Campaign is not a general workflow engine and does not retry every
rejection. It is the self-improvement link between ordinary runs: only typed
candidate progress or retryable infrastructure can authorize another cycle.
Framework mutation remains behind the existing Goal authority boundary.

## What It Optimizes

Self-evolve works on target references rather than arbitrary files. A target records the artifact type, id, optional path, and provenance used by the framework gates.

The framework target model represents several artifact types, but target representation and CLI execution support are separate:

- `skill:<name>`: available end to end for proposal runs and verified apply when the target is allowlisted.
- `workspace-artifact:<path>`, `prompt-section:<id>`, `tool-description:<id>`, and `config:<id>`: available as framework/SDK target types, but do not currently have phase-1 `aworld-cli optimize` adapters.

Automatic CLI target inference is adapter-aware. Before credit assignment, the framework filters the target inventory to target types with a registered CLI adapter. With the phase-1 registry, inference considers skill targets only; it cannot select an unsupported target such as `prompt-section:result-validation-anchor-policy`. This is a capability-level rule rather than a special case for one trajectory. An explicitly requested unsupported CLI target is rejected instead of being silently rewritten to another type.

Protected areas include AWorld framework/runtime/CLI code, dependency files, package metadata, and protected built-in skills such as `app_evaluator` and `self_evolve`.

## Design

Self-evolve follows a proposal-first pipeline:

<pre class="mermaid">
flowchart TD
    A["Trajectory / Evaluation Evidence"] --> B["Dataset Recipe&lt;br/&gt;Trainable + Held-out"]
    B --> C["Adapter-aware Target Selection"]
    C --> D["Bounded EvolutionContext"]
    D --> E["CandidateGenerationAgent&lt;br/&gt;Candidate Population"]

    E --> F["Shape / Package / Provenance Gates"]
    F -->|Repairable candidate failure| R["Focused Candidate Repair"]
    F -->|Non-repairable rejection| P["Preserve Proposal and Diagnostics"]
    R --> E

    F -->|Pass| G["Replay Adaptation"]
    G -->|Unresolved dependency| P
    G -->|Ready| H["Source Conformance"]
    H -->|Fail| R
    H -->|Pass| I["Compile and Freeze Capability"]
    I -->|Fail| R
    I -->|Pass| J["Exact Protocol Preflight&lt;br/&gt;HTTP / TCP / WebSocket"]
    J -->|Fail| R

    J -->|Pass| K["Representative Screening"]
    K --> L["Authoritative Paired Replay&lt;br/&gt;Baseline vs Candidate"]
    L --> M["Judge / Held-out / Regression Gates"]
    M -->|Repairable candidate failure| R
    M -->|Rejected| P
    M -->|Pass| N{"Apply Policy"}

    N -->|proposal| P
    N -->|auto_verified| O["Backup and Apply"]
    O --> Q["Runtime-loader Verification"]
    Q -->|Fail| S["Rollback"]
    Q -->|Pass| T["Verified Release&lt;br/&gt;status: succeeded"]
</pre>

The loop makes three boundaries visible: target selection is limited by registered adapters, inexpensive candidate/protocol checks run before authoritative rollout, and only a fully verified `auto_verified` candidate can modify the target. Proposal preservation and rollback remain valid terminal outcomes rather than being treated as successful releases.

1. Collect trajectory or evaluation evidence from a current run, trajectory log, session, JSONL dataset, or batch config.
2. Build a dataset recipe with trainable and held-out cases. Candidate optimizers see trainable evidence only.
3. Select a target explicitly from `--target` or infer one through adapter-filtered framework credit assignment.
4. Build a typed, bounded `EvolutionContext` containing target state, trainable evidence, reusable lessons, capability requirements, prior validation feedback, and acceptance constraints.
5. Generate and normalize a population of candidate variants with an AWorld-native `CandidateGenerationAgent`; skill candidates may use a bounded patch intent or a multi-file package.
6. Preserve candidate JSON, materialized content, diffs, lineage, and diagnostics under `.aworld/self_evolve/<run_id>/`.
7. Run candidate shape, source conformance, provenance, budget, replay adaptation, protocol preflight, paired replay, evaluator, and regression gates.
8. Feed machine-readable candidate failures back into a focused repair iteration without treating the model rationale as proof of a fix.
9. Keep the result as a proposal, or, for `auto_verified`, apply only after verification passes.
10. Re-evaluate the applied artifact through the runtime loader and roll back if post-apply verification fails.

This keeps the self-evolve loop outside the task response path. Post-run scheduling is best effort: a failed enqueue is logged and does not change the completed `TaskResponse`.

### Cross-run Campaign loop

An `auto_verified` CLI or online background optimize starts a Campaign with a
default maximum of three cycles. Each cycle is still an ordinary self-evolve
run with a unique `campaign-...-cycle-NNN` ID. A later cycle inherits repair
feedback only from earlier run IDs in that Campaign, deduplicates semantic and
schema/fixture constraint identities, and verifies that the inferred target
type/id has not changed. One-trajectory and multi-trajectory datasets use the
same aggregate transition function; member count never selects a retry branch.

The Campaign persists atomically under
`.aworld/self_evolve/campaigns/<campaign-id>/campaign.json`. It fingerprints
the source contents and verification contract, protects active/paused run
lineage from artifact cleanup, and subtracts each report's token, cost, and
wall-time ledger from the original ceiling. An explicit budget is Campaign
total. If the CLI caller leaves the token ceiling implicit, Campaign creation
derives a hard total of 500,000 tokens per allowed cycle while retaining the
500,000-token cap on each individual run. Missing usage telemetry fails closed
before another run. `max_iterations` limits candidate work inside a run;
`max_improvement_cycles` limits runs across the Campaign.

The possible dispositions are `complete`, `continue_candidate`,
`retry_infrastructure`, `handoff_goal`, `pause_operator`, and `exhausted`.
Unchanged semantic frontiers, non-repairable task/candidate failures, policy or
permission denials, and exhausted budgets do not loop. A framework/shared
handoff writes only public typed references and the fixed resume action; it
never lets a candidate write framework/runtime/CLI paths.

Campaign status distinguishes why bounded improvement stopped: `budget_limited`
is reserved for cycle/resource/accounting limits, while `exhausted` means a
typed repair frontier repeated without deeper stage or new constraint progress.
Both are terminal; neither is reported as a successful evolution.

### Typed recovery trace

Self-improvement progress is not limited to a new failure code. The framework
builds `aworld.self_evolve.recovery_trace.public.v1` from historical trajectory
steps and paired replay results. For each trajectory-set member it compares
baseline and candidate repetition success rates, distinguishes stable recovery,
partial recovery, regression, and unrecovered behavior, and records only
structural path data such as step/tool counts and strategy-switch counts.
Member ids are SHA-256 identities; task text, tool arguments, endpoints, and
response payloads are never included.

An intermediate failure followed by terminal success produces a recovery
lesson instead of a failure lesson. During paired replay, successful member
paths become last-good structural checkpoints. If a failed repetition proceeds
beyond a successful checkpoint without completing, the next repair receives a
bounded post-checkpoint-overrun signal and must preserve positive recovery
deltas while repairing the remaining members. The same aggregation applies to
one or many trajectory members.

Campaign progress includes monotonic per-member recovery achievements. A higher
successful-repetition count or newly stable recovery can authorize the next
bounded cycle even when the causal failure identity is unchanged; an unchanged
recovery frontier still exhausts normally. The recovery trace schema is also
part of the versioned verification-contract fingerprint, so historical semantic
deduplication cannot conflate candidates verified before and after this contract.

Candidate conformance has a parallel identity-only constraint recovery trace.
It records whether each typed constraint is active, recovered, or regressed,
plus bounded violation/recovery counts. Repeated violations require a materially
different implementation strategy; recovered constraints form a last-good
checkpoint that later repairs must preserve. The trace contains constraint
hashes only and works across arbitrary candidate populations and trajectory-set
cardinality.

Recovery attribution also has an intervention boundary. Framework startup and
protocol probes establish that a candidate runtime is valid, but they are cleared
from the task-plane trace before rollout. A replay-only candidate is considered a
repair cause only when the task actually exchanges traffic with that intervention.
Otherwise the typed cause is `candidate_intervention_unobserved`, owned by replay
adaptation/target selection, and the framework must repair context or targeting
instead of repeatedly mutating the candidate.

### Candidate Generation and Focused Repair

Each candidate-generation slot runs as an isolated AWorld task with one model call, no tools, model-aware input/output budgeting, and a typed JSON output contract. Candidate packages are normalized before they enter the population. Malformed but repairable model output receives one bounded representation-repair attempt; provider/runtime failures remain infrastructure failures and are not misclassified as bad candidates.

The default proposal budget is one optimizer iteration. `auto_verified` defaults to ten iterations because runtime-backed repairs often expose the next protocol boundary only after the previous one is corrected. The runner stops when it has a verified candidate or no new progress is possible. It may grant a bounded extension for a newly observed repairable failure family, up to six extension iterations, rather than looping on duplicate candidates or the same failed branch.

Once a concrete candidate has failed, the next repair prompt switches to `focused_candidate_delta` mode. It includes the failed candidate package, bounded machine-readable diagnostics, the relevant source branches, and the repair acceptance contract. Broad trajectory, lesson, and current-target payloads are omitted from that repair prompt so the model edits the observed failure frontier instead of regenerating the whole skill.

Compiler and replay-capability failures retain the complete capability authoring
contract even when no schema-field constraint has been emitted yet. The compiler is
explicitly a deterministic, network-disabled artifact transform: evidence references
are string keys resolved through `evidence_derivations`, and runtime listeners or
socket probes are created only later by the framework with allocated ports.

Schema-field repair constraints carry a value domain. `schema_value` paths are
absolute paths in the named generated schema layer. `source_behavior` paths are
static-analyzer predicates over a required source branch; candidates must
implement the expected behavior and must not manufacture or overwrite the path
as runtime data. Constraint identity includes this domain, so inherited
contracts cannot conflate a JSON field with a source-code behavior predicate.
Source-behavior constraints may also declare `required_operations` and
`forbidden_operations`. These are conjunctive structural data-flow requirements,
not strings that can be copied into comments or metadata, and are merged by the
same constraint identity rules as ordinary schema fields. Binding operations
require statically provable value flow through direct use or explicit function
parameters; direct field-projection operations cannot be satisfied by generic
recursive fallback logic.
Local deterministic conformance has a complete budget estimate of zero model
tokens/cost plus bounded wall time, so a token ceiling cannot incorrectly deny
the probe as unknown work.

### Layered Repair Conformance

For a repairable skill-owned replay failure, the framework validates a candidate in layers before paying for an authoritative task rollout:

1. **Source conformance** requires a material change to the failed compiler/runtime branch. It rejects rationale-only changes, deleted handlers, global fixture fallbacks, request-independent responses, response-index metadata traversal, and compiler/runtime selector drift.
2. **Compile and freeze** rebuilds the candidate-owned replay capability, verifies fixture provenance and immutable package fingerprints, and constructs an operation-indexed recorded response map exposed to the runtime as `AWORLD_REPLAY_RESPONSE_INDEX`.
3. **Probe conformance** requires the declared HTTP/TCP/WebSocket probe to cover the observed operation and to assert a non-empty scalar derived from the recorded response payload, not a mapping key, request token, placeholder, hash, or control-plane handshake.
4. **Execution preflight** starts the frozen service in the replay subprocess sandbox and executes every declared readiness/protocol probe. WebSocket probes validate the upgrade, ping/text exchange, operation correlation, non-empty result, and recorded-response binding when required.
5. **Representative screening** runs one bounded baseline/candidate pair only after conformance passes. Screening is a cost filter, not acceptance evidence; an inconclusive baseline preserves the ranked population for authoritative replay.

Failures in the first four layers are reported through the `candidate_repair_conformance` gate. When execution preflight is reached, bounded service/probe artifacts are stored under `repair_conformance/<candidate_id>/`. The failure becomes generic repair feedback for the next iteration and does not enter the full task rollout. The contracts are derived from observed operations, package structure, fixture provenance, and protocol traces; they do not contain target-specific fixes for a particular training case.

### Three-Trajectory Replay Model

A trajectory dataset is historical evidence, not an executable copy of its source
machine. Self-evolve uses three distinct trajectories:

1. The source trajectory supplies candidate-generation evidence and the semantic
   task anchor.
2. The replay baseline runs the current skill in a newly adapted environment.
3. The replay candidate runs the proposed skill from the same adapted initial state.

Only the paired replay baseline/candidate comparison is used to attribute an
improvement to the skill change. The historical trajectory is not substituted for a
missing replay baseline.

Before `build_replay_request()`, `ReplayAdaptationCompiler` rewrites workspace paths
to `${AWORLD_REPLAY_WORKSPACE}`, creates a filtered workspace seed and environment
snapshot, fingerprints both, and classifies external dependencies. Explicit bounded
local files may be copied into the seed. Live HTTP resources, local endpoints,
continuation context, stateful browser/tool names observed in the source trace,
authenticated profiles, and other stateful
dependencies require a registered deterministic adapter; the compiler never invents
a successful mock for an unknown dependency.

For trajectory logs, recorded message history and explicit parent ids take priority.
When log writers persist only the current message, ordered records with the same
session id are reconstructed as a bounded conversation chain. The compactor reserves
space for user turns across the chain before recent assistant output, preserving
source URLs, artifact identities, and other early task anchors needed by later natural
follow-ups. Natural continuation wording without a reconstructed same-session parent
is marked `context_incomplete`; it is never joined to an unrelated adjacent session.

Every baseline, candidate, and repetition receives a fresh copy of the same verified
seed as its working directory. Writes from one rollout therefore cannot change the
initial state of another rollout. A pair is comparable only when its adaptation,
workspace-seed, task-input, dataset, and baseline-skill provenance agree. Cached
baselines are reused only when target identity and all relevant fingerprints still
match and the stored successful repetition count is exactly the requested count;
older replay artifacts without provenance remain readable but are not reused and cannot
resume an evaluator path that authorizes verified apply.

Unresolved adaptation produces a failed `replay_adaptation` gate before a rollout is
started. Proposal mode still preserves the generated candidate and diagnostics.
`auto_verified` rejects the candidate because deterministic paired evidence is
missing.

## Configuration

`SelfEvolveConfig.mode` controls whether the runner schedules self-evolve work:

```python
from aworld.config.conf import AgentConfig, SelfEvolveConfig

agent_config = AgentConfig(
    self_evolve_config=SelfEvolveConfig(
        mode="shadow",
        apply_policy="proposal",
        min_eval_cases=1,
        max_improvement_cycles=3,
    )
)
```

Modes:

- `off`: default; no post-run self-evolve scheduling.
- `offline`: manual SDK/CLI runs only.
- `shadow`: post-run jobs may be enqueued, but candidates remain proposals.
- `online`: post-run jobs may apply allowlisted targets only after verified replay, evaluator gates, and post-apply re-evaluation.

`online` requires `apply_policy="auto_verified"`. `auto_verified` also requires `requires_post_apply_reevaluation=True`, which is the default. Useful verification knobs include `replay_timeout_seconds`, `replay_max_steps`, `baseline_replay_repetitions`, `candidate_replay_repetitions`, `replay_candidate_limit`, `replay_stability_margin`, `judge_repetitions`, and `judge_timeout_seconds`.

`max_improvement_cycles` is the cross-run hard cap and defaults to `3`.
`max_background_jobs` still limits how many queued generations one drain call
executes; a continuable Campaign remains checkpointed for a later drain.

Self-evolve is separate from older learning switches. `meta_learning_config` stores and extracts learning knowledge, `ContextRuleConfig.optimization_config` controls context compression/optimization behavior, and `train.evolve` is a training asset. `SelfEvolveConfig` is the opt-in for this framework proposal and verification loop.

## Runtime CLI Opt-In

Use `--evolve` when running the main CLI agent and you want completed tasks to enqueue background self-evolve work:

```bash
aworld-cli --evolve
aworld-cli --evolve=online --judge-agent ~/Documents/agent.md --judge-model-profile judge
aworld-cli run --task "summarize this page" --evolve=shadow
```

`--evolve` is equivalent to `--evolve=shadow`: the current runtime session writes proposal artifacts only. `--evolve=online` injects `SelfEvolveConfig(mode="online", apply_policy="auto_verified")` into the selected local agent, so background jobs may apply allowlisted targets only after verified replay, evaluator gates, and post-apply runtime-loader checks pass.

Use `--judge-agent`, `--judge-agent-name`, or `--judge-backend-ref` with `--evolve` to configure the evaluator used by background jobs. These selectors map to `SelfEvolveConfig.judge_config` and follow the same mutually exclusive semantics as `aworld-cli optimize`.

Use `--judge-model-profile <name>` when the judge should run on a different named model profile from the main CLI agent. For markdown judges, `agent.md` may also declare the profile in front matter:

```markdown
---
name: trajectory-evaluator
model_profile: judge
---
```

The profile is resolved by the CLI model profile loader and does not change the main task agent's `.env` model settings. An explicit `--judge-model-profile` value takes precedence over the markdown front matter. For `--judge-agent-name`, the profile is applied to local named judge agents whose runtime config is accessible; remote or opaque executors keep their own model configuration.

Example CLI config shape:

```json
{
  "models": {
    "judge": {
      "PROVIDER": "anthropic",
      "MODEL": "claude-sonnet",
      "BASE_URL": "<provider-api-base-url>",
      "api_key_env": "JUDGE_API_KEY",
      "TEMPERATURE": 0.1
    }
  }
}
```

The profile loader also accepts lower-case and AWorld-native aliases:
`provider`/`llm_provider`, `model`/`llm_model_name`, `base_url`/`llm_base_url`,
and `api_key`/`key`/`token` for literal secrets or
`api_key_env`/`key_env`/`token_env` for environment-variable names.
Use `api_key_env` instead of `api_key` when you prefer keeping secrets in `.env`
or process environment variables.

The flag does not implement a separate optimization path. It configures the runtime agent; post-run enqueue, draining, replay, candidate generation, gates, and apply remain owned by the framework self-evolve scheduler and runner. Remote agents are not mutated by the local CLI flag.

## CLI Examples

Run a proposal-only explicit skill optimization:

```bash
aworld-cli optimize \
  --target skill:login \
  --dataset examples/aworld_quick_start/self_evolve/toy_eval.jsonl \
  --apply proposal
```

Infer the target from a trajectory log:

```bash
aworld-cli optimize \
  --task "improve login retry guidance" \
  --from-trajectory ./trajectory.log \
  --apply proposal
```

`--from-trajectory` is the default manual entrypoint even when the log contains
multiple task trajectories. The framework parses the log, infers target/task
families, and uses an internally selected coherent group for candidate
generation, replay, and evaluation. Use `--from-trajectory-set` only when a
caller needs explicit control over baseline trajectory collections.
User-authored set files may contain `baseline` and `operator_added` members
only. Framework-owned accepted, rejected, and replay members come from
self-evolve run history, not from user-authored set files.

Run verified apply for an allowlisted skill target:

```bash
aworld-cli optimize \
  --from-trajectory ~/Documents/trajectory1.log \
  --apply auto_verified \
  --judge-agent ~/Documents/agent.md \
  --judge-timeout 600 \
  --judge-model-profile gpt-5.5
```

`--judge-model-profile` names a model profile from the CLI configuration; it does not bypass profile resolution or directly set a provider model id. Add `--target skill:<name>` when target selection must be explicit. Without it, adapter-aware credit assignment selects only an eligible CLI target.

Drain pending post-run jobs from `shadow` or `online` mode:

```bash
aworld-cli optimize --drain-pending
```

Resume only the evaluator and gates after a run whose replay artifacts already exist:

```bash
aworld-cli optimize --from-run <run_id> --rerun-evaluator
```

Use `--from-run --rerun-evaluator` for judge/evaluator recovery. It cannot add missing replay repetitions; rerun full optimize with a larger `--replay-timeout` when replay itself was incomplete.

## SDK Example

Use the SDK path when a caller supplies a real `CandidateOptimizer` or wants direct control over the run:

```python
import asyncio
from pathlib import Path

from aworld.self_evolve import (
    SelfEvolveRunner,
    TraceReflectiveLLMMutator,
)
from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_recipe,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SkillTextTarget


async def mutate_text(prompt: str) -> dict[str, str]:
    return {
        "content": (
            Path("aworld-skills/login/SKILL.md").read_text(encoding="utf-8")
            + "\nWhen login fails, retry only after checking the captured error evidence.\n"
        ),
        "rationale": "The trajectory showed ambiguous login retry guidance.",
    }


cases = (
    EvalCase(
        case_id="login-retry-1",
        input={"task": "debug login flakiness"},
        expected_output={"requires_evidence": True},
    ),
)
dataset = SelfEvolveDataset(
    cases=cases,
    recipe=build_dataset_recipe(
        cases,
        source_config=SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        split_seed="manual-login-proposal",
    ),
)

runner = SelfEvolveRunner(
    store=FilesystemSelfEvolveStore(Path.cwd()),
    optimizer=TraceReflectiveLLMMutator(mutate_text=mutate_text),
)

result = asyncio.run(
    runner.run_explicit_target(
        run_id="manual-login-proposal",
        target=SkillTextTarget("aworld-skills/login/SKILL.md"),
        dataset=dataset,
        trace_packs=(),
        apply_policy="proposal",
    )
)
```

Stateful dependencies can be made replayable through an explicit SDK adapter:

```python
from aworld.self_evolve import (
    ReplayAdapterBinding,
    ReplayAdaptationCompiler,
)


class RecordedServiceAdapter:
    adapter_id = "example.recorded-service.v1"

    def __init__(self, fixture: Path) -> None:
        self.fixture = fixture.resolve()

    def bind(self, dependency, *, context):
        if dependency.kind != "http_resource" or not self.fixture.is_file():
            return None
        return ReplayAdapterBinding(
            adapter_id=self.adapter_id,
            dependency_id=dependency.identifier,
            deterministic=True,
            environment={"AWORLD_REPLAY_SERVICE_FIXTURE": str(self.fixture)},
            fixture_paths=(str(self.fixture),),
        )


runner = SelfEvolveRunner(
    store=FilesystemSelfEvolveStore(Path.cwd()),
    optimizer=TraceReflectiveLLMMutator(mutate_text=mutate_text),
    replay_adaptation_compiler=ReplayAdaptationCompiler(
        adapters=(RecordedServiceAdapter(Path("fixtures/service.json")),)
    ),
)
```

An adapter is an assertion about a real deterministic fixture or simulator. Marking a
live service deterministic without supplying equivalent replay behavior invalidates
the comparison contract.

Proposal runs persist candidates and reports but do not write the target file. Use `apply_policy="auto_verified"` only when an evaluation backend, replay evidence, and post-apply runtime-loader verification are available.

## Run Artifacts

Each run writes durable artifacts under `.aworld/self_evolve/<run_id>/`:

- `run.json`: run status, selected candidate, metrics, and gate results.
- `report.json`: release-facing summary, rejected gates, evaluator reports, replay path, release checklist, and apply status.
- `dataset_recipe.json`: source config, split seed, trainable cases, held-out cases, and synthetic generation policy.
- `target_selection.json`: credit-assignment decision, confidence, and target inference signals.
- `target_provenance.json`: provenance for the selected target when available.
- `candidates/<candidate_id>.md`: runtime-only candidate content. Skill candidates are marked with `self_evolve.release_state: candidate`; task ids, evidence ids, gate names, scores, and prior feedback remain in lineage and lesson artifacts.
- `candidates/<candidate_id>.json`: durable normalized candidate record. This is the canonical fallback for audit, `--from-run`, and historical repair after redundant materializations are reclaimed.
- `candidates/<candidate_id>.diff`: unified diff against the current target, when the target adapter supports diffs.
- `candidates/<candidate_id>/`: expanded multi-file candidate skill package, including candidate-owned replay files when present.
- `optimizer_lineage/<candidate_id>.json`: optimizer name/version, parents, trainable cases, content fingerprint, semantic fingerprint, lesson-set fingerprint, addressed lesson ids, and rationale.
- `lessons/lessons.jsonl`: normalized failure memories, success memories, and required runtime behavior records extracted from evaluation feedback.
- `diagnostics/harness_diagnostics.jsonl`: advisory framework diagnostics for replay, evidence, evaluator, memory, permission-boundary, and artifact-lifecycle issues. These records are intentionally separate from runtime skill instructions.
- `judges/<backend_id>.json`: judge prompt/result metadata.
- `apply/<candidate_id>.backup.md` and `apply/<candidate_id>.journal.json`: rollback material for verified apply.
- `release_normalization` in `report.json`: pre-normalization fingerprint, normalized release fingerprint, preserved runtime constraints, removed internal line count, and normalization verification status.
- `replay_adaptation/<dataset_fingerprint>/bundle.json`: adapted per-case inputs, dependency status, adapter ids, readiness, and provenance fingerprints.
- `replay_adaptation/<dataset_fingerprint>/workspace_seed/`: filtered immutable replay seed verified before every rollout copy.
- `replay_adaptation/<dataset_fingerprint>/workspace_manifest.json`: relative paths, modes, sizes, and SHA-256 digests for seed files.
- `replay_adaptation/<dataset_fingerprint>/environment_snapshot.json`: bounded non-secret runtime, locale, platform, and observed tool metadata used in the adaptation fingerprint.
- `repair_conformance/<candidate_id>/`: bounded service stdout/stderr, probe traces, and frozen-capability diagnostics produced by pre-rollout repair validation.
- `population` in `report.json`: candidate generation, screening attempts, selection reason, repair telemetry, and token/concurrency usage.
- `artifact_retention` in `report.json`: cleanup policy, protected runs, skipped runs, and removed paths from startup and terminal cleanup.

Artifact retention runs both when a self-evolve run starts and when it reaches a
terminal report. The two newest runs, lineage-referenced runs, interrupted apply
runs, and runs with a live process lease keep their complete artifacts. Older
eligible runs discard raw replay, replay-adaptation, repair-conformance, evaluator,
overlay, and temporary-workspace data. Candidate JSON records remain durable so
`--from-run` and audit tooling can reconstruct a candidate; only redundant Markdown,
diff, and expanded package copies for unselected candidates are pruned. A non-terminal
run without a live lease becomes eligible only after the stale-run retention window.

The default store is always rooted at `<workspace>/.aworld/self_evolve`; it does not create a top-level `self_evolve_artifacts/` directory. A pre-existing directory with that name is not managed or deleted by framework GC.

Trajectory-set runs may also include framework-owned trajectory-set and population artifacts:

- `trajectory_set/set.json`: validated copy or normalized representation of the trajectory-set input.
- `trajectory_set/members/<member_id>.json`: bounded member metadata and source references.
- `population/candidates.jsonl`: generated candidate strategy records, including non-replayed candidates when replay budget is exhausted.
- `population/patches/<candidate_id>.json`: patch intent metadata before materialization.

Multi-member replay stores a versioned manifest under `replay/<candidate_id>/members/manifest.json`. Each member directory contains only that member's baseline/candidate repetitions and uses the member's own task input. Validation and held-out evaluators consume their assigned member splits; replay repetitions measure stability and do not count as additional independent held-out members.

For a one-member trajectory dataset, paired replay may provide the strict sparse-data
verification fallback. The framework requires at least two conclusive baseline controls,
three successful candidate repetitions, and a deterministic evaluation signal. A baseline
control may be either successful or an identical native task/candidate-owned failure in
every repetition. Infrastructure-owned, blocked, not-run, mixed, or inconsistent failures
never count as controls. This lets a candidate prove recovery from the failed behavior that
triggered self-evolve without treating harness failures as evidence. Multi-member datasets
continue to use trajectory-set validation and do not collapse members into replay counts.

Each repetition directory also contains a `workspace/` copied from the adaptation
seed. These workspaces are execution sandboxes and may contain rollout mutations;
the compiler seed remains the canonical initial state.

When `--include-prior-runs` is enabled, the framework imports same-target prior run reports as advisory trainable cases. These cases carry bounded status, failed gates, metric summaries, candidate ids, and report paths; they do not copy raw trajectories into optimizer prompts and they do not create new replay evidence.

The CLI summary prints the most important paths, for example:

```text
Optimize run submitted.
Status: succeeded
Report: .aworld/self_evolve/<run_id>/report.json
Target selection: .aworld/self_evolve/<run_id>/target_selection.json
Replay: .aworld/self_evolve/<run_id>/replay.json
Evaluator report: .aworld/self_evolve/<run_id>/evaluator_report.json
Best candidate: cand-1
```

## Release Checklist

`report.json` includes a user-facing `release_checklist` built from lower-level gates. For `proposal`, failed or missing checks are diagnostic. For `auto_verified`, failed checks are blocking.

Checklist groups:

- Candidate shape: no-op, malformed markdown, candidate package protocol, protected path, provenance, token limit, target type, external code-evolution, and focused repair-conformance checks.
- Quality improvement: score improvement, replay stability, and replay confidence.
- Cost and latency: replay/evaluator cost and budget regression checks.
- Evidence integrity: evidence quality, candidate replay, and judge-only signal checks.
- Verification: required verification and held-out verification.
- Regression safety: global regression benchmarks and post-apply checks.

`content_quality_diagnostics` is non-blocking and surfaces publication-style risks when evaluator metrics include evidence, citation, unsupported claim, redundancy, or publication risk fields.

## Auto-Verified Apply

`auto_verified` is intentionally stricter than proposal mode. A candidate is rejected before apply when any required gate fails, when target inference confidence is too low, when no evaluation backend exists, or when a skill apply lacks candidate replay evidence.

When apply is allowed, the runner:

1. Writes a backup and apply journal.
2. Marks skill content as `self_evolve.release_state: verified` with `verified_run_id`, `verified_candidate_id`, and `verified_at`.
3. Writes the target file through the target adapter.
4. Runs the post-apply evaluator. The default skill evaluator verifies that the runtime loader sees the real target path and matching content.
5. Activates the verified runtime skill and refreshes the runtime registry when hooks are available.
6. Accepts the apply or rolls back from the backup if verification or activation fails.

Generated draft skills are hidden from runtime discovery until verified. Runtime skill registries filter out `draft`, `candidate`, `rejected`, and `disabled` self-evolve release states.

Candidate overlays and accepted production skills should contain only runtime-executable behavior rules. Candidate-only context such as trajectory ids, evaluator scores, gate names, raw harness diagnostic labels, and evidence ids belongs in run artifacts, not in candidate bodies or `aworld-skills/.../SKILL.md`.

Domain-specific learned skills, such as a grounding or media-comprehension skill produced by a run, are target artifacts. They are examples of what self-evolve can improve, not framework-owned self-evolve logic.

## Operating Guidance

- Prefer `proposal` for exploration, weak evidence, new target types, or human review.
- Use `auto_verified` only for allowlisted targets with reliable replay, evaluator, held-out, and rollback coverage.
- Do not treat judge-only output as verified improvement.
- Do not expose held-out cases to candidate optimizers.
- Do not copy replay overlay instructions or framework control flow into target skills.
- Treat a missing gate, missing replay artifact, or missing post-apply runtime-loader signal as not verified.
- Treat `runtime_required`, `unresolved`, and `context_incomplete` replay dependencies as non-deterministic until a typed adapter supplies equivalent fixtures.
- Never repair replay by copying credentials, browser profiles, cookies, or private host state into the seed.
- For long-lived runtimes, check `post_apply.activation` and `post_apply.refresh` before claiming future tasks will observe the applied skill without restart.

## Troubleshooting

- `auto_verified apply policy requires a candidate`: the optimizer produced no non-noop candidate, so replay/evaluation/apply were skipped.
- `auto_verified self-evolve requires an evaluation backend`: pass `--judge-agent`, `--judge-agent-name`, `--judge-backend-ref`, or use a framework evaluator backend.
- `auto_verified skill apply requires candidate replay backend`: verified apply requires replay evidence for skill candidates.
- Target inference selected no target: inspect `target_selection.json`. Phase-1 CLI inference considers only registered adapters, currently `skill`; add an explicit `skill:<name>` target or install/implement a general adapter rather than adding a trajectory-specific target exception.
- `candidate_repair_conformance` failed: inspect the gate `code`, `repair_conformance` contract, and `repair_conformance/<candidate_id>/` diagnostics. A passing source check is not sufficient when the compiled runtime fails its exact protocol probe.
- `repair_probe_execution_failed`: the frozen candidate service failed readiness, handshake, request/response correlation, non-empty-result, or recorded-response validation before task rollout. Repair the candidate-owned runtime or compiler; increasing `--replay-timeout` does not fix this preflight contract.
- Judge timeout after replay completed: rerun `aworld-cli optimize --from-run <run_id> --rerun-evaluator`.
- Missing replay repetitions or replay timeout: rerun full optimize with a higher `--replay-timeout`; evaluator-only resume cannot create new replay evidence.
- `replay_adaptation requires unavailable context or dependencies`: inspect the gate details and `replay_adaptation/.../bundle.json`; provide a bounded fixture/adapter or keep the result proposal-only.
- Post-apply status is `rolled_back`: inspect `report.json` and `apply/<candidate_id>.journal.json` for the failed metric, activation error, or runtime-loader mismatch.

See [Optimize](../AWorld%20CLI/Commands/Optimize.md) for the command reference. A small toy dataset is available in the repository at `examples/aworld_quick_start/self_evolve/`.
