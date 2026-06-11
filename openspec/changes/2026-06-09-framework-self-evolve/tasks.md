## 1. Scope Freeze

- [x] 1.1 Confirm self-evolve is a framework-owned capability, with CLI as a
  caller and UX layer.
- [x] 1.2 Confirm self-evolve is disabled by default for all agents.
- [x] 1.3 Confirm phase 1 excludes framework/runtime/CLI source-code evolution
  while allowing isolated agent-produced workspace artifact optimization.
- [x] 1.4 Confirm the default apply mode is proposal-only.
- [x] 1.5 Confirm evaluation is a pluggable contract, not a hard dependency on
  the built-in evaluator agent.
- [x] 1.6 Confirm post-run self-evolve is asynchronous and must not affect the
  completed task result.
- [x] 1.7 Confirm external trajectory logs are optional eval sources and test
  fixtures, not required product dependencies.
- [x] 1.8 Confirm phase-1 CLI uses one generic `aworld-cli optimize` command
  whose `--target` option supports skill, prompt-section, and tool-description
  target forms.
- [x] 1.9 Confirm phase 1 stops at proposal + diff artifacts, with no write,
  branch, merge, or commit application.
- [x] 1.10 Confirm workspace-local code/file targets are limited to artifacts
  produced by agent task execution and must not touch AWorld or `aworld-cli`
  product logic.
- [x] 1.11 Confirm existing `Runners.evolve(...)` / `train.evolve` remains the
  training-oriented evolution pipeline, while `aworld.self_evolve` owns
  proposal-only harness optimization.
- [x] 1.12 Confirm trajectory-driven credit assignment is a phase-1 core loop,
  not a placeholder.
- [x] 1.13 Confirm opt-in uses `SelfEvolveConfig.mode`; no separate
  `AgentConfig.optimize` flag is added.
- [x] 1.14 Confirm `aworld-skills/app_evaluator/SKILL.md` remains independent
  and protected; self-evolve is a new complete framework subsystem, not an
  app_evaluator upgrade.
- [x] 1.15 Confirm phase 1 is proposal-only and does not claim the persistent
  `apply -> re-evaluate -> accept/rollback` self-evolution loop.
- [x] 1.16 Confirm LLM judge behavior is configurable: default self-evolve
  trajectory judge, explicit `agent.md` judge, or custom judge agent.

## 2. Framework Configuration

- [ ] 2.1 Add `SelfEvolveConfig` to framework config models.
- [ ] 2.2 Add `AgentConfig.self_evolve_config` with `mode="off"` disabled
  defaults and no separate `enabled` or `optimize` flag.
- [ ] 2.3 Add run budget config fields for max tokens, optional max cost, min
  eval cases, judge repetitions, and cooldown.
- [ ] 2.4 Add tests proving existing agent config construction remains backward
  compatible.
- [ ] 2.5 Add tests proving unknown extra model config kwargs still flow through
  existing `llm_config.ext_config` behavior.
- [ ] 2.6 Add tests for mode semantics: `off`, `offline`, `shadow`, and
  `online`.
- [ ] 2.7 Add tests proving phase-1 `online` remains proposal-only and does not
  imply automatic apply.
- [ ] 2.8 Add judge config tests for default trajectory judge, `agent.md`,
  custom agent, and disabled judge modes.

## 3. Self-Evolve Core Package

- [ ] 3.1 Create `aworld/self_evolve/` with stable public imports.
- [ ] 3.2 Define target interfaces and phase-1 target types.
- [ ] 3.3 Define candidate variant, run, metric, diagnostic, and gate result
  models.
- [ ] 3.4 Define optimizer and evaluation backend protocols.
- [ ] 3.5 Define persistent run artifact storage under `.aworld/self_evolve/`.
- [ ] 3.6 Define async trigger/run-context models for post-run enqueue.
- [ ] 3.7 Define `SelfEvolveRun` naming that does not conflict with existing
  `train.evolve.EvolutionRunner` / `EvolutionConfig`.
- [ ] 3.8 Define trace pack, dataset recipe, target provenance, and optimizer
  lineage models.

## 4. Evaluation Integration

- [ ] 4.1 Add a default evaluation backend that can call existing
  `EvaluateRunner`.
- [ ] 4.2 Add support for objective command verification as an evaluation signal.
- [ ] 4.3 Add support for trajectory quality scoring as an evaluation signal.
- [ ] 4.4 Add support for cost and latency metrics.
- [ ] 4.5 Add configurable judge support for the default self-evolve trajectory
  judge, explicit `agent.md` judges, and custom judge agents.
- [ ] 4.6 Add regression tests proving baseline and candidate variants are
  evaluated through the same dataset and scorer policy.
- [ ] 4.7 Add tests proving optional trajectory-log sources are accepted when
  configured but not required by default.
- [ ] 4.8 Add held-out evaluation discipline: select candidates on validation
  metrics and gate verification on optimizer-held-out test metrics when enough
  cases are available.
- [ ] 4.9 Add tests for insufficient eval cases producing limited-confidence
  proposals rather than verified candidates.
- [ ] 4.10 Add tests proving judge-only improvements remain limited-confidence
  and verified candidates require a deterministic/objective signal.

## 5. Dataset Builders

- [ ] 5.1 Add jsonl dataset ingestion for explicit eval cases.
- [ ] 5.2 Add builder support for existing batch job config as an eval source.
- [ ] 5.3 Add current-trajectory, session, and trajectory-log source interfaces
  that can feed phase-1 credit assignment.
- [ ] 5.4 Add deterministic train/validation/test split metadata.
- [ ] 5.5 Add tests for dataset identity and split reproducibility.
- [ ] 5.6 Add a fixture-backed test that can use a trajectory log sample, without
  hard-coding `~/Documents/logs/trajectory.log` into product behavior.
- [ ] 5.7 Add dataset recipe persistence with source filters, synthetic
  generation policy, and trainable vs held-out failure-case separation.
- [ ] 5.8 Add tests proving single-trajectory post-run sources cannot produce a
  verified candidate unless additional eval sources satisfy held-out gates.

## 6. Trajectory Credit Assignment

- [ ] 6.0 Run a credit-assignment spike on real trajectory fixtures with manual
  target labels before expanding the full optimizer pipeline.
- [ ] 6.1 Add `TrajectoryCreditAssigner` and `TargetSelectionReport` models.
- [ ] 6.2 Add `TracePack` normalization/compression before credit assignment.
- [ ] 6.3 Build target inventory for skill, prompt-section, tool-description,
  whitelisted config, and agent-produced workspace artifact targets.
- [ ] 6.4 Implement deterministic trajectory signal extraction using
  trajectory scorers, tool call failures, repeated actions, LLM calls, and
  generated artifact references.
- [ ] 6.5 Add optional LLM-assisted diagnosis that cites trajectory evidence and
  can return `no_target` on low confidence.
- [ ] 6.6 Add tests proving `--task` / current trajectory can infer skill,
  prompt, or tool-description targets, and can decline when evidence is
  insufficient.
- [ ] 6.7 Add target provenance and trust metadata to target inventory.

## 7. Candidate Generation

- [ ] 7.1 Add a low-dependency trace-reflective LLM mutator optimizer for text
  targets.
- [ ] 7.2 Add optional DSPy GEPA and MIPRO optimizer adapters behind dependency
  checks.
- [ ] 7.3 Add candidate fingerprinting and target version fingerprinting.
- [ ] 7.4 Add constraints for skill markdown/frontmatter, prompt section format,
  tool schema description, token limits, and no-op candidate filtering.
- [ ] 7.5 Add tests proving optimizer absence produces a clear configuration
  error, not an import-time framework failure.
- [ ] 7.6 Add workspace-local artifact candidate support for agent-produced
  code/files behind protected-path gates and isolated candidate workspace
  evaluation.
- [ ] 7.7 Ensure optimizers cannot inspect held-out test cases or held-out judge
  outputs.
- [ ] 7.8 Add optimizer lineage tracking with parent candidate ids, mutation
  rationale, and trainable failure cases used.
- [ ] 7.9 Keep Darwinian/code evolution as a future external CLI adapter only;
  do not import AGPL code into AWorld core.

## 8. Gates And Apply Policy

- [ ] 8.1 Add gate policy for minimum score improvement.
- [ ] 8.2 Add gate policy for maximum cost/latency regression.
- [ ] 8.3 Add gate policy for required deterministic verification commands.
- [ ] 8.4 Add proposal-only apply mode.
- [ ] 8.5 Reject write, branch, merge, and commit application modes as
  unsupported in phase 1.
- [ ] 8.6 Ensure proposal mode writes report, candidate files, and diffs only.
- [ ] 8.7 Add protected-path gates for framework, `aworld-cli`, runtime, shared
  infrastructure, package metadata, secret/config paths, and AWorld product
  logic.
- [ ] 8.7A Add protected-path gates for `aworld-skills/app_evaluator/SKILL.md`
  so it cannot be selected as a default target or mutated by candidates.
- [ ] 8.8 Add stopping conditions for max iterations, stalled improvement,
  pending proposal duplicate suppression, and cooldown.
- [ ] 8.9 Add whole-run token and cost budget gates.
- [ ] 8.10 Add held-out verification gates for candidates that claim verified
  improvement.
- [ ] 8.11 Add trust/provenance gates for generated, external, and protected
  targets.
- [ ] 8.12 Add global regression benchmark gates for skill, prompt-section, and
  tool-description targets.
- [ ] 8.13 Add judge-only limited-confidence gates.

## 9. Framework Runner

- [ ] 9.1 Add `SelfEvolveRunner` that orchestrates target loading, dataset
  selection/credit assignment, dataset building, baseline eval, candidate
  generation, candidate eval, gates, and artifact persistence.
- [ ] 9.2 Add a Python API entry point for SDK use.
- [ ] 9.3 Ensure active runtime behavior is not mutated during proposal-only
  runs.
- [ ] 9.4 Add targeted tests for a local fake target and fake optimizer.
- [ ] 9.5 Add tests proving async post-run enqueue failures do not fail or delay
  the completed task response.

## 9A. Async Post-Run Scheduling

- [ ] 9A.1 Add `SelfEvolveScheduler` with best-effort enqueue semantics.
- [ ] 9A.2 Add post-run eligibility checks for
  `self_evolve_config.mode in {"shadow", "online"}`.
- [ ] 9A.3 Add concurrency, timeout, retry, pending-proposal, and cooldown
  controls.
- [ ] 9A.4 Add tests proving scheduler/worker failures do not affect the main
  task result.
- [ ] 9A.5 Persist durable pending jobs before enqueue returns, and test
  short-lived CLI process behavior does not rely on fire-and-forget tasks.

## 10. CLI Integration

- [ ] 10.1 Add a single top-level `aworld-cli optimize` command.
- [ ] 10.2 Support `--agent`, `--task`, `--target`, `--dataset`,
  `--from-session`, `--from-trajectory`, `--batch-config`, `--iterations`, and
  `--apply`.
- [ ] 10.3 Add optional interactive `/optimize` command if the interactive
  command surface is in scope for phase 1.
- [ ] 10.4 Add env/config wiring so the built-in AWorld main agent can opt into
  self-evolve mode.
- [ ] 10.5 Add command tests proving CLI invokes framework APIs rather than
  owning self-evolve logic.
- [ ] 10.6 Add CLI tests proving `--from-trajectory` is optional and passed
  through as an explicit eval source.
- [ ] 10.7 Add CLI tests proving `--task` can invoke framework target inference
  through the same command path.
- [ ] 10.8 Add CLI tests proving command discovery uses the built-in plugin
  manifest/`cli_commands` path.

## 11. Documentation And Examples

- [ ] 11.1 Document framework self-evolve concepts and safety model.
- [ ] 11.2 Document `SelfEvolveConfig.mode` opt-in and its distinction from
  `meta_learning_config`, `ContextRuleConfig.optimization_config`, and
  `train.evolve`.
- [ ] 11.3 Document CLI `optimize` usage.
- [ ] 11.4 Add a minimal example that uses a toy trajectory to infer and
  propose a target improvement, with optional jsonl eval cases.
- [ ] 11.5 Add a note explaining why framework/runtime/CLI logic evolution is
  deferred while agent-produced workspace artifacts remain isolated and gated.
- [ ] 11.6 Document that phase 1 produces self-evolve proposals; the persistent
  self-modifying loop remains a future apply phase.
- [ ] 11.7 Document that `aworld-skills/app_evaluator/SKILL.md` is not part of
  the new self-evolve subsystem and may only be used as an explicitly
  configured read-only scorer/fixture.
- [ ] 11.8 Document which Hermes/GEPA/Darwinian patterns were adopted as
  framework contracts and which remain future adapters.
- [ ] 11.9 Document configurable LLM judge behavior and examples for default
  trajectory judge, `agent.md`, and custom judge agents.
- [ ] 11.10 Document phase-2 closed-loop roadmap:
  `apply -> re-evaluate -> accept/rollback`.
