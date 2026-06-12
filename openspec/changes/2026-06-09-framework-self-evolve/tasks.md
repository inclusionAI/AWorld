## 0. Credit-Assignment Spike Gate

- [x] 0.1 Collect real trajectory fixtures covering skill, prompt-section,
  tool-description, config, workspace-artifact, success, and ambiguous
  `no_target` outcomes.
- [x] 0.1A Use `~/Documents/logs/trajectory.log` as the initial source for
  task records, then commit sanitized/generated fixture cases rather than making
  tests depend on the developer-local path.
- [x] 0.2 Add manual labels for expected target/no-target decisions, rationale,
  and evidence step ids.
- [x] 0.3 Measure deterministic signals plus optional LLM-assisted diagnosis for
  target-selection precision/recall and `no_target` rejection.
- [x] 0.3A Seed the first regression benchmark dataset from task records in
  `~/Documents/logs/trajectory.log`, including source fingerprint, extraction
  filters, split seed, and train/validation/held-out case ids.
- [x] 0.4 Make the go/no-go decision explicit before building candidate
  generation, async scheduling, broad provenance, non-skill targets, DSPy
  adapters, or online automatic apply.
- [x] 0.5 If the spike fails, limit phase 1 to diagnostics and explicit-target
  proposal experiments until the credit-assignment approach is improved.

  2026-06-11 update: the initial seed from `~/Documents/logs/trajectory.log`
  was supplemented with minimized historical trajectory records to cover skill,
  prompt-section, tool-description, config, workspace-artifact, success, and
  ambiguous `no_target` cases. The spike report is
  `tests/self_evolve/fixtures/credit_assignment_cases/spike_report.json` and
  records a `go` decision for the minimized phase-0 benchmark. The next work may
  start the phase-1a explicit-target proposal-only slice; async scheduling,
  broad provenance expansion, DSPy adapters, non-skill target expansion, and
  online automatic apply remain deferred until phase-1a proves useful.

## 1. Scope Freeze

- [x] 1.1 Confirm self-evolve is a framework-owned capability, with CLI as a
  caller and UX layer.
- [x] 1.2 Confirm self-evolve is disabled by default for all agents.
- [x] 1.3 Confirm phase 1 excludes framework/runtime/CLI source-code evolution
  while allowing isolated agent-produced workspace artifact optimization.
- [x] 1.4 Confirm the default apply mode is proposal-only, while `online` with
  verified apply policy can automatically evolve allowlisted targets.
- [x] 1.5 Confirm evaluation is a pluggable contract, not a hard dependency on
  the built-in evaluator agent.
- [x] 1.6 Confirm post-run self-evolve is asynchronous and must not affect the
  completed task result.
- [x] 1.7 Confirm external trajectory logs are optional eval sources and test
  fixtures, not required product dependencies.
- [x] 1.8 Confirm phase-1 CLI uses one generic `aworld-cli optimize` command
  whose `--target` option supports skill, prompt-section, and tool-description
  target forms.
- [x] 1.9 Confirm phase 1 includes at least one controlled automatic evolve
  mode: `online` can apply verified candidates for allowlisted targets after
  post-apply re-evaluation.
- [x] 1.10 Confirm workspace-local code/file targets are limited to artifacts
  produced by agent task execution and must not touch AWorld or `aworld-cli`
  product logic.
- [x] 1.11 Confirm existing `Runners.evolve(...)` / `train.evolve` remains the
  training-oriented evolution pipeline, while `aworld.self_evolve` owns
  controlled harness optimization.
- [x] 1.12 Confirm trajectory-driven credit assignment is a phase-0 hard gate
  and a phase-1 core loop, not a placeholder.
- [x] 1.13 Confirm opt-in uses `SelfEvolveConfig.mode`; no separate
  `AgentConfig.optimize` flag is added.
- [x] 1.14 Confirm `aworld-skills/app_evaluator/SKILL.md` remains independent
  and protected; self-evolve is a new complete framework subsystem, not an
  app_evaluator upgrade.
- [x] 1.15 Confirm `shadow` is proposal-only and `online` closes a narrow
  `apply -> re-evaluate -> accept/rollback` loop for allowlisted targets.
- [x] 1.15A Confirm `online` + `auto_verified` is unattended after enablement:
  no human review, approval, confirmation, or intervention is required.
- [x] 1.16 Confirm LLM judge behavior is configurable: default self-evolve
  trajectory judge, explicit `agent.md` judge, or custom judge agent.
- [x] 1.17 Confirm `aworld-cli optimize` is the only phase-1 CLI entrypoint and
  CLI must not own scheduler, evaluator, optimizer, target inference, durable
  artifacts, or agent opt-in semantics.
- [x] 1.18 Confirm phase-1 "self-evolve" means harness-text/config evolution, not
  model-weight training or replacement of the agent policy.
- [x] 1.19 Confirm a single post-run trajectory usually produces a
  limited-confidence proposal, not an automatic verified apply.

## 1A. Phase-1a Minimal Vertical Slice

- [ ] 1A.1 Ship the first implementation as config + `SkillTextTarget` + trace
  packaging + low-dependency LLM mutator + one deterministic/objective
  evaluation signal + proposal-only artifacts + explicit SDK/CLI target path.
- [ ] 1A.2 Defer async scheduling, broad provenance expansion, DSPy adapters,
  non-skill targets, and online automatic apply until the phase-1a slice proves
  target selection and proposal value.

## 2. Framework Configuration

- [x] 2.1 Add `SelfEvolveConfig` to framework config models.
- [x] 2.2 Add `AgentConfig.self_evolve_config` with `mode="off"` disabled
  defaults and no separate `enabled` or `optimize` flag.
- [x] 2.3 Add run budget config fields for max tokens, optional max cost, min
  eval cases, judge repetitions, and cooldown.
- [x] 2.4 Add tests proving existing agent config construction remains backward
  compatible.
- [x] 2.5 Add tests proving unknown extra model config kwargs still flow through
  existing `llm_config.ext_config` behavior.
- [x] 2.6 Add tests for mode semantics: `off`, `offline`, `shadow`, and
  `online`.
- [x] 2.7 Add tests proving `online` requires explicit verified apply policy and
  does not apply candidates without passing all gates.
- [x] 2.8 Add judge config tests for default trajectory judge, `agent.md`,
  custom agent, and disabled judge modes.

## 3. Self-Evolve Core Package

- [x] 3.1 Create `aworld/self_evolve/` with stable public imports.
- [x] 3.2 Define target interfaces and phase-1 target types.
- [x] 3.3 Define candidate variant, run, metric, diagnostic, and gate result
  models.
- [x] 3.4 Define optimizer and evaluation backend protocols.
- [x] 3.5 Define persistent run artifact storage under `.aworld/self_evolve/`.
- [x] 3.6 Define async trigger/run-context models for post-run enqueue.
- [x] 3.7 Define `SelfEvolveRun` naming that does not conflict with existing
  `train.evolve.EvolutionRunner` / `EvolutionConfig`.
- [x] 3.8 Define trace pack, dataset recipe, target provenance, and optimizer
  lineage models.

## 4. Evaluation Integration

- [x] 4.1 Add a default evaluation backend that can call existing
  `EvaluateRunner`.
- [x] 4.2 Add support for objective command verification as an evaluation signal.
- [ ] 4.3 Add support for trajectory quality scoring as an evaluation signal.
- [x] 4.4 Add support for cost and latency metrics.
- [x] 4.5 Add configurable judge support for the default self-evolve trajectory
  judge, explicit `agent.md` judges, and custom judge agents.
- [x] 4.6 Add regression tests proving baseline and candidate variants are
  evaluated through the same dataset and scorer policy.
- [x] 4.7 Add tests proving optional trajectory-log sources are accepted when
  configured but not required by default.
- [ ] 4.7A Add a dataset builder test proving task records extracted from
  `~/Documents/logs/trajectory.log` can seed the initial regression benchmark
  when explicitly requested.
- [x] 4.8 Add held-out evaluation discipline: select candidates on validation
  metrics and gate verification on optimizer-held-out test metrics when enough
  cases are available.
- [x] 4.9 Add tests for insufficient eval cases producing limited-confidence
  proposals rather than verified candidates.
- [x] 4.10 Add tests proving judge-only improvements remain limited-confidence
  and verified candidates require a deterministic/objective signal.
- [x] 4.11 Add replay-cost preflight estimation for baseline and candidate
  re-execution before launching evaluation batches.

## 5. Dataset Builders

- [x] 5.1 Add jsonl dataset ingestion for explicit eval cases.
- [x] 5.2 Add builder support for existing batch job config as an eval source.
- [x] 5.3 Add current-trajectory, session, and trajectory-log source interfaces
  that can feed phase-1 credit assignment.
- [x] 5.4 Add deterministic train/validation/test split metadata.
- [x] 5.5 Add tests for dataset identity and split reproducibility.
- [x] 5.6 Add a fixture-backed test that can use a trajectory log sample, without
  hard-coding `~/Documents/logs/trajectory.log` into product behavior.
- [x] 5.7 Add dataset recipe persistence with source filters, synthetic
  generation policy, and trainable vs held-out failure-case separation.
- [x] 5.8 Add tests proving single-trajectory post-run sources cannot produce a
  verified candidate unless additional eval sources satisfy held-out gates.

## 6. Trajectory Credit Assignment

- [x] 6.0 Verify the phase-0 credit-assignment gate has been accepted before
  implementing production target inference.
- [x] 6.1 Add `TrajectoryCreditAssigner` and `TargetSelectionReport` models.
- [x] 6.2 Add SAR-first `TracePack` normalization/compression before credit
  assignment, preserving `TrajectoryItem.state`, `action`, `reward`, and stable
  evidence ids.
- [x] 6.3 Build target inventory for skill, prompt-section, tool-description,
  whitelisted config, and agent-produced workspace artifact targets.
- [x] 6.4 Implement deterministic trajectory signal extraction using
  trajectory scorers, tool call failures, repeated actions, LLM calls, and
  generated artifact references.
- [x] 6.5 Add optional LLM-assisted diagnosis that cites trajectory evidence and
  can return `no_target` on low confidence.
- [x] 6.6 Add tests proving `--task` / current trajectory can infer skill,
  prompt, or tool-description targets, and can decline when evidence is
  insufficient.
- [x] 6.7 Add target provenance and trust metadata to target inventory.

## 7. Candidate Generation

- [x] 7.1 Add a low-dependency trace-reflective LLM mutator optimizer for text
  targets.
- [ ] 7.2 Add optional DSPy GEPA and MIPRO optimizer adapters behind dependency
  checks.
- [x] 7.3 Add candidate fingerprinting and target version fingerprinting.
- [ ] 7.4 Add constraints for skill markdown/frontmatter, prompt section format,
  tool schema description, token limits, and no-op candidate filtering.
- [x] 7.5 Add tests proving optimizer absence produces a clear configuration
  error, not an import-time framework failure.
- [ ] 7.6 Add workspace-local artifact candidate support for agent-produced
  code/files behind protected-path gates and isolated candidate workspace
  evaluation.
- [x] 7.7 Ensure optimizers cannot inspect held-out test cases or held-out judge
  outputs.
- [x] 7.8 Add optimizer lineage tracking with parent candidate ids, mutation
  rationale, and trainable failure cases used.
- [ ] 7.9 Keep Darwinian/code evolution as a future external CLI adapter only;
  do not import AGPL code into AWorld core.

## 8. Gates And Apply Policy

- [x] 8.1 Add gate policy for minimum score improvement.
- [x] 8.2 Add gate policy for maximum cost/latency regression.
- [x] 8.3 Add gate policy for required deterministic verification commands.
- [x] 8.4 Add proposal-only apply mode as the default.
- [x] 8.5 Add `auto_verified` apply mode for `online` and allowlisted targets.
- [x] 8.5A Ensure `auto_verified` is gate-driven and unattended after enablement.
- [x] 8.6 Ensure proposal mode writes report, candidate files, and diffs only.
- [x] 8.7 Add protected-path gates for framework, `aworld-cli`, runtime, shared
  infrastructure, package metadata, secret/config paths, and AWorld product
  logic.
- [x] 8.7A Add protected-path gates for `aworld-skills/app_evaluator/SKILL.md`
  so it cannot be selected as a default target or mutated by candidates.
- [x] 8.8 Add stopping conditions for max iterations, stalled improvement,
  pending proposal duplicate suppression, and cooldown.
- [x] 8.9 Add whole-run token and cost budget gates.
- [x] 8.10 Add held-out verification gates for candidates that claim verified
  improvement.
- [x] 8.11 Add trust/provenance gates for generated, external, and protected
  targets.
- [x] 8.12 Add global regression benchmark gates for skill, prompt-section, and
  tool-description targets.
- [x] 8.13 Add judge-only limited-confidence gates.
- [x] 8.14 Add post-apply re-evaluation and rollback/rejection gates for
  `auto_verified`.

## 9. Framework Runner

- [ ] 9.1 Add `SelfEvolveRunner` that orchestrates target loading, dataset
  selection/credit assignment, dataset building, baseline eval, candidate
  generation, candidate eval, gates, and artifact persistence.
- [x] 9.2 Add a Python API entry point for SDK use.
- [x] 9.3 Ensure active runtime behavior is not mutated during proposal-only
  runs or during the task that triggered an online evolve job.
- [x] 9.4 Add targeted tests for a local fake target and fake optimizer.
- [ ] 9.5 Add tests proving async post-run enqueue failures do not fail or delay
  the completed task response.

## 9A. Async Post-Run Scheduling

- [x] 9A.0 Start this section only after the phase-1a explicit-target
  proposal-only slice proves useful.
- [x] 9A.1 Add `SelfEvolveScheduler` with best-effort enqueue semantics.
- [x] 9A.2 Add post-run eligibility checks for
  `self_evolve_config.mode in {"shadow", "online"}`.
- [x] 9A.2A Hook enqueue from `TaskEventRunner.do_run(...)` after
  `_save_trajectories()` and `_response()` have made trajectory and `llm_calls`
  available; keep `Runners.run(...)` as a delegating wrapper.
- [ ] 9A.3 Add concurrency, timeout, retry, pending-proposal, and cooldown
  controls.
- [ ] 9A.4 Add tests proving scheduler/worker failures do not affect the main
  task result.
- [ ] 9A.5 Persist durable pending jobs before enqueue returns, and test
  short-lived CLI process behavior does not rely on fire-and-forget tasks.

## 10. CLI Integration

- [x] 10.1 Add a single top-level `aworld-cli optimize` command.
- [x] 10.2 Support `--agent`, `--task`, `--target`, `--dataset`,
  `--from-session`, `--from-trajectory`, `--batch-config`, `--iterations`, and
  `--apply`.
- [x] 10.3 Do not add interactive `/optimize` or other extra phase-1 CLI
  entrypoints.
- [x] 10.4 Do not add CLI-owned env/config wiring for built-in AWorld main agent
  self-evolve mode; use framework agent config semantics only.
- [x] 10.5 Add command tests proving CLI invokes framework APIs rather than
  owning self-evolve logic.
- [x] 10.6 Add CLI tests proving `--from-trajectory` is optional and passed
  through as an explicit eval source.
- [x] 10.7 Add CLI tests proving `--task` can invoke framework target inference
  through the same command path.
- [x] 10.8 Add CLI tests proving command discovery uses the built-in plugin
  manifest/`cli_commands` path.
- [x] 10.9 Add CLI tests proving CLI does not own scheduler, evaluator,
  optimizer, target inference, durable artifacts, or agent opt-in semantics.

## 11. Documentation And Examples

- [x] 11.1 Document framework self-evolve concepts and safety model.
- [x] 11.1A Document that phase-1 self-evolve means harness-text/config
  evolution, not model-weight training or replacement of the agent policy.
- [x] 11.2 Document `SelfEvolveConfig.mode` opt-in and its distinction from
  `meta_learning_config`, `ContextRuleConfig.optimization_config`, and
  `train.evolve`.
- [x] 11.3 Document CLI `optimize` usage.
- [x] 11.4 Add a minimal example that uses a toy trajectory to infer and
  propose a target improvement, with optional jsonl eval cases.
- [x] 11.5 Add a note explaining why framework/runtime/CLI logic evolution is
  deferred while agent-produced workspace artifacts remain isolated and gated.
- [x] 11.6 Document that phase 1 has two modes: proposal-only `shadow` and
  controlled automatic `online` for allowlisted verified targets.
- [x] 11.6A Document that the default single-trajectory post-run path produces a
  limited-confidence proposal unless independent eval sources and
  deterministic/objective gates are configured.
- [x] 11.7 Document that `aworld-skills/app_evaluator/SKILL.md` is not part of
  the new self-evolve subsystem and may only be used as an explicitly
  configured read-only scorer/fixture.
- [x] 11.8 Document which Hermes/GEPA/Darwinian patterns were adopted as
  framework contracts and which remain future adapters.
- [x] 11.9 Document configurable LLM judge behavior and examples for default
  trajectory judge, `agent.md`, and custom judge agents.
- [x] 11.10 Document the online closed-loop path:
  `apply -> re-evaluate -> accept/rollback`, and note which broader targets are
  deferred to later phases.
- [x] 11.11 Document that online automatic mode is unattended after enablement
  and does not wait for human review or approval.
