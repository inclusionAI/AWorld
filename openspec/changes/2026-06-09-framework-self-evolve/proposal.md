## Why

AWorld already has several ingredients needed for agent improvement loops:

- trajectory and `llm_calls` capture in the framework runtime
- `EvaluateRunner`, `EvalTarget`, `Scorer`, and LLM-as-judge scoring
- `RalphRunner` for bounded repair loops with verification feedback
- existing trajectory scorers such as `trajectory_validators`
- skill registry and prompt/context augmentation surfaces
- `aworld-cli` commands, batch execution, session logs, and durable memory
- an existing `Runners.evolve(...)` / `train.evolve.EvolutionRunner` pipeline
  for model/data/tool-synthesis training workflows

However, these pieces are not yet organized as a framework-level self-evolve
capability. Current improvement behavior is mostly task-local or prompt-driven:

- a user can ask the CLI AWorld agent to evaluate and improve an artifact
- `RalphRunner` can repair a task against explicit verification commands
- traces and trajectories are recorded, but not systematically converted into
  candidate harness changes
- existing `train.evolve` focuses on evolution/training workflows, not
  controlled harness improvements

The target is to make self-evolution a first-class AWorld framework capability,
not only an `aworld-cli` workflow. AWorld should be able to run controlled
optimization loops after an agent run has produced a trajectory. In phase 1,
the scope is harness-text and harness-configuration improvement: analyze
trajectory quality, identify which skill text, prompt section, tool description,
or allowlisted harness knob is most likely responsible for the failure or
inefficiency, propose safe harness improvements, evaluate candidates, and either
persist reviewable proposal/diff artifacts or, in an explicitly enabled online
mode, apply a verified candidate through a controlled
`apply -> re-evaluate -> accept/rollback` loop. Automatic application MUST be
limited to allowlisted targets and MUST NOT affect the task that already
completed.

This proposal is intentionally separate from existing `train/evolve` assets.
Existing evolve code owns model/data/tool-synthesis training and may continue to
use names such as `EvolutionRunner` and `EvolutionConfig`. This change owns
controlled harness optimization under `aworld.self_evolve`, with artifacts
stored separately under `.aworld/self_evolve/`.

Phase 1 does not train model weights, replace the agent policy, or make the
agent intrinsically stronger independent of its harness. A single post-run
trajectory usually has too little evidence to verify an automatic change, so the
default post-run outcome is a target-selection report plus a limited-confidence
proposal. Automatic `online` application is only expected when an allowlisted
target, independent evaluation sources, deterministic/objective gates, and
post-apply re-evaluation are all configured.

This is the intended boundary: the goal of this change is harness evolution, not
policy or weight evolution. "Execution capability" is improved only through the
agent-facing harness that guides future execution.

## What Changes

- Add a framework-owned self-evolve module that manages optimization targets,
  trajectory credit assignment, datasets, candidate generation, evaluation,
  gates, and run artifacts.
- Add a phase-0 credit-assignment spike as a hard go/no-go gate before building
  the full optimizer pipeline. The spike must use real trajectory fixtures with
  manually labeled target/no-target outcomes and demonstrate acceptable target
  selection precision/recall before candidate generation, async scheduling, or
  broad target support expands. Initial acceptance thresholds are
  target-selection precision >= 0.80, target-selection recall >= 0.70, and
  `no_target` precision >= 0.80, unless the proposal is amended with stricter
  thresholds before implementation starts.
- Seed the first regression benchmark from task records in the local trajectory
  log at `~/Documents/logs/trajectory.log`. The seed process should extract
  task/evaluation cases, persist a dataset recipe with the source path and
  content fingerprint, and copy any committed test fixtures into the repository
  rather than making product behavior depend on that local path.
- Add trace packaging, target provenance, dataset recipes, and optimizer
  lineage as first-class framework concepts.
- Add an explicit agent-level opt-in surface, `AgentConfig.self_evolve_config`
  with `SelfEvolveConfig.mode`, with self-evolve disabled by default.
- Add an asynchronous post-run trigger path that can enqueue trajectory-driven
  self-evolve work after agent execution, without blocking the main task flow.
- Define a stable evaluation contract for self-evolve so the capability depends
  on an evaluation interface rather than one specific evaluator agent.
- Allow LLM-judge behavior to be configured. The default judge should be a
  self-evolve-owned trajectory judge, while users may supply an `agent.md` or a
  custom agent as an additional judge signal.
- Reuse existing `aworld.evaluations`, trajectory, `llm_calls`, and Ralph
  verification capabilities as default evaluation and repair backends.
- Absorb the useful architecture from Hermes self-evolution work: trace-
  reflective optimization, organism/evaluator/mutator separation, trainable vs
  held-out failure cases, lineage artifacts, and trust/provenance guardrails.
- Add framework target types for phase 1:
  - skill text / `SKILL.md`
  - prompt sections
  - tool descriptions
  - agent config / harness knobs
  - workspace-local code or files, only when they are produced by agent task
    execution and validated in an isolated candidate workspace
- Keep `aworld-skills/app_evaluator/SKILL.md` and the existing app-evaluator
  workflow out of scope for candidate mutation. Self-evolve must be a new
  complete framework subsystem, not an upgrade or rewrite of that skill.
- Defer framework, `aworld-cli`, and runtime source-code evolution to a later
  phase.
- Add one CLI product surface to invoke the framework capability:
  - a single top-level `aworld-cli optimize` command with extensible options
    and target forms
  - no CLI-owned optimization loop, scheduler, evaluator, or agent opt-in
    semantics
- Persist self-evolve run metadata, candidate diffs, metrics, diagnostics, apply
  state, acceptance state, and rejection/rollback state under a
  workspace-scoped `.aworld/self_evolve/` location.
- Add at least one real automatic evolve mode: `online` with a verified apply
  policy. This mode must apply eligible allowlisted candidates after gates and
  post-apply re-evaluation pass without human approval; otherwise it must fall
  back to a proposal or rejected state.

## Capabilities

### New Capabilities

- `framework-self-evolve`: AWorld can optimize agent-facing harness artifacts
  through a controlled, measurable, framework-owned loop that may run
  asynchronously after an opted-in agent execution.
- `self-evolve-credit-assignment`: AWorld can inspect a trajectory and produce
  an evidence-backed target selection report before candidate generation.
- `self-evolve-targets`: AWorld can model skills, prompt sections, tool
  descriptions, agent configuration, and isolated agent-produced workspace
  artifacts as explicit optimization targets.
- `self-evolve-evaluation-contract`: AWorld can evaluate baseline and candidate
  variants through a pluggable evaluation interface.
- `self-evolve-judge-contract`: AWorld can run a default trajectory-aware judge
  or a user-specified `agent.md`/custom judge agent as an evaluation signal.
- `self-evolve-run-artifacts`: AWorld can persist lineage, metrics, diffs,
  diagnostics, apply state, acceptance state, and rejection/rollback state for
  each self-evolve run.
- `self-evolve-provenance`: AWorld can track target source, trust, and protected
  status separately from target content.

### Modified Capabilities

- `agent-configuration`: agents can opt into self-evolve through a
  disabled-by-default `self_evolve_config.mode` contract.
- `aworld-cli-task-execution`: CLI can expose commands that invoke framework
  self-evolve for a specified task, target, dataset, or previous session.
- `aworld-evaluation`: existing evaluation components become default backends
  for self-evolve but remain usable independently.

## Impact

- Affected framework areas:
  - `aworld/config/conf.py`
  - `aworld/evaluations/`
  - `aworld/dataset/`
  - `aworld/runners/`
  - `aworld/runners/ralph_runner.py`
  - `aworld/skills/`
  - `aworld/core/context/amni/`
- Affected CLI areas:
  - `aworld-cli/src/aworld_cli/top_level_commands/`
  - `aworld-cli/src/aworld_cli/builtin_plugins/`
- Safety constraints:
  - self-evolve MUST be off by default
  - post-run self-evolve MUST be asynchronous and MUST NOT affect the completed
    task result
  - candidate generation MUST NOT directly mutate active runtime behavior
  - default and shadow runs MUST stop at proposal and diff artifacts
  - online runs MUST automatically apply verified candidates for allowlisted
    targets when held-out, deterministic/objective, regression, budget,
    protected-path, and post-apply re-evaluation gates pass
  - online automatic apply MUST be unattended and MUST NOT require human review,
    approval, confirmation, or intervention
  - automatic online apply MUST support rollback or mark the candidate rejected
    if post-apply metrics regress
  - framework, `aworld-cli`, and runtime source-code evolution MUST NOT be part
    of phase 1
  - `aworld-skills/app_evaluator/SKILL.md` MUST NOT be modified by this change
    or selected as a default self-evolve target
  - workspace-local task artifact optimization MUST run in isolation and MUST
    produce proposal and diff artifacts only in phase 1 unless a later target
    policy explicitly allows auto-apply
  - self-evolve candidates MUST NOT modify AWorld framework or `aworld-cli`
    product logic
  - candidate selection MUST use validation data, while pass/fail gates MUST use
    optimizer-held-out evaluation data when enough cases are available
  - single-trajectory post-run jobs usually lack enough held-out cases and MUST
    produce limited-confidence proposals unless additional eval sources are
    supplied
  - phase-0 credit assignment MUST be accepted before building the full
    optimizer pipeline; otherwise phase 1 MUST stop at diagnostics and explicit
    target-only experiments
  - the initial regression benchmark SHOULD be seeded from
    `~/Documents/logs/trajectory.log`, but runtime self-evolve MUST still require
    explicit dataset/session/batch/trajectory sources and MUST NOT hard-code that
    path as a product dependency
  - global harness-text targets MUST pass configured regression benchmarks
    before a candidate can be considered verified
  - judge-only improvements MUST remain limited-confidence; verified
    improvements MUST include at least one deterministic signal such as command
    verification or a configured objective scorer
  - every run MUST enforce a token/cost budget ceiling
  - candidate evaluation MUST estimate baseline plus candidate replay cost before
    launching batch evaluation and MUST reduce candidates/eval cases or stop
    before exceeding the configured budget
  - optimizers MUST receive compact trace packs and trainable failure cases,
    not raw unbounded trajectories or held-out gate data
  - evaluator-agent-only loops MUST NOT be the only correctness signal
