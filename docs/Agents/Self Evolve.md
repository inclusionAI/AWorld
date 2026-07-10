# Self Evolve

Self-evolve is a framework-owned capability for improving agent-facing harness artifacts from observed task trajectories. Phase 1 is intentionally narrow: it proposes and verifies changes to harness text and configuration surfaces, especially skills. It does not mutate AWorld framework code, runtime code, CLI code, dependency manifests, secrets, or repository product logic.

The capability is disabled by default. Agents opt in through `AgentConfig.self_evolve_config`, `aworld-cli --evolve` can enable it for the current CLI runtime session, and `aworld-cli optimize` provides an explicit manual/debug entrypoint for release operators and developers.

## What It Optimizes

Self-evolve works on target references rather than arbitrary files. A target records the artifact type, id, optional path, and provenance used by the framework gates.

Phase 1 target support:

- `skill:<name>`: available end to end for proposal runs and verified apply when the target is allowlisted.
- `workspace-artifact:<path>`: available for proposal preservation when the path is outside protected product roots.
- `prompt-section:<id>`, `tool-description:<id>`, and `config:<id>`: represented as target types, but treated as skeleton/roadmap targets until adapters are implemented and covered by tests.

Protected areas include AWorld framework/runtime/CLI code, dependency files, package metadata, and protected built-in skills such as `app_evaluator` and `self_evolve`.

## Design

Self-evolve follows a proposal-first pipeline:

1. Collect trajectory or evaluation evidence from a current run, trajectory log, session, JSONL dataset, or batch config.
2. Build a dataset recipe with trainable and held-out cases. Candidate optimizers see trainable evidence only.
3. Select a target explicitly from `--target` or infer one through framework credit assignment.
4. Generate one or more candidate variants through a `CandidateOptimizer`.
5. Preserve candidates and diffs under `.aworld/self_evolve/<run_id>/`.
6. Run shape, provenance, budget, evidence, replay, evaluator, and regression gates.
7. Keep the result as a proposal, or, for `auto_verified`, apply only after verification passes.
8. Re-evaluate the applied artifact through the runtime loader and roll back if post-apply verification fails.

This keeps the self-evolve loop outside the task response path. Post-run scheduling is best effort: a failed enqueue is logged and does not change the completed `TaskResponse`.

## Configuration

`SelfEvolveConfig.mode` controls whether the runner schedules self-evolve work:

```python
from aworld.config.conf import AgentConfig, SelfEvolveConfig

agent_config = AgentConfig(
    self_evolve_config=SelfEvolveConfig(
        mode="shadow",
        apply_policy="proposal",
        min_eval_cases=1,
    )
)
```

Modes:

- `off`: default; no post-run self-evolve scheduling.
- `offline`: manual SDK/CLI runs only.
- `shadow`: post-run jobs may be enqueued, but candidates remain proposals.
- `online`: post-run jobs may apply allowlisted targets only after verified replay, evaluator gates, and post-apply re-evaluation.

`online` requires `apply_policy="auto_verified"`. `auto_verified` also requires `requires_post_apply_reevaluation=True`, which is the default. Useful verification knobs include `replay_timeout_seconds`, `replay_max_steps`, `baseline_replay_repetitions`, `candidate_replay_repetitions`, `replay_candidate_limit`, `replay_stability_margin`, `judge_repetitions`, and `judge_timeout_seconds`.

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
  --target skill:login \
  --from-trajectory ./trajectory.log \
  --apply auto_verified \
  --judge-agent ./judges/login_quality.md \
  --replay-timeout 900 \
  --baseline-replay-repetitions 2 \
  --candidate-replay-repetitions 3
```

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

Proposal runs persist candidates and reports but do not write the target file. Use `apply_policy="auto_verified"` only when an evaluation backend, replay evidence, and post-apply runtime-loader verification are available.

## Run Artifacts

Each run writes durable artifacts under `.aworld/self_evolve/<run_id>/`:

- `run.json`: run status, selected candidate, metrics, and gate results.
- `report.json`: release-facing summary, rejected gates, evaluator reports, replay path, release checklist, and apply status.
- `dataset_recipe.json`: source config, split seed, trainable cases, held-out cases, and synthetic generation policy.
- `target_selection.json`: credit-assignment decision, confidence, and target inference signals.
- `target_provenance.json`: provenance for the selected target when available.
- `candidates/<candidate_id>.md`: runtime-only candidate content. Skill candidates are marked with `self_evolve.release_state: candidate`; task ids, evidence ids, gate names, scores, and prior feedback remain in lineage and lesson artifacts.
- `candidates/<candidate_id>.diff`: unified diff against the current target, when the target adapter supports diffs.
- `optimizer_lineage/<candidate_id>.json`: optimizer name/version, parents, trainable cases, content fingerprint, semantic fingerprint, lesson-set fingerprint, addressed lesson ids, and rationale.
- `lessons/lessons.jsonl`: normalized failure memories, success memories, and required runtime behavior records extracted from evaluation feedback.
- `diagnostics/harness_diagnostics.jsonl`: advisory framework diagnostics for replay, evidence, evaluator, memory, permission-boundary, and artifact-lifecycle issues. These records are intentionally separate from runtime skill instructions.
- `judges/<backend_id>.json`: judge prompt/result metadata.
- `apply/<candidate_id>.backup.md` and `apply/<candidate_id>.journal.json`: rollback material for verified apply.
- `release_normalization` in `report.json`: pre-normalization fingerprint, normalized release fingerprint, preserved runtime constraints, removed internal line count, and normalization verification status.

Trajectory-set runs may also include framework-owned trajectory-set and population artifacts:

- `trajectory_set/set.json`: validated copy or normalized representation of the trajectory-set input.
- `trajectory_set/members/<member_id>.json`: bounded member metadata and source references.
- `population/candidates.jsonl`: generated candidate strategy records, including non-replayed candidates when replay budget is exhausted.
- `population/patches/<candidate_id>.json`: patch intent metadata before materialization.

Multi-member replay stores a versioned manifest under `replay/<candidate_id>/members/manifest.json`. Each member directory contains only that member's baseline/candidate repetitions and uses the member's own task input. Validation and held-out evaluators consume their assigned member splits; replay repetitions measure stability and do not count as additional independent held-out members.

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

- Candidate shape: no-op, malformed markdown, protected path, provenance, token limit, target type, and external code-evolution checks.
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
- For long-lived runtimes, check `post_apply.activation` and `post_apply.refresh` before claiming future tasks will observe the applied skill without restart.

## Troubleshooting

- `auto_verified apply policy requires a candidate`: the optimizer produced no non-noop candidate, so replay/evaluation/apply were skipped.
- `auto_verified self-evolve requires an evaluation backend`: pass `--judge-agent`, `--judge-agent-name`, `--judge-backend-ref`, or use a framework evaluator backend.
- `auto_verified skill apply requires candidate replay backend`: verified apply requires replay evidence for skill candidates.
- Judge timeout after replay completed: rerun `aworld-cli optimize --from-run <run_id> --rerun-evaluator`.
- Missing replay repetitions or replay timeout: rerun full optimize with a higher `--replay-timeout`; evaluator-only resume cannot create new replay evidence.
- Post-apply status is `rolled_back`: inspect `report.json` and `apply/<candidate_id>.journal.json` for the failed metric, activation error, or runtime-loader mismatch.

See [Optimize](../AWorld%20CLI/Commands/Optimize.md) for the command reference. A small toy dataset is available in the repository at `examples/aworld_quick_start/self_evolve/`.
