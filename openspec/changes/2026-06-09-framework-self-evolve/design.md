## Context

The reference design in `NousResearch/hermes-agent-self-evolution` treats
self-evolution as an optimization pipeline operating on an agent harness:

1. select a target such as a skill, prompt, tool description, or code file
2. build an evaluation dataset from real or synthetic examples
3. generate candidate variants with an optimizer
4. evaluate baseline and candidates through a batch harness
5. apply constraints and benchmark gates
6. output a diff, metrics, and reviewable change

AWorld should adopt the same core loop, but with a different ownership boundary.
The self-evolve foundation should belong to `aworld` framework. `aworld-cli`
should be a caller and product surface, not the owner of the core capability.

This follows the desired positioning:

- framework owns the reusable self-evolve abstractions
- agents can opt in through disabled-by-default configuration
- opted-in agents can enqueue asynchronous post-run self-evolve work after a
  trajectory is produced
- CLI can enable the feature for the built-in AWorld main agent
- CLI can provide one generic manual/debug command to optimize a specified task
  or target

## Goals / Non-Goals

**Goals**

- Make self-evolve a framework-level capability in AWorld.
- Keep the feature disabled by default.
- Add an agent-level opt-in contract using `AgentConfig.self_evolve_config.mode`.
- Support asynchronous post-run self-evolve for opted-in agents after a
  trajectory is produced, without blocking or changing the main task result.
- Make trajectory-driven credit assignment a phase-1 core capability: the
  framework must inspect the trajectory, choose an evidence-backed target, and
  either produce a proposal/diff or record why no reliable target exists.
- Allow CLI AWorld main agent to enable self-evolve through config or env.
- Provide a CLI command that can optimize a specified task, target, dataset, or
  prior session as a manual/debug entrypoint.
- Support phase-1 optimization targets:
  - skills / `SKILL.md`
  - prompt sections
  - tool descriptions
  - selected agent config knobs
  - workspace-local code or files produced by agent task execution, validated
    in an isolated candidate workspace
- Reuse existing AWorld evaluation, trajectory, and Ralph verification
  capabilities instead of introducing a parallel evaluation stack.
- Store every self-evolve run as an auditable artifact with baseline/candidate
  metrics, diff, diagnostics, and approval state.
- Support proposal-only runs that emit report and diff artifacts, without
  phase-1 write or branch application.

**Non-Goals**

- Do not train or fine-tune model weights.
- Do not replace or rename the existing `Runners.evolve(...)` /
  `train.evolve.EvolutionRunner` training pipeline.
- Do not make self-evolve run automatically for all agents.
- Do not bind self-evolve to the current UI `app_evaluator` skill.
- Do not require a specific optimizer such as DSPy/GEPA for the framework
  contract.
- Do not include framework, CLI core, runtime, shared infrastructure, package
  source-code evolution, or any self-evolve candidate changes to `aworld/` or
  `aworld-cli/` product logic in phase 1.
- Do not treat external trajectory logs as required product dependencies; they
  are optional evaluation sources and may be used as test fixtures.
- Do not let candidate prompt/skill/tool changes silently alter the active
  runtime without gates and explicit application.
- Do not replace existing `EvaluateRunner`, `RalphRunner`, trajectory, or CLI
  batch capabilities.

## Decisions

### Decision: Self-evolve is separate from existing train/evolve

The repository already has an `evolve` concept:

- `Runners.evolve(...)` delegates to `train.evolve.EvolutionRunner`.
- `train.evolve.EvolutionConfig` configures a training-oriented pipeline.
- `EvolutionPipelineAgent` plans tool synthesis, data synthesis, training, and
  evaluation.
- `AgentConfig.meta_learning_config` and `ModelConfig.optimization_config`
  already describe adjacent learning/optimization surfaces.

This change MUST NOT overload those assets. The boundary is:

- existing `train.evolve`: model/data/tool-synthesis training workflows,
  including dependencies such as `transformers`, `verl`, and `AgentTrainer`
- new `aworld.self_evolve`: proposal-only harness optimization for skills,
  prompt sections, tool descriptions, whitelisted config knobs, and
  agent-produced workspace artifacts

Naming follows that boundary:

- framework package: `aworld/self_evolve/`
- runner/config/run models: `SelfEvolveRunner`, `SelfEvolveConfig`,
  `SelfEvolveRun`
- artifact root: `.aworld/self_evolve/<run_id>/`

`AgentConfig.self_evolve_config` is a dedicated config surface because this
capability has different safety, storage, and apply semantics from
`meta_learning_config` and model optimization. It should not add a parallel
`AgentConfig.optimize` flag; `SelfEvolveConfig.mode="off"` is the disabled
state and any other mode is the explicit opt-in.

### Decision: Framework owns the self-evolve core

The new capability should live under a framework package such as:

- `aworld/self_evolve/`

Recommended submodules:

- `config.py`: `SelfEvolveConfig` and related policy models
- `targets.py`: optimization target interfaces and built-in target types
- `datasets.py`: dataset builders from jsonl, batch config, session logs, and
  trajectory artifacts
- `credit_assignment.py`: trajectory analysis and target selection
- `optimizers/`: pluggable candidate generators
- `evaluation.py`: baseline/candidate evaluation orchestration
- `gates.py`: constraints and benchmark gates
- `scheduler.py`: best-effort asynchronous post-run enqueue and worker control
- `store.py`: self-evolve run artifact persistence
- `runner.py`: `SelfEvolveRunner`

Why:

- The same self-evolve loop should work from Python SDK, `aworld-cli`, tests,
  or future services.
- CLI-specific UX should not leak into framework contracts.

### Decision: Agent opt-in is explicit and disabled by default

`AgentConfig` should gain one disabled-by-default self-evolve surface.
`SelfEvolveConfig.mode` controls both eligibility and execution behavior.

Recommended shape:

```python
class SelfEvolveConfig(BaseConfig):
    mode: Literal["off", "offline", "shadow", "online"] = "off"
    apply_policy: Literal["proposal"] = "proposal"
    target_types: list[str] = [
        "skills",
        "prompt_sections",
        "tool_descriptions",
        "agent_config",
        "workspace_artifacts",
    ]
    eval_sources: list[SelfEvolveEvalSourceConfig] = []
    max_iterations: int = 3
    min_improvement: float = 0.03
    min_eval_cases: int = 30
    judge_repetitions: int = 3
    max_run_tokens: int = 500_000
    max_run_cost_usd: Optional[float] = None
    max_background_jobs: int = 1
    cooldown_minutes: int = 60

class AgentConfig(BaseConfig):
    self_evolve_config: SelfEvolveConfig = SelfEvolveConfig()
```

Semantics:

- `mode="off"`: the agent is not eligible for self-evolve.
- `mode="offline"`: only explicit optimize runs are allowed.
- `mode="shadow"`: successful agent task completion may enqueue an asynchronous
  post-run self-evolve job that can generate diagnostics or proposals, but
  candidates are not applied.
- `mode="online"`: successful agent task completion may enqueue asynchronous
  self-evolve work that generates reviewable proposals and diffs. Phase 1 does
  not apply candidates automatically and must not affect the already-completed
  task.

Why:

- Self-evolve changes harness artifacts and must be opt-in.
- A separate `optimize` or `enabled` flag would duplicate `mode="off"` and
  create contradictory states such as `optimize=False` with `mode="shadow"`.

### Decision: Post-run self-evolve is asynchronous and best-effort

Agent task execution remains the primary workflow. When an opted-in agent
finishes a run and a trajectory is available, the framework should only perform
a lightweight eligibility check and enqueue a background self-evolve job:

```python
if agent_config.self_evolve_config.mode in {"shadow", "online"}:
    SelfEvolveScheduler.enqueue(run_context)
```

The enqueue operation MUST be best-effort:

- it MUST NOT block task response delivery
- it MUST NOT change the completed task result
- enqueue failures MUST be recorded as diagnostics or warnings, not surfaced as
  task failures
- worker failures MUST persist failed self-evolve artifacts when a run was
  created
- background workers MUST enforce timeout, concurrency, retry, and cooldown
  policies

Why:

- Self-evolve can be expensive and should not degrade the normal user-facing
  task path.
- A failed optimizer or evaluator should never make the original agent task
  fail retroactively.

Execution model:

- `enqueue` MUST persist a durable pending job record before returning.
- In long-lived runtimes, the scheduler MAY also hand the job to an in-process
  asyncio worker.
- In short-lived CLI processes, post-run enqueue MUST NOT rely on a fire-and-
  forget task surviving process exit. It should leave a durable pending job that
  can be drained by an explicit framework/CLI optimize invocation using the same
  generic command path.
- Explicit `aworld-cli optimize ...` runs are synchronous manual/debug runs
  unless the caller requests queue behavior.

### Decision: Evaluation sources are pluggable and trajectory logs are optional

Self-evolve should build evaluation context from explicit source definitions.
The current run trajectory is the default source for post-run shadow/online
jobs. Additional sources are optional and may be supplied by API or CLI.

Recommended shape:

```python
class SelfEvolveEvalSourceConfig(BaseConfig):
    kind: Literal["current_trajectory", "trajectory_log", "session", "jsonl", "batch_config"]
    path: Optional[str] = None
    session_id: Optional[str] = None
    task_ids: list[str] = []
    max_cases: int = 100
```

Source semantics:

- `current_trajectory`: use the just-completed run trajectory for diagnostics
  and phase-1 credit assignment.
- `trajectory_log`: read a caller-supplied trajectory log path.
- `session`: mine an existing session/run record.
- `jsonl`: load explicit task/eval cases.
- `batch_config`: reuse a batch job configuration as an eval source.

External files such as `~/Documents/logs/trajectory.log` MAY be used as local
tests or explicit `--from-trajectory` input, but MUST NOT be required by the
self-evolve feature.

### Decision: Phase 1 includes trajectory credit assignment

The phase-1 loop is not complete unless a trajectory can drive target
selection. Self-evolve should introduce a `TrajectoryCreditAssigner` that turns
the current trajectory plus available target inventory into a target selection
report.

Recommended interface:

```python
class TrajectoryCreditAssigner(Protocol):
    async def assign(
        self,
        trajectory: TrajectorySource,
        target_inventory: TargetInventory,
        policy: CreditAssignmentPolicy,
    ) -> TargetSelectionReport: ...
```

The report MUST include:

- selected target identity or explicit `no_target`
- confidence score and threshold decision
- evidence references to trajectory steps, tool calls, LLM calls, scorer
  findings, or generated artifacts
- failure category, such as tool misuse, prompt misunderstanding, missing skill
  guidance, config limit, artifact failure, or insufficient signal
- diagnostic text suitable for the persisted report

Phase-1 assignment should combine deterministic signals and optional LLM
analysis:

- invalid or repeated tool calls can select a `ToolDescriptionTarget`
- misunderstood task constraints can select a `PromptSectionTarget`
- skill invocation failures or missing skill guidance can select a
  `SkillTextTarget`
- generated file failures can select a `WorkspaceArtifactTarget` only when the
  file was produced by agent task execution and passes protected-path checks
- low-confidence or ambiguous evidence MUST produce `no_target` instead of a
  speculative candidate

Explicit CLI/API targets bypass target inference, but the runner should still
record trajectory evidence when a trajectory source is available.

### Decision: Evaluation is a contract, not a single evaluator agent

Self-evolve MUST depend on a pluggable evaluation contract.

Recommended interface:

```python
class EvaluationBackend(Protocol):
    async def evaluate_variant(
        self,
        target: SelfEvolveTarget,
        variant: CandidateVariant,
        dataset: SelfEvolveDataset,
        policy: EvaluationPolicy,
    ) -> EvaluationResult: ...
```

Default backends may use:

- `EvaluateRunner`
- `EvalTarget`
- `Scorer`
- trajectory validators
- LLM-as-judge scorers
- Ralph verification commands
- external benchmark commands

The built-in evaluator agent and `app_evaluator` skill can be optional scorers
for UI/app use cases, but MUST NOT be the only correctness signal.

Evaluation discipline:

- candidate generation may use training/source cases and trajectory feedback
- candidate ranking uses validation metrics
- pass/fail gates use optimizer-held-out test metrics when at least
  `min_eval_cases` are available
- if there are too few cases for a meaningful held-out gate, the run may still
  produce diagnostics and proposal diffs, but MUST mark the candidate confidence
  as limited and MUST NOT label the candidate as verified
- LLM-as-judge metrics SHOULD use fixed prompts, fixed seeds when supported,
  and repeated judgments (`judge_repetitions`) when used as a gate signal
- `min_improvement` applies to the held-out gate, not to training/source cases

Why:

- Self-evolve needs objective tests, trajectory quality, tool selection,
  latency/cost, and regression signals.
- A single evaluator agent is too narrow and can overfit.

### Decision: Candidate generation is pluggable

The framework should not require one optimizer package.

Recommended optimizer interface:

```python
class CandidateOptimizer(Protocol):
    async def propose(
        self,
        target: SelfEvolveTarget,
        dataset: SelfEvolveDataset,
        feedback: SelfEvolveFeedback,
        policy: OptimizerPolicy,
    ) -> list[CandidateVariant]: ...
```

Phase-1 optimizer backends:

- `LLMMutatorOptimizer`: low-dependency fallback, uses an LLM to propose
  candidate text changes from traces and scorer feedback.
- `DSPyOptimizer`: optional integration for GEPA/MIPROv2 when dependencies are
  installed.

Why:

- AWorld can ship the abstraction without forcing DSPy as a hard dependency.
- GEPA-style reflective trace optimization remains possible as an optional
  engine.

The optimizer MUST NOT inspect held-out test cases or held-out judge outputs
while proposing candidates. This prevents the LLM mutator from optimizing
directly against the final gate.

### Decision: Phase 1 optimizes harness text plus isolated agent-produced workspace artifacts

Phase-1 target types:

- `SkillTextTarget`: reads and proposes updates for `SKILL.md`
- `PromptSectionTarget`: targets named prompt sections owned by framework or
  agent definitions
- `ToolDescriptionTarget`: targets tool schema descriptions visible to agents
- `AgentConfigTarget`: targets explicitly whitelisted config knobs
- `WorkspaceArtifactTarget`: targets code or files produced by the agent during
  task execution and live outside framework/runtime/CLI source roots

Phase 1 excludes:

- framework source rewrites under `aworld/`
- CLI core rewrites under `aworld-cli/`
- any self-evolve candidate change to AWorld or `aworld-cli` product logic
- runtime, shared infrastructure, packaging, and secret/config rewrites
- model weight training
- automatic merge/commit by default

Why:

- Text harness artifacts are high leverage and lower risk.
- Some tasks produce workspace-local code or files; optimizing those outputs is
  useful when the trajectory shows artifact-level failures.
- Framework and runtime code evolution needs stronger deterministic tests and a
  stricter review model than phase 1 should provide.

Workspace-local code targets MUST be evaluated in an isolated candidate
workspace or overlay. Any candidate diff touching framework, `aworld-cli`,
runtime, shared infrastructure, package metadata, secret/config paths, or AWorld
product logic MUST fail safety gates. In phase 1, passing candidates still
produce proposal and diff artifacts only.

### Decision: Self-evolve run artifacts are durable and auditable

Every run MUST persist artifacts under a workspace-scoped self-evolve root, for
example:

- `.aworld/self_evolve/<run_id>/run.json`
- `.aworld/self_evolve/<run_id>/target_selection.json`
- `.aworld/self_evolve/<run_id>/baseline.json`
- `.aworld/self_evolve/<run_id>/candidates/<candidate_id>/variant.json`
- `.aworld/self_evolve/<run_id>/candidates/<candidate_id>/diff.patch`
- `.aworld/self_evolve/<run_id>/candidates/<candidate_id>/metrics.json`
- `.aworld/self_evolve/<run_id>/report.md`

Artifacts MUST include:

- target identity and version fingerprint
- target selection report and trajectory evidence, for inferred targets
- dataset identity and split information
- optimizer backend and policy
- baseline metrics
- candidate metrics
- constraint/gate results
- diagnostics and failure summaries
- apply status
- async trigger metadata when the run was created from post-run enqueue
- source identities for current trajectory or optional external eval sources
- run token/cost budget and actual usage

Why:

- Self-evolve should be explainable and reversible.
- Future CLI and UI surfaces need stable data to display.

### Decision: Application is separate from proposal

Phase-1 self-evolve should support this apply mode:

- `proposal`: generate report, candidate files, and reviewable diffs only

Default mode MUST be `proposal`.

Why:

- Candidate generation is useful even before automatic application is trusted.
- Human review should happen before any persistent target mutation.

### Decision: Self-evolve has bounded stopping conditions

Self-evolve MUST avoid unbounded optimization loops. Each run should stop when
any of these conditions is reached:

- `max_iterations` is reached
- the target quality threshold has been met
- no candidate meets `min_improvement`
- quality improves but cost or latency regresses beyond policy
- the same agent/target already has a pending proposal
- repeated gate failures put the target into cooldown
- evaluation signals are insufficient to generate a reliable candidate
- run token or cost budget is exhausted

Why:

- Self-evolve should continuously improve quality and stability, but it should
  not run forever or create repeated low-value proposals.

### Decision: Runs have explicit budget ceilings

Every self-evolve run MUST enforce a whole-run token and cost budget. Candidate
generation, baseline evaluation, candidate evaluation, repeated judge calls, and
verification commands all count against the run budget.

Why:

- Post-run optimization can be triggered frequently.
- Candidate count multiplied by evaluation cases and judge repetitions can grow
  quickly.
- Cost controls must exist at the run level, not only as candidate-level
  latency or cost regression gates.

### Decision: CLI invokes framework self-evolve

`aworld-cli` should provide a single top-level command, backed by framework
APIs:

```bash
aworld-cli optimize \
  --agent Aworld \
  --task "..." \
  --target skill:app_evaluator \
  --dataset evals/ui_apps.jsonl \
  --iterations 5 \
  --apply proposal
```

The command should remain generic. Different optimization surfaces are selected
through an extensible `--target <type>:<id>` scheme rather than separate CLI
commands. Supported target forms should include:

- `skill:<name>`
- `prompt:<section>`
- `tool:<tool-name>`
- `agent-config:<field>`
- `task` for a task-driven target inference flow

Optional source forms:

- `--dataset <jsonl>`
- `--from-session <session-id>`
- `--from-trajectory <path>`
- `--batch-config <yaml>`

Why:

- CLI users need a direct way to optimize a specified task or target and to
  debug the same framework path used by asynchronous post-run jobs.
- The command should not own the optimizer logic.

### Decision: Built-in AWorld main agent can opt in through config

The CLI-built AWorld main agent may enable self-evolve through env/config, such
as:

- `AWORLD_SELF_EVOLVE_MODE=off|offline|shadow|online`

Default behavior remains off.

Why:

- Product users can enable self-evolve for the main agent without affecting SDK
  agents or unrelated CLI sessions.

## Resolved Phase-1 Decisions

- The first CLI surface is a single generic `aworld-cli optimize` command. It
  supports `skill:<name>`, `prompt:<section>`, and `tool:<tool-name>` target
  forms through `--target`, plus approved task-driven target inference.
- Phase 1 stops at proposal and diff artifacts. It does not perform write,
  branch, merge, or commit application, so it produces self-evolve proposals
  rather than a persistent self-modifying loop.
- Workspace-local code/file targets are limited to artifacts produced by the
  agent during task execution. Self-evolve candidates MUST NOT change AWorld
  framework or `aworld-cli` product logic.
- Trajectory-driven credit assignment is in phase 1: task-driven or post-run
  optimize must identify a target from trajectory evidence or record `no_target`
  before candidate generation.
- Existing `train.evolve` remains the training/weight-evolution pipeline; this
  change uses distinct `self_evolve` naming, config, and artifact storage.
