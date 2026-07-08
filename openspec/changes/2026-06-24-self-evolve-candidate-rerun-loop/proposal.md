## Why

AWorld self-evolve can now generate skill candidates, persist proposals, run
framework gates, and evaluate baseline/candidate trajectory artifacts through
the existing AWorld evaluator runtime. That closes the evaluator-agent side of
the loop, but it does not yet prove that a generated candidate skill improves
runtime behavior.

The remaining gap is the candidate execution loop: after generating a skill
candidate, the framework must run the same task with that candidate skill
mounted as an isolated overlay, capture a new candidate trajectory, compare it
against the baseline trajectory through evaluator agents and gates, and only
then apply the candidate to the real skill when policy permits.

Without this change, `auto_verified` can verify candidate text and evaluator
metrics, but it cannot fully prove "the evolved skill makes the same task run
better" because the candidate trajectory is not automatically produced by a
real rerun. Users can still provide candidate trajectories manually, but the
framework does not own the end-to-end closed loop.

## What Changes

- Add a framework-owned task rerun harness for self-evolve candidate
  evaluation.
- Add a skill overlay mechanism that mounts a candidate `SKILL.md` without
  overwriting the installed real skill.
- Define the overlay injection seam as a materialized shadow skill root so
  both legacy `skills_path` loaders and runtime skill registries can observe
  the candidate through their normal loading paths.
- Re-run the same task with the candidate skill overlay to produce a candidate
  trajectory.
- Pair the original baseline trajectory with the candidate trajectory in the
  self-evolve evaluation dataset.
- Control replay variance by rerunning baseline when possible or by requiring
  candidate replay repetitions and stronger aggregate margins when comparing
  against a historical baseline.
- Reuse existing AWorld evaluator runtime and evaluator-agent selectors to
  score baseline/candidate trajectories.
- Require score improvement, evaluator gates, regression checks, protected-path
  gates, budget checks, and post-apply verification before `auto_verified`
  writes the candidate back to the real skill.
- Require post-apply verification to load the applied skill through the real
  runtime skill loader path and refresh any long-lived skill registry/cache.
- Persist an original-skill backup and apply journal before verified writes so
  asynchronous worker crashes can be recovered.
- Extend the asynchronous worker path so post-run self-evolve jobs can execute
  the full loop without blocking the original user task.
- Provide a real drain entrypoint for post-run jobs rather than only a helper
  method that callers may invoke manually.
- Keep task-specific environment setup outside target inference. If a replay
  needs a browser, credentials, CDP, tools, or external services, that remains
  part of the task runtime environment and must be declared or detected as a
  replay prerequisite, not inferred as the mutation target.

## Capabilities

### New Capabilities

- `self-evolve-candidate-skill-overlay`: AWorld can mount a skill candidate as
  an isolated runtime overlay for replay without mutating the real skill.
- `self-evolve-task-rerun`: AWorld can rerun a task from baseline trajectory
  context under a candidate overlay and capture a candidate trajectory.
- `self-evolve-paired-trajectory-evaluation`: AWorld can construct paired
  baseline/candidate trajectory datasets for evaluator-agent comparison.
- `self-evolve-verified-apply-loop`: AWorld can automatically apply a
  candidate only after candidate rerun, evaluator comparison, gates, and
  post-apply verification pass.

### Modified Capabilities

- `self-evolve-framework`: runner orchestration expands from candidate
  generation and evaluator scoring to full candidate execution and trajectory
  comparison.
- `aworld-cli-self-evolve`: CLI validation and manual invocation can request
  the rerun loop, but CLI remains a thin surface and MUST NOT own replay,
  overlay, evaluator, gate, or apply logic.
- `built-in-self-evolve-skill`: guidance may describe the closed loop only
  after the framework implementation and tests exist.

## Impact

- Affected framework areas:
  - `aworld.self_evolve.runner`
  - `aworld.self_evolve.evaluation`
  - `aworld.self_evolve.scheduler`
  - `aworld.self_evolve.datasets`
  - `aworld.self_evolve.targets`
  - skill discovery / runtime skill loading surfaces used by `aworld-cli`
  - `.aworld/self_evolve/` artifact layout
- Affected CLI/testing areas:
  - `aworld-cli optimize` thin parameters and reports, if needed
  - `scripts/self_evolve_cli_trajectory_case.py`
  - self-evolve and evaluator runtime tests
- Safety constraints:
  - candidate overlays MUST be isolated from the real skill until apply gates
    pass
  - proposal and shadow modes MUST NOT mutate installed skills
  - target inference MUST NOT special-case task environments such as CDP,
    browser profiles, or a specific skill name
  - replay failures MUST reject or downgrade the candidate rather than applying
    it
  - verified apply MUST account for replay variance and cost before accepting
    candidate improvements
  - `auto_verified` MUST remain allowlist-gated and post-apply verified
  - post-apply verification MUST use the production runtime skill loader path,
    not only a file-content comparison
  - verified writes MUST have a persisted backup and apply journal
  - evaluator-agent-only positive signals MUST remain insufficient for verified
    apply unless required deterministic and held-out criteria are satisfied

## Non-Goals

- Do not implement general source-code self-modification.
- Do not make `aworld-cli` own replay orchestration or evaluator semantics.
- Do not make task-specific environment repair a self-evolve target.
- Do not require `~/Documents/agent.md` or any single evaluator agent.
- Do not guarantee all task categories are replayable in phase 1; unsupported
  replay environments should produce explicit diagnostics.
