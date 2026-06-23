## 1. Scope And Placement

- [x] 1.1 Confirm the built-in self-evolve skill is an agent-facing operating
  guide and not a second implementation of the self-evolve engine.
- [x] 1.2 Confirm the canonical repository path is
  `aworld-skills/self_evolve/SKILL.md`.
- [x] 1.3 Confirm the skill may reference `aworld.self_evolve` and
  `aworld-cli optimize` as execution surfaces.
- [x] 1.4 Confirm `aworld-skills/app_evaluator/SKILL.md` remains protected and
  independent.
- [x] 1.5 Confirm `aworld-skills/self_evolve/SKILL.md` is protected from
  default self-mutation.
- [x] 1.6 Confirm current framework readiness before writing skill guidance:
  runner gates, worker drain path, target adapters, CLI optimizer behavior, and
  apply policies.
- [x] 1.7 Mark any unsupported or partially wired path as conditional or
  roadmap-only in the skill and plan.

## 2. Skill Content

- [x] 2.1 Create `aworld-skills/self_evolve/SKILL.md`.
- [x] 2.2 Add frontmatter with `name: self_evolve` and a description that
  triggers for evolving skills, prompt sections, tool descriptions, harness
  config, and trajectory-backed self-evolve proposals.
- [x] 2.3 Document that the skill MUST route work through framework
  self-evolve APIs or `aworld-cli optimize`.
- [x] 2.4 Document the default workflow: clarify goal, select or infer target,
  gather evaluation evidence, invoke framework self-evolve, inspect gates, and
  report artifacts.
- [x] 2.5 Document target tier priority: skill text, tool descriptions, prompt
  sections, allowlisted agent config, and workspace-local task artifacts.
- [x] 2.5A Label target tiers as available, conditional, or roadmap according to
  current framework implementation status.
- [x] 2.6 Document default proposal-only behavior.
- [x] 2.7 Document that `auto_verified` may be used only through framework
  gates and explicit policy.
- [x] 2.8 Document expected output fields: run id, target, evidence source,
  candidate id, metric deltas, gate status, report path, and apply status.
- [x] 2.9 Add copy-pasteable `aworld-cli optimize` examples for the available
  proposal-only paths.
- [x] 2.10 Add a copy-pasteable SDK example that injects a real
  `CandidateOptimizer` instead of implying the CLI fallback mutator improves
  content by itself.
- [x] 2.11 Add text explaining that CLI fallback behavior may preserve the
  baseline when no real optimizer is configured.
- [x] 2.12 Write the skill description narrowly enough to distinguish
  framework-gated self-evolve from the existing `optimizer` skill.

## 3. Plan Reference

- [x] 3.1 Create
  `aworld-skills/self_evolve/references/plan.md`.
- [x] 3.2 Add a Vision section explaining that the skill operates on AWorld
  harness artifacts through framework self-evolve.
- [x] 3.3 Add a What Can Be Improved section with target tiers and examples.
- [x] 3.4 Add an Architecture section showing the separation between skill,
  CLI, and framework.
- [x] 3.5 Add an Optimization Loop section covering target selection, dataset
  building, candidate generation, evaluation, gates, and artifacts.
- [x] 3.6 Add an AWorld Integration Points section covering trace packs,
  credit assignment, datasets, evaluation backends, gates, scheduler, and
  `.aworld/self_evolve/` artifacts.
- [x] 3.7 Add a Safety Gates section that mirrors framework gate requirements.
- [x] 3.8 Add a Phases section that starts with skill evolution and defers
  broader prompt/config/artifact evolution behind evidence and gates.
- [x] 3.9 Add a Non-goals section excluding framework source evolution,
  separate optimizer implementation, and protected skill mutation.
- [x] 3.10 Add a Framework Readiness section that distinguishes currently
  available, conditional, and roadmap-only paths.
- [x] 3.11 Add invocation examples that match current CLI and SDK behavior.

## 4. Discovery And Validation

- [x] 4.1 Add or update a test proving repository-distributed skills include
  `self_evolve` when the built-in skill path is loaded.
- [x] 4.2 Add a lightweight validation test that the skill has required
  frontmatter and no broken reference to `references/plan.md`.
- [x] 4.3 Add or update protected-path/provenance tests proving
  `aworld-skills/self_evolve/SKILL.md` is not a default mutable target.
- [x] 4.4 Add or update tests proving roadmap target tiers are not advertised as
  available when their target adapters are still skeletons.
- [x] 4.5 Run the relevant skill provider tests.
- [x] 4.6 Run self-evolve tests to ensure the new skill does not alter
  framework behavior.
- [x] 4.7 Run OpenSpec validation for this change.

## 5. Documentation And Completion

- [x] 5.1 Update any built-in skill index or CLI docs if repository skills are
  listed explicitly.
- [x] 5.2 Record that the skill is an operating surface over framework
  self-evolve, not the implementation owner.
- [ ] 5.3 Commit the spec and follow-up implementation separately unless the
  user asks to combine them.
