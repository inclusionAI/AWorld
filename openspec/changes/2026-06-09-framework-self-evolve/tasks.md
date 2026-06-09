## 1. Scope Freeze

- [x] 1.1 Confirm self-evolve is a framework-owned capability, with CLI as a
  caller and UX layer.
- [x] 1.2 Confirm self-evolve is disabled by default for all agents.
- [x] 1.3 Confirm phase 1 excludes arbitrary source-code evolution.
- [x] 1.4 Confirm the default apply mode is proposal-only.
- [x] 1.5 Confirm evaluation is a pluggable contract, not a hard dependency on
  the built-in evaluator agent.

## 2. Framework Configuration

- [ ] 2.1 Add `SelfEvolveConfig` to framework config models.
- [ ] 2.2 Add `AgentConfig.optimize: bool = False`.
- [ ] 2.3 Add `AgentConfig.self_evolve_config` with disabled defaults.
- [ ] 2.4 Add tests proving existing agent config construction remains backward
  compatible.
- [ ] 2.5 Add tests proving unknown extra model config kwargs still flow through
  existing `llm_config.ext_config` behavior.

## 3. Self-Evolve Core Package

- [ ] 3.1 Create `aworld/self_evolve/` with stable public imports.
- [ ] 3.2 Define target interfaces and phase-1 target types.
- [ ] 3.3 Define candidate variant, run, metric, diagnostic, and gate result
  models.
- [ ] 3.4 Define optimizer and evaluation backend protocols.
- [ ] 3.5 Define persistent run artifact storage under `.aworld/evolution/`.

## 4. Evaluation Integration

- [ ] 4.1 Add a default evaluation backend that can call existing
  `EvaluateRunner`.
- [ ] 4.2 Add support for objective command verification as an evaluation signal.
- [ ] 4.3 Add support for trajectory quality scoring as an evaluation signal.
- [ ] 4.4 Add support for cost and latency metrics.
- [ ] 4.5 Add regression tests proving baseline and candidate variants are
  evaluated through the same dataset and scorer policy.

## 5. Dataset Builders

- [ ] 5.1 Add jsonl dataset ingestion for explicit eval cases.
- [ ] 5.2 Add builder support for existing batch job config as an eval source.
- [ ] 5.3 Add session/trajectory mining interfaces, initially read-only.
- [ ] 5.4 Add deterministic train/validation/test split metadata.
- [ ] 5.5 Add tests for dataset identity and split reproducibility.

## 6. Candidate Generation

- [ ] 6.1 Add a low-dependency LLM mutator optimizer for text targets.
- [ ] 6.2 Add an optional DSPy/GEPA optimizer adapter behind dependency checks.
- [ ] 6.3 Add candidate fingerprinting and target version fingerprinting.
- [ ] 6.4 Add constraints for skill markdown/frontmatter, prompt section format,
  tool schema description, token limits, and no-op candidate filtering.
- [ ] 6.5 Add tests proving optimizer absence produces a clear configuration
  error, not an import-time framework failure.

## 7. Gates And Apply Policy

- [ ] 7.1 Add gate policy for minimum score improvement.
- [ ] 7.2 Add gate policy for maximum cost/latency regression.
- [ ] 7.3 Add gate policy for required deterministic verification commands.
- [ ] 7.4 Add proposal-only apply mode.
- [ ] 7.5 Add write apply mode behind explicit confirmation/config.
- [ ] 7.6 Add branch apply mode if git integration is approved for phase 1.

## 8. Framework Runner

- [ ] 8.1 Add `SelfEvolveRunner` that orchestrates target loading, dataset
  building, baseline eval, candidate generation, candidate eval, gates, and
  artifact persistence.
- [ ] 8.2 Add a Python API entry point for SDK use.
- [ ] 8.3 Ensure active runtime behavior is not mutated during proposal-only
  runs.
- [ ] 8.4 Add targeted tests for a local fake target and fake optimizer.

## 9. CLI Integration

- [ ] 9.1 Add top-level `aworld-cli optimize` command.
- [ ] 9.2 Support `--agent`, `--task`, `--target`, `--dataset`,
  `--from-session`, `--from-trajectory`, `--batch-config`, `--iterations`, and
  `--apply`.
- [ ] 9.3 Add optional interactive `/optimize` command if the interactive
  command surface is in scope for phase 1.
- [ ] 9.4 Add env/config wiring so the built-in AWorld main agent can opt into
  self-evolve eligibility.
- [ ] 9.5 Add command tests proving CLI invokes framework APIs rather than
  owning self-evolve logic.

## 10. Documentation And Examples

- [ ] 10.1 Document framework self-evolve concepts and safety model.
- [ ] 10.2 Document `AgentConfig.optimize` and `SelfEvolveConfig`.
- [ ] 10.3 Document CLI `optimize` usage.
- [ ] 10.4 Add a minimal example optimizing a toy skill against a jsonl dataset.
- [ ] 10.5 Add a note explaining why code evolution is deferred.
