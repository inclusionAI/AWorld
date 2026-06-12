# Self Evolve

Self-evolve is a framework-owned capability for improving agent-facing harness artifacts from observed task trajectories. Phase 1 is intentionally narrow: it can propose changes to harness text and configuration targets such as skills, prompt sections, tool descriptions, and agent config surfaces. It does not mutate AWorld framework code, runtime code, CLI code, dependency manifests, or repository product logic.

The capability is disabled by default. Agents opt in through `AgentConfig.self_evolve_config`, and the CLI only provides a manual/debug entrypoint for explicit runs.

## Safety Model

Self-evolve is designed around proposal-first operation:

- targets are represented as framework target references with provenance
- optimizers receive trainable trajectory/eval cases, not held-out cases
- gates reject malformed, no-op, unsafe, unverified, judge-only, and regressing candidates
- candidate artifacts and reports are persisted under `.aworld/self_evolve/`
- online automatic application requires `apply_policy="auto_verified"` and post-apply re-evaluation
- framework/runtime/CLI product code paths are protected from mutation

The built-in `aworld-skills/app_evaluator/SKILL.md` evaluator skill is not a default self-evolve target. It is part of the evaluation substrate and is protected from self-mutation in phase 1. Future designs may use it only as an explicitly configured read-only scorer or fixture.

## Configuration

`SelfEvolveConfig.mode` controls whether and when optimization work is scheduled:

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
- `offline`: manual SDK/CLI runs only; no automatic post-run scheduling.
- `shadow`: post-run jobs may be enqueued, but candidates remain proposals.
- `online`: post-run jobs may apply changes only after required verification and post-apply re-evaluation.

`online` requires `apply_policy="auto_verified"`. `auto_verified` requires post-apply re-evaluation so the framework can roll back a candidate that fails verification.

This is separate from older learning and optimization switches. `meta_learning_config` stores and extracts learning knowledge, `ContextRuleConfig.optimization_config` controls context compression/optimization behavior, and `train.evolve` is a training asset. `SelfEvolveConfig` is the opt-in for this framework self-evolve proposal and verification loop.

## Post-Run Flow

After `TaskEventRunner.do_run(...)` saves trajectories and builds the final `TaskResponse`, the runner best-effort enqueues a durable pending self-evolve job when the agent config mode is `shadow` or `online` and a trajectory is available. Enqueue failures are logged and do not alter the completed task response.

The default single-trajectory post-run path uses the current trajectory as evidence. It does not synthesize additional training data by default; synthetic generation is disabled in the dataset recipe unless explicitly added by a future framework feature. A single current trajectory usually produces a limited-confidence proposal unless independent eval sources and deterministic or objective gates are configured.

## Explicit SDK Example

This minimal example uses a toy trajectory, a text target, and a trace-reflective mutator. The optimizer sees only the trainable dataset split produced from the current trajectory.

```python
import asyncio
from pathlib import Path

from aworld.self_evolve import (
    TraceReflectiveLLMMutator,
    optimize_explicit_target,
)
from aworld.self_evolve.targets import SkillTextTarget

trajectory = [
    {
        "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
        "state": {"input": {"content": "Fix login flakiness."}},
        "action": {"content": "The guidance was ambiguous."},
        "reward": {"status": "failed"},
    }
]

target = SkillTextTarget(Path("skills/login/SKILL.md"))
optimizer = TraceReflectiveLLMMutator(
    mutate_text=lambda prompt: {
        "content": target.load_current_content() + "\nAdd stricter login retry guidance.\n",
        "rationale": "Trajectory shows missing retry guidance.",
    }
)

result = asyncio.run(
    optimize_explicit_target(
        workspace_root=".",
        run_id="manual-login-optimization",
        target=target,
        current_trajectory=trajectory,
        task_id="login-task",
        optimizer=optimizer,
        apply_policy="proposal",
    )
)
```

For independent eval cases, provide JSONL rows with `input`, optional `expected_output`, and optional `verification_command`, then pass that file through `aworld-cli optimize --target <target> --dataset eval.jsonl` or the equivalent framework dataset source.

## Judge Behavior

The default judge mode is trajectory-based. Configure judge behavior with `SelfEvolveConfig.judge_config`:

- `trajectory`: use the default trajectory judge backend.
- `agent_md`: load a markdown judge agent from `agent_path`.
- `custom_agent`: call a configured custom judge agent identified by `agent_id`.
- `disabled`: disable LLM judge signals.

Judge verdicts are evidence, not sufficient proof by themselves. Gates require structured verification and regression checks before accepting a candidate.

## Pattern Boundaries

Phase 1 adopts the useful parts of Hermes, GEPA, and Darwinian optimization patterns while keeping unsafe parts out of the default loop:

- Hermes-like reflection is used to diagnose trajectory failures and target likely harness causes.
- GEPA-like proposal generation is represented as an optimizer adapter boundary; optional DSPy integration remains dependency-gated.
- Darwinian/code evolution is deferred to future external adapters and is not allowed to rewrite AWorld framework/runtime/CLI code.

## Online Closed Loop

The intended online closed-loop path is:

1. A task completes and returns its normal response.
2. The runner persists trajectory evidence and enqueues a pending self-evolve job.
3. A worker drains the durable job outside the user response path.
4. The framework builds a dataset, selects a target, proposes candidates, evaluates gates, and writes artifacts.
5. In `shadow` mode the best candidate remains a proposal.
6. In `online` mode the framework may apply an allowlisted target only when verification passes, then re-evaluates and rolls back on failure.

Online mode is unattended after enablement. Operators should enable it only for allowlisted targets with reliable verification and rollback.
