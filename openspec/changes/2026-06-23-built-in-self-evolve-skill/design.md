# Built-In Self-Evolve Skill Design

## Summary

Add a repository-distributed `self_evolve` skill that gives agents a safe,
repeatable way to use AWorld's framework self-evolve capability. The skill is
not a new optimizer. It is a thin operating manual and workflow adapter that
routes optimization work through `aworld.self_evolve` or `aworld-cli optimize`.

This keeps the existing separation:

- `aworld.self_evolve`: owns target models, trace packs, datasets, candidate
  generation, evaluation, gates, apply policy, scheduler, and artifacts.
- `aworld-cli optimize`: owns the manual CLI invocation surface and delegates
  core behavior to the framework.
- `aworld-skills/self_evolve`: tells agents when and how to use the framework
  surface safely.

## Proposed Files

```text
aworld-skills/self_evolve/
  SKILL.md
  references/
    plan.md
```

`SKILL.md` should stay short and operational. It should contain:

- frontmatter with `name: self_evolve` and a trigger-focused `description`
- usage boundary: use framework/CLI, do not hand-edit as the default path
- default workflow
- target priority order
- safety rules
- expected output/report format
- instruction to read `references/plan.md` only when planning a larger
  self-evolve rollout or explaining the architecture

`references/plan.md` should follow a plan shape:

- Vision
- What Can Be Improved
- Architecture
- Optimization Loop
- AWorld Integration Points
- Safety Gates
- Invocation Forms
- Phases
- Non-goals

## Skill Triggering

The skill should trigger for requests such as:

- "evolve this skill"
- "improve this agent using self-evolve"
- "run self-evolve on this failed trajectory"
- "optimize the tool description based on traces"
- "create a self-evolve proposal for this prompt section"
- "use self-evolve after this run"

It should not trigger for ordinary manual editing requests where the user asks
for a direct, one-off text rewrite and does not want an evidence-backed
optimization loop.

The skill description should avoid broad wording that overlaps with the older
`optimizer` skill. `self_evolve` should own trace-backed, framework-gated,
artifact-producing optimization workflows. The legacy `optimizer` skill may
remain available for direct agent/prompt/tool-configuration optimization
workflows that do not use `aworld.self_evolve`.

## Capability Levels

The skill should not present every planned target tier as currently available.
It should label each path:

- **Available**: the target adapter, dataset path, runner behavior, gates, and
  reporting are implemented and covered by tests.
- **Conditional**: the path exists but requires explicit configuration, such as
  `auto_verified`, an evaluation backend, held-out cases, and a real optimizer.
- **Roadmap**: the target type or workflow is part of the plan but not yet
  wired end to end.

For the initial implementation, `skill:<name>` is the only target tier expected
to be fully operational unless implementation verifies additional target
adapters. Tool, prompt, config, and broad workspace-artifact tiers should be
documented as roadmap or conditional when their framework adapters still raise
`NotImplementedError` or lack end-to-end tests.

## Workflow

The skill should instruct the agent to follow this sequence:

1. Identify whether the user wants diagnostics, proposal generation, or
   verified automatic application.
2. Select or infer the target:
   - explicit user target wins
   - otherwise use framework trajectory credit assignment
   - decline when evidence is insufficient
3. Gather evaluation evidence:
   - dataset path
   - prior session
   - trajectory file
   - current trajectory
   - batch or regression benchmark source
4. Invoke framework self-evolve through one of these paths:
   - Python API for SDK/framework work
   - `aworld-cli optimize` for manual CLI work
5. Require proposal-only behavior by default.
6. For `auto_verified`, first verify the framework path has an evaluation
   backend, held-out cases, deterministic/objective signal, target allowlist,
   budget gates, protected-path gates, and post-apply re-evaluation.
7. If any required auto-apply prerequisite is missing, downgrade to proposal or
   rejected status.
8. Summarize artifacts and next actions:
   - run id
   - target
   - evidence source
   - candidate id
   - metric deltas
   - gate status
   - report path
   - apply status

## Target Tiers

The skill should explain target tiers in a concise, AWorld-specific way.

Tier 1: skill text

- Lowest risk and highest initial value.
- Target form: `skill:<name>`.
- Typical artifact: `SKILL.md`.
- Must preserve valid skill frontmatter and concise procedural guidance.

Tier 2: tool descriptions

- Useful when traces show wrong tool choice, missing tool use, or repeated
  failed tool calls.
- Target form: `tool:<tool-name>`.
- Must not change tool schemas or implementation code.
- Roadmap-only until `ToolDescriptionTarget` is implemented end to end.

Tier 3: prompt sections

- Useful for recurring behavior issues across tasks.
- Target form: `prompt:<section>`.
- Higher blast radius; requires stronger regression evidence.
- Roadmap-only until `PromptSectionTarget` is implemented end to end.

Tier 4: allowlisted agent configuration knobs

- Useful only for framework-approved harness knobs.
- Target form: `agent-config:<field>`.
- Must remain allowlisted by `SelfEvolveConfig`.
- Roadmap-only until `AgentConfigTarget` is implemented end to end.

Tier 5: workspace-local task artifacts

- Only for artifacts produced by agent task execution.
- Must pass provenance and protected-path gates.
- Proposal-only unless a later target policy explicitly permits automatic
  application.

## Safety Model

The skill must repeat the core safety boundary in operational language:

- Do not hand-write changes into the target as the default self-evolve action.
- Do not bypass `SelfEvolveRunner`.
- Do not treat judge-only output as verified improvement.
- Do not expose held-out gate data to mutators.
- Do not target protected paths or product source code.
- Do not target `aworld-skills/self_evolve/SKILL.md` by default; the operating
  skill should not rewrite its own safety instructions without a later
  self-hosting policy.
- Do not auto-apply unless mode, policy, target allowlist, verification,
  regression, budget, provenance, and post-apply gates pass.
- If any gate result is missing, failed, or unavailable, report a proposal or
  rejection instead of applying.

## Invocation Examples

The skill should include examples that are accurate for the current framework
capabilities. At minimum:

```bash
aworld-cli optimize \
  --target skill:example_skill \
  --dataset path/to/eval_cases.jsonl \
  --apply proposal
```

For trajectory-backed target inference:

```bash
aworld-cli optimize \
  --from-trajectory path/to/trajectory.log \
  --infer-target \
  --apply proposal
```

The skill must explain that the CLI fallback mutator may preserve the baseline
when no real optimizer is configured. Examples that claim actual content
improvement must use either a configured framework optimizer or an SDK example
that injects a real `CandidateOptimizer`.

SDK examples should be copy-pasteable and should show `SelfEvolveRunner` or
`optimize_explicit_target(...)` with a caller-supplied optimizer. They should
not show direct file mutation as the self-evolve path.

## Relationship To Existing Skills

`app_evaluator` is an evaluator-style skill. It can inspire packaging style
because it is distributed under `aworld-skills/`, but it is not the
self-evolve implementation and should remain protected from default mutation.

`optimizer` is an older agent optimization skill. The new `self_evolve` skill
should not inherit its AST/tool-specific instructions. It should use the
framework self-evolve path instead. Its frontmatter description should mention
framework gates, trace evidence, proposals, and `aworld-cli optimize` so runtime
skill selection can distinguish it from the broader legacy optimizer.

## Acceptance Criteria

- The built-in skill exists at `aworld-skills/self_evolve/SKILL.md`.
- The plan reference exists at
  `aworld-skills/self_evolve/references/plan.md`.
- The skill frontmatter is valid and trigger-focused.
- The skill body is concise and operational.
- The skill explicitly routes execution through `aworld.self_evolve` or
  `aworld-cli optimize`.
- The skill explicitly prohibits bypassing framework gates.
- The skill explicitly labels unsupported target tiers and unavailable apply
  modes as conditional or roadmap-only.
- The skill includes copy-pasteable CLI and SDK examples that match current
  framework capability.
- The plan reference includes target tiers, workflow, integration points,
  safety gates, phases, and non-goals.
- Tests or validation prove the skill is discoverable through the repository
  skill catalog path used for built-in skills.
- Tests or validation prove the skill and plan reference remain internally
  linked and the self-evolve skill is protected from default self-mutation.
- Self-evolve test coverage still passes after adding the skill.

## Non-Goals

- Do not move framework self-evolve logic into the skill.
- Do not add a second CLI entrypoint.
- Do not implement DSPy, GEPA, MIPRO, or external code evolution in this change.
- Do not import external project code or license-bound implementation details.
- Do not make `app_evaluator` a self-evolve target.
- Do not enable automatic online application by default.
