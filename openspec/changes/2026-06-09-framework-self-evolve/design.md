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
- CLI provides one generic manual/debug command that invokes the framework
  capability for a specified task or target

In this change, "self-evolve" means improving the agent-facing harness that
shapes future runs: skill text, prompt sections, tool descriptions, selected
configuration knobs, and isolated task artifacts. It does not mean training
model weights, replacing the agent policy, or changing framework/runtime product
code. The default single-trajectory post-run path is expected to produce
diagnostics and limited-confidence proposals unless independent evaluation
sources and deterministic/objective gates are configured.

## Goals / Non-Goals

**Goals**

- Make self-evolve a framework-level capability in AWorld.
- Keep the feature disabled by default.
- Add an agent-level opt-in contract using `AgentConfig.self_evolve_config.mode`.
- Support asynchronous post-run self-evolve for opted-in agents after a
  trajectory is produced, without blocking or changing the main task result.
- Make trajectory-driven credit assignment the phase-0 go/no-go gate and a
  phase-1 core capability: the framework must inspect the trajectory, choose an
  evidence-backed target, and either produce a proposal/diff or record why no
  reliable target exists. The rest of the optimizer pipeline must not expand
  until the spike demonstrates acceptable target-selection precision/recall on
  real labeled trajectories.
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
  metrics, diff, diagnostics, apply state, acceptance state, and rejection or
  rollback state.
- Support proposal-only runs that emit report and diff artifacts.
- Support at least one explicit automatic evolve mode: `online` must apply a
  verified candidate for allowlisted targets after gates and post-apply
  re-evaluation pass.

**Non-Goals**

- Do not train or fine-tune model weights.
- Do not claim phase-1 harness-text optimization is equivalent to agent policy
  or model self-improvement.
- Do not replace or rename the existing `Runners.evolve(...)` /
  `train.evolve.EvolutionRunner` training pipeline.
- Do not make self-evolve run automatically for all agents.
- Do not bind self-evolve to the current UI `app_evaluator` skill.
- Do not modify, replace, or implicitly optimize
  `aworld-skills/app_evaluator/SKILL.md`; that existing skill remains a
  separate app-evaluation capability.
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
- `AgentConfig.meta_learning_config` and
  `ContextRuleConfig.optimization_config` already describe adjacent
  learning/optimization surfaces.

This change MUST NOT overload those assets. The boundary is:

- existing `train.evolve`: model/data/tool-synthesis training workflows,
  including dependencies such as `transformers`, `verl`, and `AgentTrainer`
- new `aworld.self_evolve`: controlled harness optimization for skills, prompt
  sections, tool descriptions, whitelisted config knobs, and agent-produced
  workspace artifacts, with proposal-only and verified-auto-apply policies

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

### Decision: app_evaluator remains independent

The existing `aworld-skills/app_evaluator/SKILL.md` capability MUST remain
independent from this change. Self-evolve should build a complete new subsystem
under `aworld.self_evolve` instead of evolving the app-evaluator skill into the
new framework feature.

Rules:

- `aworld-skills/app_evaluator/SKILL.md` is a protected path for phase 1.
- It MUST NOT be selected as a default self-evolve target.
- It MUST NOT be modified by generated candidates, proposal application, or
  framework wiring.
- It MAY be used only as an optional read-only scorer or fixture in UI/app
  evaluation scenarios, and only when explicitly configured.
- Self-evolve evaluation MUST remain based on framework contracts such as
  `EvaluationBackend`, `Scorer`, trajectory scorers, deterministic verification,
  and held-out gates, not on app_evaluator-specific behavior.

Why:

- `app_evaluator` is an existing skill-level capability with its own behavior
  and users.
- Self-evolve should be a reusable framework subsystem, not a hidden extension
  of one evaluator skill.

### Decision: Framework owns the self-evolve core

The new capability should live under a framework package such as:

- `aworld/self_evolve/`

Recommended submodules after the credit-assignment gate passes:

- `config.py`: `SelfEvolveConfig` and related policy models
- `targets.py`: optimization target interfaces and built-in target types
- `datasets.py`: dataset builders from jsonl, batch config, session logs, and
  trajectory artifacts
- `trace_pack.py`: trajectory normalization, compression, and evidence packing
- `credit_assignment.py`: trajectory analysis and target selection
- `optimizers/`: pluggable candidate generators
- `evaluation.py`: baseline/candidate evaluation orchestration
- `gates.py`: constraints and benchmark gates
- `provenance.py`: target/source provenance, protected target registry, and
  trust metadata
- `scheduler.py`: best-effort asynchronous post-run enqueue and worker control
- `store.py`: self-evolve run artifact persistence
- `runner.py`: `SelfEvolveRunner`

The first vertical slice should be narrower than the final package shape:
credit-assignment spike, config, `SkillTextTarget`, trace packaging, a simple
LLM mutator, one deterministic/objective evaluation signal, proposal-only
artifact persistence, and an explicit target CLI/API path. Async scheduling,
provenance expansion, DSPy/GEPA adapters, non-skill targets, and online
auto-apply should follow only after that slice proves useful.

Why:

- The same self-evolve loop should work from Python SDK, `aworld-cli`, tests,
  or future services.
- CLI-specific UX should not leak into framework contracts.

### Decision: Absorb Hermes self-evolution ideas as framework contracts, not as a dependency

The Hermes Agent self-evolution plan and local Hermes implementation contain
useful patterns, but AWorld should absorb them into a native framework
subsystem instead of depending on Hermes code or copying its repository shape.

Patterns to adopt:

- **External-supervisor discipline:** the optimizer must operate on explicit
  targets and run artifacts instead of modifying the live agent path.
- **Tiered target maturity:** start with text harness artifacts, then tool
  descriptions and prompt sections, and keep code evolution behind stronger
  isolated evaluation.
- **Trace-reflective optimization:** traces are not just logs; they are
  evidence for failure diagnosis and candidate mutation.
- **Organism/evaluator/mutator model:** a target baseline plus candidate
  variants form the organism, evaluation backends are the evaluator, and
  optimizers are mutators.
- **Held-out discipline:** trainable failure cases may be shown to the mutator,
  while held-out cases remain hidden for final gates.
- **Lineage artifacts:** optimization should persist candidate ancestry,
  failure cases, metrics, and reports so applied changes can be audited without
  requiring approval in the online path.
- **Provenance and trust gates:** generated or externally sourced artifacts need
  explicit provenance, protected target lists, and security/content scans before
  they can be trusted even as proposals.

Patterns not to adopt in phase 1:

- automatic PR creation or branch application
- live mutation of active runtime harnesses
- importing AGPL code evolution libraries into AWorld core
- using a single evaluator skill as the framework's correctness mechanism

Why:

- The useful part is the loop architecture and safety discipline, not the exact
  implementation packaging.
- AWorld already has `EvaluateRunner`, trajectory capture, scorers, Ralph, and
  CLI hooks; the design should compose those native assets.

### Decision: Agent opt-in is explicit and disabled by default

`AgentConfig` should gain one disabled-by-default self-evolve surface.
`SelfEvolveConfig.mode` controls both eligibility and execution behavior.

Recommended shape:

```python
class SelfEvolveJudgeConfig(BaseConfig):
    kind: Literal["default_trajectory", "agent_md", "custom_agent", "none"] = "default_trajectory"
    agent_path: Optional[str] = None
    agent_name: Optional[str] = None

class SelfEvolveConfig(BaseConfig):
    mode: Literal["off", "offline", "shadow", "online"] = "off"
    apply_policy: Literal["proposal", "auto_verified"] = "proposal"
    auto_apply_target_types: list[str] = ["skills"]
    target_types: list[str] = [
        "skills",
        "prompt_sections",
        "tool_descriptions",
        "agent_config",
        "workspace_artifacts",
    ]
    eval_sources: list[SelfEvolveEvalSourceConfig] = []
    judge: SelfEvolveJudgeConfig = SelfEvolveJudgeConfig()
    require_deterministic_signal_for_verified: bool = True
    regression_benchmarks: list[str] = []
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
  self-evolve work. When `apply_policy="auto_verified"`, the runner must apply a
  candidate automatically only after it passes all verification, regression,
  protected-path, budget, and post-apply re-evaluation gates. The completed task
  result MUST NOT change retroactively.
- Phase-1 automatic apply should start with a narrow allowlist, such as
  `SkillTextTarget` only. Other targets may still produce proposals until their
  target-specific apply policy is implemented and verified.

Why:

- Self-evolve changes harness artifacts and must be opt-in.
- A separate `optimize` or `enabled` flag would duplicate `mode="off"` and
  create contradictory states such as `optimize=False` with `mode="shadow"`.
- `shadow` keeps the review-only workflow. `online` is the real controlled
  self-evolve mode and must close the loop for at least one allowlisted target.

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

Concrete integration point:

- `Runners.run(...)` in `aworld/runner.py` constructs a `Task` and delegates to
  `Runners.run_task(...)`; it should not own trajectory extraction.
- `TaskEventRunner.do_run(...)` in `aworld/runners/event_runner.py` is the
  first concrete completion path where both `_save_trajectories()` has populated
  `TaskResponse.trajectory` and `_response()` has copied `llm_calls`.
- The post-run enqueue hook should live immediately after `resp =
  self._response()` and before response finalization/return side effects, or in
  an equivalent helper called from that point. It must be wrapped in a broad
  best-effort guard so enqueue failures are logged as diagnostics and never
  replace `resp`.

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

### Decision: Trajectory evidence is normalized before credit assignment

Self-evolve should not feed raw, oversized, provider-specific trajectory blobs
directly into credit assignment or optimizers. A `TracePack` stage should
normalize and compress trajectory evidence while preserving optimization signal.

Trace packs MUST preserve:

- task input, final answer, status, and error summary
- first turns that define the task, system context, and initial tool inventory
- final turns that show failure, completion, or verification state
- tool calls, tool results, exit codes, and failed tool arguments
- LLM call summaries, usage/cost metadata, and reasoning/tool-selection evidence
- generated artifact references and workspace paths
- scorer findings and deterministic verification outputs

Trace packs MAY summarize middle turns when needed for budget, but summaries
must include evidence references so target selection reports can cite specific
steps.

Why:

- Hermes' trajectory compression pattern preserves first and final turns while
  summarizing middle context; that is a good fit for AWorld's run budget and
  evidence-citation needs.
- GEPA-style reflective optimization depends on traces being compact enough for
  an LLM while still carrying failure signal.

### Decision: Phase 1 includes trajectory credit assignment

The phase-1 loop is not complete unless a trajectory can drive target
selection. Before building the full optimizer pipeline, phase 0 MUST run a
credit-assignment spike on real trajectories with manually labeled expected
targets and `no_target` cases. If the spike cannot reach the configured
precision/recall threshold, implementation MUST stop at diagnostics and explicit
target-only proposal experiments.

After that gate passes, self-evolve should introduce a
`TrajectoryCreditAssigner` that turns the current trajectory plus available
target inventory into a target selection report.

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

### Decision: Targets use provenance and trust metadata

Every optimization target should carry provenance metadata in addition to its
identity and fingerprint.

Recommended fields:

- source kind: bundled framework, CLI product, user workspace, generated
  artifact, external registry, or test fixture
- write origin: foreground user request, agent-produced artifact, background
  self-evolve job, or imported fixture
- trust level: framework-owned, user-owned, generated, external trusted,
  external community
- protected status and reason

Phase-1 behavior:

- framework, CLI product, runtime, secrets/config, package metadata, and
  `aworld-skills/app_evaluator/SKILL.md` are protected
- externally sourced skill or prompt artifacts require static security/content
  scan before becoming candidates
- generated workspace artifacts are eligible only when trajectory evidence shows
  the agent produced them during the task
- user-authored foreground artifacts must not be silently treated as
  self-evolve-managed artifacts

Why:

- Hermes separates usage/provenance sidecars from `SKILL.md`; AWorld should use
  the same idea for self-evolve metadata instead of polluting target files.
- Provenance makes it possible to build automatic curation later without
  confusing user-authored content with background-generated content.

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
- a self-evolve-owned trajectory judge
- user-specified judge agents loaded from `agent.md` or custom agent
  definitions
- Ralph verification commands
- external benchmark commands

The built-in evaluator agent and `app_evaluator` skill can be optional read-only
scorers for UI/app use cases, but MUST NOT be the only correctness signal and
MUST NOT be mutated by self-evolve.

Judge configuration:

- default: `default_trajectory`, a self-evolve-owned judge that reads trace
  packs, target-selection reports, baseline/candidate outputs, and scorer
  diagnostics
- `agent_md`: load a user-specified judge from an explicit `agent.md` path
- `custom_agent`: use a caller-specified agent definition or registered agent
  name
- `none`: disable LLM judge signals for runs that rely only on deterministic
  evaluation

User-supplied judges are evaluation signals, not optimization owners. They MUST
receive compact trace packs and candidate outputs through the evaluation
contract, and their findings MUST be persisted as judge artifacts. They MUST NOT
receive held-out judge outputs from earlier candidates and MUST NOT be the only
gate for a verified improvement.

Evaluation discipline:

- candidate generation may use training/source cases and trajectory feedback
- candidate ranking uses validation metrics
- pass/fail gates use optimizer-held-out test metrics when at least
  `min_eval_cases` are available
- single-trajectory post-run jobs usually have too few cases for held-out
  verification; they may produce proposals and target-selection diagnostics,
  but they should be marked limited-confidence unless additional dataset,
  session, batch, or benchmark sources are attached
- the credit-assignment spike is a hard prerequisite to candidate generation,
  async post-run scheduling, or automatic application
- if there are too few cases for a meaningful held-out gate, the run may still
  produce diagnostics and proposal diffs, but MUST mark the candidate confidence
  as limited and MUST NOT label the candidate as verified
- LLM-as-judge metrics SHOULD use fixed prompts, fixed seeds when supported,
  and repeated judgments (`judge_repetitions`) when used as a gate signal
- LLM-judge-only improvements MUST remain limited-confidence even when repeated
  judgments agree. A verified improvement MUST include at least one
  deterministic signal, such as command verification, exact/objective scoring,
  or a configured regression benchmark.
- `min_improvement` applies to the held-out gate, not to training/source cases

Global harness-text target discipline:

- `SkillTextTarget`, `PromptSectionTarget`, and `ToolDescriptionTarget` are
  shared across tasks, so a single trajectory is not sufficient to prove a
  global behavior improvement.
- Verified candidates for global targets MUST pass configured regression
  benchmarks that are independent from the source trajectory or source task.
- If no regression benchmark is configured for a global target, candidates may
  still be persisted as proposals, but MUST remain limited-confidence.

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

- `TraceReflectiveMutator`: low-dependency fallback, uses an LLM to propose
  candidate text changes from traces and scorer feedback.
- `DSPyGEPAOptimizer`: optional primary adapter for trace-reflective prompt,
  skill, and tool-description optimization when dependencies are installed.
- `DSPyMIPROOptimizer`: optional fallback for instruction text and few-shot
  examples when enough examples exist.
- `DarwinianExternalOptimizer`: future external CLI/subprocess adapter for
  workspace-local code artifacts only; never import AGPL code into AWorld core.

Why:

- AWorld can ship the abstraction without forcing DSPy as a hard dependency.
- GEPA-style reflective trace optimization remains possible as an optional
  engine.
- Different target types need different search strategies, but they should all
  plug into the same candidate/evaluation/gate contracts.

The optimizer MUST NOT inspect held-out test cases or held-out judge outputs
while proposing candidates. This prevents the LLM mutator from optimizing
directly against the final gate.

### Decision: Dataset recipes are first-class artifacts

Self-evolve should persist how an evaluation dataset was built, not only the
resulting cases. A `DatasetRecipe` should describe source selection, filters,
splits, synthetic generation policy, and holdout policy.

Recipe sources may include:

- current trajectory
- session/task history
- explicit jsonl golden cases
- batch configs
- synthetic cases derived from observed failure categories

Artifacts should include trainable failure cases and held-out failure cases
separately. Mutators may see trainable failure cases; gates use held-out cases.

Why:

- Hermes' plan treats session mining, synthetic/golden sources, and train/val/
  test split as core infrastructure. AWorld needs the same reproducibility for
  auditable self-evolve runs.

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
  stricter deterministic review model than the first online apply slice should
  carry.

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
- target provenance and trust metadata
- target selection report and trajectory evidence, for inferred targets
- dataset identity and split information
- dataset recipe and source filters
- optimizer backend and policy
- optimizer lineage, parent candidate ids, and mutation rationale
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

### Decision: Application policy is explicit

Phase-1 self-evolve should support these apply policies:

- `proposal`: generate report, candidate files, and reviewable diffs only
- `auto_verified`: for `mode="online"` only, automatically apply the selected
  candidate when every verification, regression, protected-path, budget,
  provenance, and post-apply re-evaluation gate passes

Default mode MUST be `proposal`.

Initial automatic apply scope:

- The first automatic apply slice should be `SkillTextTarget` only.
- `PromptSectionTarget`, `ToolDescriptionTarget`, `AgentConfigTarget`, and
  `WorkspaceArtifactTarget` may remain proposal-only until their regression
  benchmarks and apply/rollback mechanics are proven.
- Framework source, CLI source, runtime source, package metadata,
  `aworld-skills/app_evaluator/SKILL.md`, secrets/config paths, and AWorld
  product logic remain protected and ineligible for automatic apply.

Why:

- Candidate generation is useful in `shadow` even before automatic application
  is trusted.
- `online` must provide real self-evolution for a narrow, verified path rather
  than only accumulating proposals.
- Automatic application must be explicit, allowlisted, gate-driven, auditable,
  reversible, and unattended once enabled.

### Decision: Online mode closes a narrow unattended self-evolution loop

Phase 1 should include a narrow automatic self-evolution loop for at least one
allowlisted target, but this belongs after the phase-1a proposal-only vertical
slice proves target selection and candidate evaluation quality. The controlled
loop is:

1. select a target from trajectory evidence or an explicit request
2. generate and evaluate candidates against validation data
3. gate the selected candidate with held-out evaluation, deterministic/objective
   signal, global regression benchmark, protected-path checks, and budget checks
4. apply the candidate in an isolated branch, overlay, or managed workspace
   depending on target policy
5. re-run held-out evaluation and global regression benchmarks after apply
6. accept the change only if post-apply metrics still pass gates
7. record lineage from candidate proposal to applied version
8. roll back automatically or mark the candidate rejected when post-apply
   metrics regress

This loop MUST be unattended after the operator enables `online` mode and
`apply_policy="auto_verified"`. It MUST NOT wait for human review, approval,
confirmation, or intervention between candidate selection and application.

Later phases may broaden automatic apply to more target types, but broad
application is not required to prove the initial online mode.

Why:

- The user's intended value is `propose -> apply -> measure -> accept/rollback`.
- Keeping automatic apply narrow avoids confusing "can evolve safely" with
  "can rewrite any harness artifact".

### Decision: Broader apply and product-code evolution remain later phases

Later changes may define broader application modes:

1. apply a verified candidate in a persistent branch or managed workspace
2. re-run held-out evaluation and global regression benchmarks after apply
3. accept the change only if post-apply metrics still pass gates
4. record lineage from proposal to applied version
5. roll back automatically or mark the candidate rejected when post-apply
   metrics regress

Why:

- Framework, CLI, runtime, and broad workspace code evolution need stronger
  tests and review gates than the first online apply slice should carry.

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

`aworld-cli` should provide exactly one phase-1 entrypoint for self-evolve: a
single top-level command backed by framework APIs:

```bash
aworld-cli optimize \
  --agent Aworld \
  --task "..." \
  --target skill:demo \
  --dataset evals/ui_apps.jsonl \
  --iterations 5 \
  --apply auto_verified
```

The command should remain generic. Different optimization surfaces are selected
through an extensible `--target <type>:<id>` scheme rather than separate CLI
commands. The first stable explicit target forms should include:

- `skill:<name>`
- `prompt:<section>`
- `tool:<tool-name>`

The framework target model may also support agent-config and task-driven
inference surfaces, but the first CLI version should avoid separate
target-specific commands and should not require additional explicit target
forms beyond `skill`, `prompt`, and `tool`.

Optional source forms:

- `--dataset <jsonl>`
- `--from-session <session-id>`
- `--from-trajectory <path>`
- `--batch-config <yaml>`

Why:

- CLI users need a direct way to optimize a specified task or target and to
  debug the same framework path used by asynchronous post-run jobs.
- The command should not own the optimizer logic.
- The CLI should not own self-evolve scheduler, evaluator, optimizer, target
  inference, durable artifacts, or agent opt-in semantics.

Registration:

- The command should follow the existing built-in plugin pattern under
  `aworld-cli/src/aworld_cli/builtin_plugins/*_cli/`, with a plugin manifest
  exposing a `cli_commands` entrypoint.
- A command class may live under `aworld_cli.top_level_commands` for reuse, but
  `register_builtin_top_level_commands` is currently a no-op stub and should
  not be treated as the registration mechanism.

### Decision: CLI does not own agent opt-in

Agent opt-in belongs to framework `AgentConfig.self_evolve_config`. CLI may
load or pass normal framework agent configuration when invoking
`aworld-cli optimize`, but phase 1 should not add a separate CLI-owned
self-evolve mode for the built-in AWorld main agent.

Why:

- The self-evolve core must remain usable from the framework SDK without CLI
  internals.
- Keeping CLI to one command prevents product UX from becoming the owner of
  framework learning semantics.

## Resolved Phase-1 Decisions

- The first CLI surface is a single generic `aworld-cli optimize` command. It
  supports `skill:<name>`, `prompt:<section>`, and `tool:<tool-name>` target
  forms through `--target`, plus configured task-driven target inference.
- `aworld-cli optimize` is the only CLI entrypoint in phase 1. Interactive
  slash commands, CLI-owned env modes, scheduler ownership, evaluator logic, and
  target inference remain out of CLI scope.
- Default and `shadow` runs stop at proposal and diff artifacts.
- `online` with `apply_policy="auto_verified"` must support automatic
  apply/re-evaluate/accept-or-rollback for at least one allowlisted target type,
  initially `SkillTextTarget`.
- Delivery order is gated: phase 0 proves credit assignment on labeled real
  trajectories; phase 1a ships the thinnest proposal-only `SkillTextTarget`
  vertical slice; async scheduling, broad provenance, optional DSPy adapters,
  non-skill targets, and `online` auto-apply come after that evidence exists.
- Workspace-local code/file targets are limited to artifacts produced by the
  agent during task execution. Self-evolve candidates MUST NOT change AWorld
  framework or `aworld-cli` product logic.
- `aworld-skills/app_evaluator/SKILL.md` remains protected and independent; it
  is not a default target and is not modified by this change.
- Trajectory-driven credit assignment is in phase 1: task-driven or post-run
  optimize must identify a target from trajectory evidence or record `no_target`
  before candidate generation.
- Existing `train.evolve` remains the training/weight-evolution pipeline; this
  change uses distinct `self_evolve` naming, config, and artifact storage.
