---
name: self_evolve
description: "Use for framework-gated self-evolve workflows in AWorld: evolve skills, create trajectory-backed proposals, inspect self-evolve run artifacts, run aworld-cli optimize, or prepare verified apply decisions through aworld.self_evolve gates."
---

# Self-Evolve

Use this skill to run AWorld self-evolve as an evidence-backed workflow. The
framework is the engine. This skill is the operating guide.

Do not bypass `aworld.self_evolve`, `SelfEvolveRunner`, framework gates, or
`.aworld/self_evolve/` artifacts. Do not directly edit a target as the default
self-evolve action.

Read `references/plan.md` when the user asks for architecture, rollout
strategy, target tier planning, or a larger self-evolve roadmap. For a narrow
proposal run, use the workflow below without loading the full plan unless it is
needed.

## Capability Levels

- **Available**: `skill:<name>` proposal runs, explicit target invocation,
  trajectory-backed target inference, artifact reporting, and framework gate
  reporting when the run produces gate results.
- **Conditional**: `auto_verified` apply, asynchronous post-run jobs, and any
  flow requiring an evaluation backend, held-out cases, deterministic signals,
  candidate replay, post-apply runtime-loader verification, or a
  caller-supplied real optimizer.
- **Roadmap**: `tool:<tool-name>`, `prompt:<section>`,
  `agent-config:<field>`, and broad workspace-artifact evolution unless the
  current framework target adapter is implemented end to end and covered by
  tests.

If a requested path is not Available, say so and downgrade to a proposal,
diagnostic, or roadmap note instead of implying verified behavior.

## Framework Boundaries

Keep three layers separate:

- This `self_evolve` skill describes how to operate the self-evolve workflow.
  It is not the execution engine and must not replace framework gates,
  replay, evaluation, overlay, draft, release, or apply logic.
- `aworld.self_evolve` owns execution semantics: target inference, candidate
  generation contracts, replay, evaluator integration, feedback normalization,
  gates, apply journals, post-apply verification, and runtime-loader checks.
- Target skills express task behavior only. A candidate or draft target skill
  may improve how an agent performs the task, but must not encode
  self-evolve framework control flow, gate bypasses, release decisions, or
  evaluator policy.

When a candidate is evaluated, the framework may inject replay-time execution
constraints or overlays. Do not copy those framework controls into the target
skill unless the final applied guidance is independently useful for the target
task itself.

## Workflow

1. Clarify the requested outcome: diagnostics, proposal-only improvement, or
   verified automatic application.
2. Select the target:
   - Use the explicit user target when provided.
   - Otherwise use framework trajectory credit assignment.
   - Decline candidate generation when evidence is insufficient.
3. Gather evaluation evidence: dataset, prior session, trajectory file, current
   trajectory, batch config, or regression benchmark source.
4. Invoke framework self-evolve through `aworld.self_evolve` APIs or
   `aworld-cli optimize`.
5. Default to proposal-only behavior.
6. For `auto_verified`, require framework gate evidence for candidate replay,
   evaluation, held-out cases, deterministic or objective signal, target
   allowlist, budget, protected path, provenance, and post-apply runtime-loader
   verification.
7. Report the run id, target, evidence source, candidate id, metric deltas,
   gate status, replay path, evaluator report path, report path, and apply
   status.

## Trajectory-Set Learning

Use trajectory-set inputs when the goal is sustained improvement instead of a
single trace reflection. Prefer a small related set that includes the original
baseline trajectory, candidate replay results, accepted-skill follow-up
trajectories, and rejected candidate runs for the same target.

The framework owns trajectory-set validation, prior-run inclusion, lesson
extraction, candidate population ranking, replay selection, and lineage
memory. This skill should only describe the operating flow:

- Use `--from-trajectory-set <set.json>` when the user has a curated set.
- Use `--include-prior-runs` when same-target self-evolve history should be
  used as advisory learning memory.
- Treat lessons, harness diagnostics, lineage, and population records as
  framework artifacts under `.aworld/self_evolve/<run_id>/`.
- Do not copy raw trajectories, judge rubrics, internal gate names, task ids,
  or replay-only controls into a target skill.
- If prior runs show repeated rejected variants, expect the framework to avoid
  exact, semantic, and lesson-set duplicates before expensive replay.

Candidate generation should preserve successful lean behavior paths and only
add lesson-backed behavior deltas. When no safe lesson-backed delta exists,
the correct outcome is no-op or rejection, not a longer or more specific
target skill.

## Target Tiers

- **Skill text - Available**: use `skill:<name>` when traces or user intent
  point to a skill procedure issue. Preserve valid frontmatter and concise
  procedural guidance.
- **Tool descriptions - Roadmap**: use only after `ToolDescriptionTarget` is
  implemented end to end. Do not change schemas or tool implementation code.
- **Prompt sections - Roadmap**: use only after `PromptSectionTarget` is
  implemented end to end. Require stronger regression evidence.
- **Agent config - Roadmap**: use only for framework-allowlisted config fields
  after `AgentConfigTarget` is implemented end to end.
- **Workspace artifacts - Conditional/Roadmap**: use only for artifacts
  produced by agent task execution and only after provenance, protected-path,
  and isolated evaluation gates allow the target.

Treat `aworld-skills/app_evaluator/SKILL.md` and
`aworld-skills/self_evolve/SKILL.md` as protected from default mutation.

## CLI Examples

Proposal-only explicit skill target:

```bash
aworld-cli optimize \
  --target skill:example_skill \
  --dataset path/to/eval_cases.jsonl \
  --apply proposal
```

Proposal-only trajectory-backed target inference:

```bash
aworld-cli optimize \
  --from-trajectory path/to/trajectory.log \
  --apply proposal
```

Drain pending post-run self-evolve jobs:

```bash
aworld-cli optimize --drain-pending
```

Verified replay with an evaluator agent:

```bash
aworld-cli optimize \
  --target skill:example_skill \
  --from-trajectory path/to/trajectory.log \
  --apply auto_verified \
  --judge-agent path/to/agent.md
```

CLI fallback behavior may preserve the baseline when no real optimizer is
configured. Do not claim content improvement from a CLI run unless the report
shows a changed candidate and passing framework evidence.

Verified replay is conditional. The framework mounts a candidate skill in an
isolated overlay and reruns the task through the runtime. If credentials, model
configuration, browser state, services, or other environment prerequisites are
missing, report a replay failure. Do not treat those prerequisites as mutation
targets.

Replay variance and cost controls are framework configuration, not skill-local
logic. Respect `replay_candidate_limit`, `baseline_replay_repetitions`,
`candidate_replay_repetitions`, and `replay_stability_margin` when they appear
in config or reports. A fixed historical baseline plus a single candidate rerun
is limited-confidence unless framework policy explicitly accepts it.

During optimize runs, progress messages may report trajectory-set loading,
candidate population generation, replay, evaluation, lesson extraction, and
release normalization. These are framework stages. Do not infer success from a
progress stage alone; use the final report gates and post-apply status.

## SDK Example

Use the SDK path when a real `CandidateOptimizer` is supplied by the caller:

```python
from pathlib import Path

from aworld.self_evolve import SelfEvolveRunner
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SkillTextTarget


async def run_self_evolve(candidate_optimizer, dataset, trace_packs):
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(Path.cwd()),
        optimizer=candidate_optimizer,
    )
    return await runner.run_explicit_target(
        run_id="manual-skill-proposal",
        target=SkillTextTarget("example_skill", Path("aworld-skills/example_skill/SKILL.md")),
        dataset=dataset,
        trace_packs=trace_packs,
        apply_policy="proposal",
    )
```

The supplied `candidate_optimizer` must implement the framework
`CandidateOptimizer` protocol and produce candidate text through the framework
runner. Do not mutate the skill file directly in this workflow.

## Safety Rules

- Do not expose held-out gate data to candidate mutators.
- Do not treat judge-only output as verified improvement.
- Do not target framework, runtime, `aworld-cli`, package metadata, secrets, or
  protected built-in skills.
- Do not auto-apply unless the framework run records passing gates and
  post-apply runtime-loader acceptance.
- For long-lived runtimes, require a registry refresh/reload signal in the
  report before claiming future tasks will observe the applied skill without
  restart.
- Do not treat a successful process exit as verified replay unless the run
  captured candidate trajectory evidence.
- If gate information is missing, failed, or unavailable, report proposal-only,
  diagnostic, or rejected status.

## Report Format

Return a compact summary with:

- `run_id`
- `target`
- `evidence_source`
- `candidate_id`
- `metric_deltas`
- `gate_status`
- `replay_path`
- `evaluator_report_paths`
- `report_path`
- `apply_status`

For verified skills, the released `SKILL.md` should contain only runtime
behavior guidance. Internal self-evolve context belongs in report artifacts:
`lessons`, `harness_diagnostics`, `optimizer_lineage`, `population`, and
`release_normalization`. If release normalization removes gate-critical runtime
constraints, the framework must reject or roll back rather than publishing an
untested normalized skill.
