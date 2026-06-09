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
- agents can opt into eligibility through configuration
- CLI can enable the feature for the built-in AWorld main agent
- CLI can provide commands to optimize a specified task or target

## Goals / Non-Goals

**Goals**

- Make self-evolve a framework-level capability in AWorld.
- Keep the feature disabled by default.
- Add an agent-level opt-in contract, tentatively `AgentConfig.optimize=True`.
- Allow CLI AWorld main agent to enable self-evolve through config or env.
- Provide a CLI command that can optimize a specified task, target, dataset, or
  prior session.
- Support phase-1 optimization targets:
  - skills / `SKILL.md`
  - prompt sections
  - tool descriptions
  - selected agent config knobs
- Reuse existing AWorld evaluation, trajectory, and Ralph verification
  capabilities instead of introducing a parallel evaluation stack.
- Store every evolution run as an auditable artifact with baseline/candidate
  metrics, diff, diagnostics, and approval state.
- Support proposal-only runs before any write/apply path.

**Non-Goals**

- Do not train or fine-tune model weights.
- Do not make self-evolve run automatically for all agents.
- Do not bind self-evolve to the current UI `app_evaluator` skill.
- Do not require a specific optimizer such as DSPy/GEPA for the framework
  contract.
- Do not include code evolution in phase 1.
- Do not let candidate prompt/skill/tool changes silently alter the active
  runtime without gates and explicit application.
- Do not replace existing `EvaluateRunner`, `RalphRunner`, trajectory, or CLI
  batch capabilities.

## Decisions

### Decision: Framework owns the self-evolve core

The new capability should live under a framework package such as:

- `aworld/self_evolve/`

Recommended submodules:

- `config.py`: `SelfEvolveConfig` and related policy models
- `targets.py`: optimization target interfaces and built-in target types
- `datasets.py`: dataset builders from jsonl, batch config, session logs, and
  trajectory artifacts
- `optimizers/`: pluggable candidate generators
- `evaluation.py`: baseline/candidate evaluation orchestration
- `gates.py`: constraints and benchmark gates
- `store.py`: evolution run artifact persistence
- `runner.py`: `SelfEvolveRunner`

Why:

- The same self-evolve loop should work from Python SDK, `aworld-cli`, tests,
  or future services.
- CLI-specific UX should not leak into framework contracts.

### Decision: Agent opt-in is explicit and disabled by default

`AgentConfig` should gain a disabled-by-default self-evolve surface.

Recommended shape:

```python
class SelfEvolveConfig(BaseConfig):
    enabled: bool = False
    mode: Literal["offline", "shadow", "online"] = "offline"
    target_types: list[str] = ["skills", "prompt_sections", "tool_descriptions"]
    auto_apply: bool = False
    max_iterations: int = 5
    min_improvement: float = 0.05
    benchmark_gate: Optional[str] = None

class AgentConfig(BaseConfig):
    optimize: bool = False
    self_evolve_config: SelfEvolveConfig = SelfEvolveConfig()
```

Semantics:

- `optimize=False`: the agent is not eligible for self-evolve.
- `optimize=True`: the agent is eligible for explicit self-evolve workflows, but
  active/background behavior still depends on `self_evolve_config.enabled` and
  mode.
- `self_evolve_config.enabled=False`: no automatic shadow or online behavior is
  active, even when `optimize=True`; explicit framework or CLI optimize
  invocations may still run in offline/proposal mode.
- `mode="offline"`: only explicit optimize runs are allowed.
- `mode="shadow"`: task runs may emit optimization candidates or diagnostics,
  but candidates are not applied.
- `mode="online"`: limited task-local repair may run, but persistent harness
  mutation still requires gates and apply policy.

Why:

- Self-evolve changes harness artifacts and must be opt-in.
- Eligibility and execution mode are separate concerns.

### Decision: Evaluation is a contract, not a single evaluator agent

Self-evolve MUST depend on a pluggable evaluation contract.

Recommended interface:

```python
class EvaluationBackend(Protocol):
    async def evaluate_variant(
        self,
        target: EvolutionTarget,
        variant: CandidateVariant,
        dataset: EvolutionDataset,
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
        target: EvolutionTarget,
        dataset: EvolutionDataset,
        feedback: EvolutionFeedback,
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

### Decision: Phase 1 optimizes text harness artifacts only

Phase-1 target types:

- `SkillTextTarget`: reads and proposes updates for `SKILL.md`
- `PromptSectionTarget`: targets named prompt sections owned by framework or
  agent definitions
- `ToolDescriptionTarget`: targets tool schema descriptions visible to agents
- `AgentConfigTarget`: targets explicitly whitelisted config knobs

Phase 1 excludes:

- arbitrary source-code rewrites
- model weight training
- automatic merge/commit by default

Why:

- Text harness artifacts are high leverage and lower risk.
- Code evolution needs stronger deterministic tests and a stricter review model.

### Decision: Self-evolve run artifacts are durable and auditable

Every run MUST persist artifacts under a workspace-scoped evolution root, for
example:

- `.aworld/evolution/<run_id>/run.json`
- `.aworld/evolution/<run_id>/baseline.json`
- `.aworld/evolution/<run_id>/candidates/<candidate_id>/variant.json`
- `.aworld/evolution/<run_id>/candidates/<candidate_id>/diff.patch`
- `.aworld/evolution/<run_id>/candidates/<candidate_id>/metrics.json`
- `.aworld/evolution/<run_id>/report.md`

Artifacts MUST include:

- target identity and version fingerprint
- dataset identity and split information
- optimizer backend and policy
- baseline metrics
- candidate metrics
- constraint/gate results
- diagnostics and failure summaries
- apply status

Why:

- Self-evolve should be explainable and reversible.
- Future CLI and UI surfaces need stable data to display.

### Decision: Application is separate from proposal

Self-evolve should support at least these apply modes:

- `proposal`: generate report and candidate files only
- `write`: write the selected candidate into the target file after confirmation
- `branch`: create a git branch and apply the selected candidate there

Default mode MUST be `proposal`.

Why:

- Candidate generation is useful even before automatic application is trusted.
- Human review and git rollback should be first-class paths.

### Decision: CLI invokes framework self-evolve

`aworld-cli` should provide a top-level command, backed by framework APIs:

```bash
aworld-cli optimize \
  --agent Aworld \
  --task "..." \
  --target skill:app_evaluator \
  --dataset evals/ui_apps.jsonl \
  --iterations 5 \
  --apply proposal
```

Supported target forms should include:

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

- CLI users need a direct way to optimize a specified task or target.
- The command should not own the optimizer logic.

### Decision: Built-in AWorld main agent can opt in through config

The CLI-built AWorld main agent may enable self-evolve through env/config, such
as:

- `AWORLD_AGENT_OPTIMIZE=1`
- `AWORLD_SELF_EVOLVE=1`
- `AWORLD_SELF_EVOLVE_MODE=offline|shadow|online`

Default behavior remains off.

Why:

- Product users can enable self-evolve for the main agent without affecting SDK
  agents or unrelated CLI sessions.

## Open Questions

- Should the first CLI command support only `skill:<name>` targets, or also
  prompt/tool-description targets?
- Should phase-1 include git branch creation, or stop at proposal + diff files?
- Should evaluation datasets live under `.aworld/evolution/datasets/` or be
  user-provided only in phase 1?
