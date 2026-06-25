## Overview

This change closes the self-evolve runtime loop for skill targets:

1. capture or load a baseline trajectory
2. infer or receive a mutable `skill:<name>` target
3. generate a candidate `SKILL.md`
4. mount the candidate as an isolated runtime skill overlay
5. rerun the same task with the candidate overlay
6. capture the candidate trajectory
7. evaluate baseline and candidate trajectories through AWorld evaluator
   runtime
8. run verification and safety gates
9. apply the candidate to the real skill only when `auto_verified` policy and
   gates allow it

The framework owns every step after invocation. CLI, skills, and scripts may
request the loop, but they do not implement replay, scoring, gates, or writes.

## Architecture

### Candidate Skill Overlay

Add a self-evolve overlay abstraction that can expose a candidate skill file to
the runtime loader before or instead of the installed real skill.

The overlay should:

- live under `.aworld/self_evolve/<run_id>/overlays/<candidate_id>/`
- preserve the normal skill directory shape, including `SKILL.md`
- record the original target path and candidate provenance
- be disposable after evaluation
- never write into `aworld-skills/<name>/SKILL.md` or user skill directories
  until apply gates pass

Runtime loading should prefer the overlay skill for the selected target only.
Other skills should resolve normally. This keeps the candidate evaluation
focused and avoids accidental broad environment changes.

### Overlay Loader Injection

Candidate replay MUST use a concrete loader injection mechanism, not an
implicit environment side effect. The required mechanism is a materialized
shadow skill root:

1. Resolve the effective baseline skill inventory from the same sources the
   runtime would normally use.
2. Create
   `.aworld/self_evolve/<run_id>/overlays/<candidate_id>/skills/` as a
   temporary skill root.
3. Write the candidate target skill into
   `<shadow-root>/<skill-name>/SKILL.md`.
4. For every other visible skill in the baseline inventory, add a symlink or
   copy into the shadow root that preserves the normal skill directory shape.
5. Exclude the original selected target skill from the shadow root so the
   candidate is the only definition for that skill name.
6. Pass the shadow root as the replay `skills_path` for legacy loaders such as
   `_load_skill_agent`, and as the first framework-controlled skill source for
   runtime bootstrap paths that accept multiple skill sources.

This choice makes the selected candidate win without requiring a special-case
registry override. It also keeps unrelated skills available during replay, so a
task that depends on multiple skills can run with only the selected target
shadowed.

Each replay MUST instantiate a fresh skill registry from the shadow root. It
MUST NOT reuse a registry object or content cache from the baseline run.

### Task Replay Harness

Add a replay request model that is derived from baseline trajectory evidence
and explicit invocation context.

The request should include:

- task id
- original task input
- agent name or agent id, if known
- workspace root
- source trajectory identity
- selected target
- candidate id
- overlay path
- optional environment prerequisites
- timeout, token, cost, and step budgets

The replay harness should execute the same task through the existing AWorld CLI
or runtime executor with the overlay skill path injected through a framework
controlled mechanism. When it uses `aworld-cli run`, it MUST run from the
original workspace root and inherit the normal process environment so the
existing `aworld-cli` configuration chain (`.env`, global config, provider
environment variables) is reused. Replay MUST NOT introduce a separate model
credential or provider configuration path; only runtime prerequisites such as
browser/CDP availability may be reported as replay environment context.

The CLI replay path MUST request machine-readable trajectory output from
`aworld-cli run` instead of scraping human console rendering. It should produce:

- candidate trajectory
- stdout/stderr or structured runtime logs
- status
- latency, cost, token, and step metrics where available
- failure diagnostics

If the task cannot be replayed because required environment state is missing,
the harness must return a structured replay failure. That failure is an
evaluation signal and must prevent verified apply.

### Replay Signal Strategy

Verified apply cannot rely on one old baseline trajectory compared with one
fresh candidate rerun. Agent behavior, model sampling, tool state, network
latency, and external environment state can all introduce variance.

For `auto_verified`, the replay policy MUST use one of these strategies:

- rerun both baseline and candidate under the same replay environment and
  compare aggregated metrics
- or run multiple candidate replays against an accepted fixed baseline and
  require the aggregate candidate score to clear a stronger improvement margin

The default verified strategy SHOULD rerun both baseline and candidate once
when the baseline skill is available, then allow additional repetitions through
configuration. A candidate MUST NOT be verified if replay variance diagnostics
show that the measured improvement is below the configured stability margin.

Proposal mode MAY compare the original baseline trajectory to a single
candidate rerun, but the report MUST label that result as limited confidence.

To control cost, the runner MUST cap the number of candidates that receive
full replay. The default should replay only the selected best candidate unless
the caller explicitly raises a configured replay candidate limit.

### Paired Trajectory Dataset

After rerun, the framework should construct a paired evaluation case with:

- baseline trajectory from the original run or explicit baseline source
- candidate trajectory from the rerun
- original task input
- target and candidate metadata
- replay diagnostics

The existing `AWorldTrajectoryEvaluatorBackend` can consume this by receiving
variant trajectories in dataset metadata. This change should make that
population automatic rather than requiring a test script or caller to fill it
manually.

### Evaluation And Gates

The evaluator path should reuse existing selector forms:

- default trajectory evaluator, when available
- `agent.md`
- named evaluator agent
- `module:factory` backend reference

Candidate apply eligibility should require:

- candidate trajectory was produced successfully
- evaluator score improves over baseline by configured minimum delta
- evaluator trajectory gate passes
- required deterministic signal is present when verified apply is requested
- held-out criteria pass when configured
- protected path, trust provenance, malformed/noop, token, and budget gates pass
- cost/latency regressions remain within policy
- post-apply re-evaluation confirms the real skill contains and loads the
  applied candidate

Evaluator-agent-only positive judgment should be persisted but must not be the
sole basis for verified apply.

### Post-Apply Runtime Load Verification

Post-apply verification MUST prove more than file equality. After a candidate
is written to the real skill path, verification MUST exercise the same runtime
skill loading path used by subsequent tasks.

At minimum, the verifier MUST:

- create a fresh production skill registry from the real runtime skill sources
- load the selected skill through that registry
- confirm the loaded skill content or fingerprint matches the applied
  candidate
- confirm the loaded skill path is the real target path, not the overlay path
- invalidate or refresh any long-lived runtime skill registry that would
  otherwise keep serving the old content

For long-lived runtimes such as gateway processes, implementation MUST define
an explicit cache invalidation or registry reload hook. If the process cannot
prove that future tasks will observe the new skill without restart, verified
apply MUST fail or report that a restart/reload is required.

When feasible, post-apply verification SHOULD run a minimal replay or loader
smoke test after the fresh registry check. A pure `file == candidate` check is
not sufficient for this change.

### Scheduler Integration

Post-run enqueue remains best effort. The worker should drain jobs and run the
closed loop asynchronously:

1. build dataset from current trajectory
2. infer target
3. generate candidate
4. run overlay replay
5. evaluate paired trajectories
6. run gates
7. apply or reject according to policy
8. persist complete artifacts and final job status

The original task response must not wait for this loop. Worker failures must
be captured in job state and report artifacts.

An automatic self-evolve feature also needs a real drain entrypoint. This
change MUST add a framework-owned way for applications to run pending jobs,
for example a service hook, CLI command, or runtime background worker. Merely
documenting that callers may invoke `SelfEvolveJobWorker.drain_pending_jobs()`
is not enough for the `online` path.

The drain entrypoint MUST define its runtime model:

- whether jobs run in the current process or a separate worker process
- how event loops are handled so candidate replay does not call
  `asyncio.run()` inside an already-running loop
- how model/tool/browser resources are bounded
- how replay timeout and cancellation propagate to job state
- how many jobs may run concurrently

## Artifact Layout

Each run should persist enough evidence to audit the decision:

```text
.aworld/self_evolve/<run_id>/
  report.json
  target_selection.json
  target_provenance.json
  candidates/<candidate_id>/...
  overlays/<candidate_id>/<skill-name>/SKILL.md
  replay/<candidate_id>/
    request.json
    baseline_trajectory.log
    candidate_trajectory.log
    stdout.txt
    stderr.txt
    metrics.json
    failure.json
  evaluation/<candidate_id>/
    report.json
    extracted/
```

Exact filenames may vary, but the report must link to the relevant artifacts.

## Environment Boundaries

Self-evolve target inference must stay generic. It may infer that a skill
procedure should evolve, but it must not infer task environment setup as a
mutation target. Examples:

- a browser task may require Chrome/CDP to be available for replay
- a repository task may require dependencies to be installed
- a cloud task may require credentials or network access

Those are replay prerequisites. Missing prerequisites should produce replay
diagnostics, not a `config:` target unless a later implemented config adapter
and allowlist explicitly support that target.

## Failure Handling

- Overlay creation failure: reject candidate and persist diagnostics.
- Replay failure: reject or proposal-only; never verified apply.
- Evaluator failure: reject or proposal-only; never verified apply.
- Gate failure: reject for `auto_verified`, preserve proposal artifacts.
- Apply failure: mark run failed and keep original skill unchanged when
  possible.
- Post-apply verification failure: rollback from a persisted backup when
  possible, otherwise mark failed and report manual recovery information.
- Worker crash during apply: recover from persisted backup and apply journal
  on the next drain attempt before running new jobs.

Before writing the real skill, the runner MUST persist a backup of the
original skill content and an apply journal under the run artifacts. In-memory
rollback state is not sufficient for asynchronous verified apply.

## Testing Strategy

Use layered tests:

- unit tests for overlay directory creation and priority resolution
- unit tests for replay request extraction from trajectory evidence
- integration tests with a fake runtime executor that produces different
  baseline/candidate trajectories
- evaluator backend tests proving paired trajectories are passed to AWorld
  evaluator runtime
- runner tests proving `auto_verified` applies only after rerun metrics and
  gates pass
- scheduler tests proving background jobs drain through the closed loop
- CLI tests proving `aworld-cli optimize` remains thin
- post-apply loader tests proving a fresh runtime registry observes the new
  real skill and does not resolve the overlay
- cache invalidation tests for any long-lived registry path used by subsequent
  tasks
- replay variance tests covering baseline rerun, candidate repetitions, and
  stability-margin rejection
- crash-safety tests for persisted backup and recovery after interrupted apply
- negative tests for missing environment prerequisites, replay failure,
  evaluator failure, gate failure, and proposal/shadow non-mutation
