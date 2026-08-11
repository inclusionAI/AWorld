## Why

AWorld self-evolve already has a strong verification and release pipeline. It
can freeze trajectory datasets, generate bounded candidate populations, run
paired replay, evaluate held-out and regression evidence, preserve lineage,
account for token/cost/wall-time budgets, and reject or roll back unsafe
releases.

The remaining gap is trustworthy measurement of *where an observed improvement
came from*. A higher selected-candidate score can combine several effects:

- the task model changed
- the harness artifact changed
- a generator produced a better candidate distribution
- a scheduler received more search opportunities
- best-of-N selection found a lucky outlier
- the evaluator, environment, seed, or runtime changed
- the result is ordinary statistical noise

The current release-facing `report.json` records what happened in one run and
whether its gates passed. It does not define a controlled experiment that
freezes non-treatment variables, estimates an effect distribution, separates
search gain from candidate gain, or reports quality as a function of token,
cost, and time budget.

The concrete Campaign
`campaign-f0b510b7bb07ae5de6f0` demonstrates the distinction. Its explicit
target selection resolved `skill:agent-browser` with confidence `1.0`, but
target inference was bypassed; that confidence established target identity and
provenance, not causal responsibility. Across two cycles the Campaign consumed
465,993 tokens and 2,820.82 wall seconds, generated eleven unique candidates,
and produced zero authoritative candidates and zero comparable paired outcomes.
The second cycle improved replay capability enough to advance from capability
compilation into task rollout, but both baseline and candidate timed out. The
framework correctly rejected the run, yet the final records cannot express the
full conclusion:

- target resolution was conclusive
- measurement-harness readiness improved
- the baseline/candidate experiment remained invalid or inconclusive
- task-plane Skill improvement was not measured
- additional candidate mutation had zero observed measurement yield

This change introduces a framework-owned trusted improvement measurement plane
that makes those distinctions explicit and lets Campaign decisions consume
them through deterministic typed policies.

## What Changes

- Add a versioned controlled-experiment contract that declares one swap axis,
  freezes all non-treatment identities, predeclares primary outcomes, and
  separates search and measurement budgets.
- Support controlled swap axes for:
  - baseline harness artifact versus evolved harness artifact
  - task model A versus task model B under a fixed harness
  - generator/operator A versus generator/operator B under a fixed framework,
    scheduler, dataset, evaluator, and search budget
  - scheduler A versus scheduler B under a fixed generator, dataset, evaluator,
    and search budget
- Separate target-resolution confidence, experiment validity, and improvement
  effect confidence. No one field may stand in for all three.
- Add baseline/control viability and experiment-comparability checks before an
  effect is estimated or candidate failure is used to authorize another repair.
- Record independent task cases separately from repeated executions of the
  same case. Repetitions may estimate stability, but they do not increase the
  independent held-out sample count.
- Add paired effect estimates, uncertainty intervals, pass@K/best@K curves,
  measurement-yield metrics, and quality/cost/latency frontiers.
- Add cross-task, cross-Skill-family, and temporal held-out transfer panels.
- Persist raw bounded observations and a detailed attribution report under a
  run-owned `experiments/` directory while keeping `report.json` as a bounded
  release-facing summary.
- Add shadow, advisory, and required measurement-policy modes. Shadow mode is
  the initial default and cannot alter existing release behavior.
- Add separate measurement-readiness and candidate-effect progress to Campaign
  state. Invalid experiments route to measurement repair or operator action;
  they do not automatically authorize more candidate mutation.
- Allow Campaign scheduling to use typed measurement outcomes for champion
  selection, evidence collection, early stopping, and budget allocation only
  after shadow calibration is accepted.
- Keep the implementation native to AWorld and reuse AWorld replay, dataset,
  evaluation, budget, provenance, report, and Campaign contracts.

## Capabilities

### New Capabilities

- `self-evolve-controlled-experiments`: AWorld can execute versioned controlled
  swaps with explicit treatment, control, frozen variables, and budget
  contracts.
- `self-evolve-effect-attribution`: AWorld can estimate whether an observed gain
  is attributable to a harness artifact, task model, generator, scheduler, or
  remains unmeasured/inconclusive.
- `self-evolve-measurement-validity`: AWorld can distinguish valid negative
  evidence from invalid controls, infrastructure failures, drift, leakage, and
  insufficient independent evidence.
- `self-evolve-budget-curves`: AWorld can report quality, regression, cost, and
  latency across search budgets rather than only the final best candidate.
- `self-evolve-transfer-measurement`: AWorld can measure cross-task,
  cross-Skill-family, and temporal held-out transfer without exposing those
  panels to candidate generation.
- `self-evolve-measurement-artifacts`: AWorld can persist controlled experiment
  specifications, observations, attribution reports, and release-facing
  measurement summaries.

### Modified Capabilities

- `self-evolve-framework`: verification gains an optional measurement plane
  before trusted effect claims and automatic promotion.
- `self-evolve-candidate-population`: population reports gain best@K/pass@K and
  equal-budget search-efficiency measurements.
- `self-evolve-campaign`: Campaign progress distinguishes measurement readiness
  from candidate quality and may consume conclusive effect summaries through a
  deterministic policy.
- `self-evolve-run-artifacts`: `report.json` links to detailed experiment
  artifacts and contains a bounded measurement summary.
- `aworld-cli-self-evolve`: CLI may configure measurement mode and inspect
  measurement artifacts, but it remains a thin wrapper over framework APIs.

## Impact

- Expected framework areas:
  - `aworld/self_evolve/measurement.py` or an equivalent focused module
  - `aworld/self_evolve/runner.py`
  - `aworld/self_evolve/replay.py`
  - `aworld/self_evolve/evaluation.py`
  - `aworld/self_evolve/population.py`
  - `aworld/self_evolve/campaign.py`
  - `aworld/self_evolve/budget.py`
  - `aworld/self_evolve/store.py`
  - `aworld/self_evolve/config.py`
- Expected CLI/docs areas:
  - `aworld-cli optimize`
  - `docs/Agents/Self Evolve.md`
  - `docs/AWorld CLI/Commands/Optimize.md`
  - `.aworld/self_evolve/<run_id>/experiments/`
- Existing replay, evaluator, gate, apply, rollback, provenance, and artifact
  retention contracts remain authoritative and are extended rather than
  replaced.

## Safety And Scientific-Validity Constraints

- Experiment validity SHALL be established before an effect estimate can be
  conclusive or promotion-eligible.
- A controlled experiment SHALL change exactly one declared swap axis unless a
  separately declared factorial experiment is used.
- Task model, generator, scheduler, evaluator, prompt/context assembly,
  dataset, environment, runtime, and budget identities SHALL be fingerprinted
  or marked unavailable. Missing required identity telemetry SHALL fail closed
  in required mode.
- Search budget and measurement budget SHALL be accounted separately.
- Candidate count, retries, and best-of-N opportunities SHALL be equalized or
  explicitly normalized before generator or scheduler gains are claimed.
- Repetitions of one task SHALL NOT be counted as independent task evidence.
- Primary outcomes and decision thresholds SHALL be declared before treatment
  observations are evaluated.
- Held-out and temporal transfer cases SHALL remain hidden from candidate
  generators and adaptive schedulers until their designated evaluation stage.
- Multiple-candidate selection SHALL report the selection protocol and correct
  or conservatively account for repeated comparisons.
- Invalid or incomparable controls SHALL produce `effect = null`; they SHALL
  NOT be encoded as zero or negative candidate improvement.
- Measurement failures SHALL retain typed ownership and SHALL NOT be converted
  automatically into Skill instructions or candidate-owned lessons.
- Raw observation artifacts SHALL use the existing public diagnostic
  projection, evidence minimization, path safety, and secret-redaction
  boundaries.
- Shadow mode SHALL be the default until calibration demonstrates that the new
  decision rules do not regress current release safety.

## Independent-Implementation Boundary

The controlled-swap and improvement-attribution direction is informed by
general ideas found in external self-improving-agent research and projects,
including OpenRSI. OpenRSI is a conceptual reference only.

- AWorld SHALL NOT import, vendor, wrap, or depend on OpenRSI source code.
- AWorld SHALL NOT copy OpenRSI implementation files, internal APIs, prompts,
  tests, fixtures, data models, or repository structure.
- No OpenRSI runtime or package SHALL be required for build, test, execution, or
  evaluation.
- Contracts SHALL be derived from AWorld's existing replay, evaluation,
  Campaign, budget, provenance, and report semantics.
- Implementations SHALL use AWorld naming, types, artifact layout, test
  fixtures, and coding conventions.
- External attribution in documentation may identify conceptual inspiration,
  but external code is not normative for behavior or compatibility.

## Non-Goals

- Do not train or fine-tune task-model weights as part of this change.
- Do not implement a general causal-inference framework.
- Do not claim causality when an experiment is observational, underpowered,
  drifted, or missing comparable controls.
- Do not make every proposal run pay the full cost of repeated controlled
  measurement.
- Do not replace current replay, evaluator, regression, challenger, apply, or
  rollback subsystems.
- Do not allow an LLM to decide promotion by interpreting raw experiment JSON;
  Campaign decisions must consume typed deterministic summaries.
- Do not merge raw per-seed/per-case observations into the release-facing
  `report.json`.
- Do not reinterpret target-selection confidence as effect confidence.
- Do not use the concrete `agent-browser` Campaign as a product dependency or
  permanent fixture containing private local paths. A minimized committed test
  fixture may reproduce its structural conditions with synthetic identities.
- Do not implement factorial interaction studies in the first delivery. The
  schema may reserve the concept, but initial execution supports one swap axis
  at a time.
