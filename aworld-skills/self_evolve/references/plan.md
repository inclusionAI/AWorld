# AWorld Self-Evolve Skill Plan

## Vision

The `self_evolve` skill makes AWorld's framework self-evolve capability
discoverable to agents without moving the engine into a skill. It operates on
agent-facing harness artifacts through `aworld.self_evolve` and
`aworld-cli optimize`, producing auditable proposals and framework-gated apply
decisions.

The framework owns trace packs, target inference, datasets, candidate
generation, evaluation, gates, scheduling, apply policy, rollback, and
artifacts. The skill owns the procedure an agent follows when deciding whether
and how to use that framework.

## What Can Be Improved

Available:

- **Skill files**: `skill:<name>` targets, usually `SKILL.md`. This is the
  first operational tier because skill text is easy to diff and evaluate.

Conditional:

- **Workspace-local task artifacts**: only when they were produced by agent task
  execution and pass provenance, protected-path, and isolated evaluation gates.
- **Verified automatic application**: only with explicit policy, sufficient
  evaluation evidence, deterministic or objective signals, and post-apply
  re-evaluation.

Roadmap:

- **Tool descriptions**: `tool:<tool-name>`, limited to agent-visible wording.
- **Prompt sections**: `prompt:<section>`, with stronger regression evidence
  because blast radius is broader.
- **Agent configuration knobs**: `agent-config:<field>`, limited to
  allowlisted framework-safe fields.

## Architecture

```text
agent request
  |
  v
aworld-skills/self_evolve/SKILL.md
  |
  | chooses an available, conditional, or roadmap path
  v
aworld-cli optimize or aworld.self_evolve SDK API
  |
  v
SelfEvolveRunner
  |
  +-- trace packs and target inference
  +-- dataset and evaluation backends
  +-- candidate generation
  +-- gates and apply policy
  +-- .aworld/self_evolve artifacts
```

The skill never replaces `SelfEvolveRunner`. It should not implement a parallel
optimizer, evaluator, scheduler, gate system, or artifact store.

## Optimization Loop

1. **Select target**: use an explicit target or framework trajectory credit
   assignment. Return `no_target` when evidence is insufficient.
2. **Build evaluation evidence**: use a dataset, current trajectory, prior
   session, trajectory log, batch config, or regression benchmark source.
3. **Package traces**: pass bounded trace packs to mutators, not raw unbounded
   trajectories.
4. **Generate candidates**: use a configured framework optimizer. A CLI
   fallback may preserve the baseline and should not be described as a real
   improvement.
5. **Evaluate baseline and candidate**: compare score, cost, latency, and
   deterministic or objective verification signals.
6. **Run gates**: enforce protected-path, provenance, budget, held-out,
   regression, judge-only, target allowlist, and post-apply gates as available.
7. **Persist artifacts**: write run metadata, target selection, provenance,
   candidate files, diffs, metrics, gate results, report, apply status, and
   rollback/rejection state.

## AWorld Integration Points

- `aworld.self_evolve.runner.SelfEvolveRunner`: orchestration and apply policy.
- `aworld.self_evolve.trace_pack`: bounded trajectory evidence.
- `aworld.self_evolve.credit_assignment`: target inference and `no_target`
  diagnostics.
- `aworld.self_evolve.datasets`: dataset and split construction.
- `aworld.self_evolve.evaluation`: baseline/candidate evaluation and confidence.
- `aworld.self_evolve.gates`: safety and regression gates.
- `aworld.self_evolve.scheduler`: post-run job persistence and draining.
- `.aworld/self_evolve/`: durable run artifacts.
- `aworld-cli optimize`: manual CLI entrypoint for proposal and diagnostics.

## Safety Gates

Do not apply automatically unless framework artifacts show all required gates
passed:

- target type is allowlisted for auto-apply
- target path is not protected
- target provenance is trusted enough for the requested policy
- budget preflight passes
- baseline and candidate use comparable evaluation data
- held-out case count is sufficient for verified claims
- deterministic or objective verification signal is present
- judge-only improvement is downgraded to limited confidence
- global regression benchmark passes when configured
- post-apply re-evaluation accepts the candidate

If a required gate is missing, failed, or unavailable, produce a proposal,
diagnostic, or rejection. Do not report verified improvement.

## Invocation Forms

Available CLI proposal:

```bash
aworld-cli optimize \
  --target skill:example_skill \
  --dataset path/to/eval_cases.jsonl \
  --apply proposal
```

Available CLI target inference:

```bash
aworld-cli optimize \
  --from-trajectory path/to/trajectory.log \
  --infer-target \
  --apply proposal
```

Conditional SDK path with a real optimizer:

```python
from aworld.self_evolve import SelfEvolveRunner


async def run(runner: SelfEvolveRunner, target, dataset, trace_packs):
    return await runner.run_explicit_target(
        run_id="manual-proposal",
        target=target,
        dataset=dataset,
        trace_packs=trace_packs,
        apply_policy="proposal",
    )
```

## Phases

1. **Phase 1a - available**: skill-text proposals, explicit target runs,
   target inference diagnostics, durable artifacts, and protected-path gates.
2. **Phase 1b - conditional**: verified apply for allowlisted skill targets
   when evaluation, held-out, deterministic signal, budget, and post-apply
   checks are present.
3. **Phase 2 - roadmap**: tool descriptions and prompt sections after their
   target adapters are implemented and covered end to end.
4. **Phase 3 - roadmap**: allowlisted config knobs and workspace artifacts with
   stronger provenance and regression gates.
5. **Later**: broader automated loops only after current gates produce
   measurable improvements without regression.

## Non-goals

- Do not move framework self-evolve into this skill.
- Do not add a second CLI command.
- Do not mutate AWorld framework, runtime, `aworld-cli`, package metadata,
  secrets, or protected built-in skills.
- Do not use `app_evaluator` or `self_evolve` as default mutation targets.
- Do not claim CLI fallback output is a real improvement when it preserved the
  baseline.
- Do not import external project implementation code into AWorld.
