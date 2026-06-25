## ADDED Requirements

### Requirement: Self-evolve MUST evaluate skill candidates through an isolated runtime overlay

AWorld MUST be able to mount a generated skill candidate as an isolated runtime
overlay for candidate evaluation. The overlay MUST NOT mutate the installed
real skill before framework apply gates pass.

#### Scenario: Candidate overlay is created

- **WHEN** self-evolve generates a candidate for `skill:<name>`
- **THEN** AWorld MUST write the candidate skill content into a run-scoped
  overlay directory
- **AND** the overlay MUST preserve the normal skill directory shape
- **AND** the overlay artifact MUST record candidate id, target path, target
  fingerprint, and provenance

#### Scenario: Candidate overlay is used during replay

- **WHEN** AWorld reruns a task to evaluate the candidate
- **THEN** the runtime MUST resolve the selected skill from the candidate
  overlay for that replay
- **AND** unrelated skills MUST resolve through normal installed skill paths
- **AND** the real skill file MUST remain unchanged during replay

#### Scenario: Shadow skill root is constructed for loader compatibility

- **WHEN** AWorld prepares candidate replay for `skill:<name>`
- **THEN** it MUST construct a materialized shadow skill root containing the
  candidate skill and every other baseline-visible skill except the original
  selected target skill
- **AND** it MUST pass that shadow root to replay loaders as the effective
  `skills_path` or first framework-controlled skill source
- **AND** replay MUST instantiate a fresh skill registry from that shadow root
  instead of reusing baseline registry or content-cache objects

#### Scenario: Proposal or shadow mode evaluates a candidate

- **WHEN** apply policy is `proposal` or mode is `shadow`
- **THEN** AWorld MAY create and evaluate candidate overlays
- **AND** it MUST NOT write the candidate back to the installed real skill

### Requirement: Self-evolve MUST rerun the same task under the candidate overlay

AWorld MUST provide a framework-owned replay harness that reruns the original
task using the candidate skill overlay and captures a candidate trajectory.

#### Scenario: Replay request is built from trajectory evidence

- **WHEN** a self-evolve run has a baseline trajectory and selected skill
  target
- **THEN** AWorld MUST build a replay request containing task id, task input,
  workspace root, selected target, candidate id, overlay path, and budgets
- **AND** it SHOULD include agent identity when available from invocation
  context or trajectory metadata

#### Scenario: Candidate task rerun succeeds

- **WHEN** the replay harness runs the task with the candidate overlay
- **THEN** AWorld MUST capture a candidate trajectory
- **AND** it MUST persist replay metrics such as status, latency, step count,
  token usage, and cost when available
- **AND** it MUST link replay artifacts from the self-evolve report

#### Scenario: Candidate task rerun cannot execute

- **WHEN** replay prerequisites are missing or the runtime fails before a
  candidate trajectory is produced
- **THEN** AWorld MUST persist structured replay failure diagnostics
- **AND** `auto_verified` MUST reject the candidate or leave it as proposal
- **AND** the failure MUST NOT be converted into a task-specific mutation target

### Requirement: Self-evolve MUST compare paired baseline and candidate trajectories

AWorld MUST build paired trajectory evaluation cases so evaluator agents score
the original baseline trajectory against the candidate rerun trajectory for
the same task.

#### Scenario: Paired evaluation case is constructed

- **WHEN** candidate replay produces a candidate trajectory
- **THEN** AWorld MUST construct an evaluation case that includes both baseline
  and candidate trajectories for the same task id
- **AND** it MUST include original task input and candidate metadata
- **AND** it MUST mark which trajectory belongs to each variant id

#### Scenario: Evaluator agent scores paired trajectories

- **WHEN** a trajectory evaluator backend is configured
- **THEN** AWorld MUST pass the baseline trajectory for the baseline variant
  and candidate trajectory for the candidate variant
- **AND** evaluator metrics MUST be stored as comparable baseline and
  candidate summaries
- **AND** candidate selection MUST use those metrics instead of text-only
  candidate-change heuristics when replay metrics exist

#### Scenario: Candidate improves only by evaluator-agent judgment

- **WHEN** the candidate trajectory improves only according to an LLM evaluator
  signal and lacks required deterministic or held-out verification
- **THEN** AWorld MAY persist the candidate as a proposal
- **AND** it MUST NOT mark the candidate as verified for automatic application

### Requirement: Verified replay MUST control stochastic variance

AWorld MUST account for replay variance before marking a candidate verified.
It MUST NOT treat a single fresh candidate rerun compared with one historical
baseline trajectory as sufficient proof for `auto_verified` apply.

#### Scenario: Baseline can be replayed

- **WHEN** baseline skill content and replay prerequisites are available
- **THEN** AWorld MUST rerun both baseline and candidate under the same replay
  environment for verified apply
- **AND** it MUST compare aggregate baseline and candidate metrics
- **AND** the candidate MUST clear the configured improvement and stability
  margins

#### Scenario: Baseline cannot be replayed

- **WHEN** only the original baseline trajectory is available
- **THEN** AWorld MUST either run multiple candidate replays and require a
  stronger aggregate margin or downgrade the result to limited confidence
- **AND** it MUST NOT mark the candidate verified when replay variance makes
  the improvement inconclusive

#### Scenario: Multiple candidates are proposed

- **WHEN** an optimizer returns more candidates than the replay candidate limit
- **THEN** AWorld MUST replay only the configured maximum number of candidates
- **AND** skipped candidates MUST remain proposals without verified apply
- **AND** replay cost estimates MUST include baseline repetitions, candidate
  repetitions, and judge repetitions

### Requirement: Auto-verified apply MUST depend on candidate rerun and gates

For skill targets, `auto_verified` MUST apply a candidate only after candidate
rerun, paired evaluation, safety gates, and post-apply verification succeed.

#### Scenario: Candidate passes verified replay gates

- **WHEN** candidate replay succeeds
- **AND** candidate evaluator score improves over baseline by the configured
  minimum delta
- **AND** evaluator trajectory gate passes
- **AND** required deterministic, held-out, protected-path, trust provenance,
  budget, token, malformed, noop, cost, and latency gates pass
- **THEN** AWorld MAY apply the candidate to the installed real skill when the
  target type is allowlisted for `auto_verified`
- **AND** it MUST persist apply status and post-apply verification result

#### Scenario: Any verified replay gate fails

- **WHEN** replay, evaluator, improvement, safety, or post-apply verification
  fails
- **THEN** AWorld MUST NOT leave an unverified candidate applied
- **AND** it MUST preserve proposal artifacts and diagnostics
- **AND** the run status MUST clearly indicate rejected or failed state

#### Scenario: Post-apply verification fails

- **WHEN** AWorld writes the candidate to the real skill but post-apply
  verification fails
- **THEN** AWorld MUST rollback to the original skill content when the
  persisted backup is available
- **AND** it MUST mark the run failed if rollback cannot be proven
- **AND** it MUST persist recovery diagnostics

#### Scenario: Post-apply runtime loader verification runs

- **WHEN** AWorld applies a candidate to the installed real skill
- **THEN** post-apply verification MUST create a fresh runtime skill registry
  from the real production skill sources
- **AND** it MUST load the selected skill through that registry
- **AND** it MUST confirm the loaded skill content or fingerprint matches the
  applied candidate
- **AND** it MUST confirm the loaded skill path is the real target path rather
  than the candidate overlay path
- **AND** a file-content equality check alone MUST NOT satisfy post-apply
  verification

#### Scenario: Long-lived runtime cache may serve old skill content

- **WHEN** a runtime keeps a long-lived skill registry or content cache
- **THEN** AWorld MUST invalidate or refresh that cache after verified apply
- **AND** verified apply MUST fail or require an explicit reload/restart when
  the framework cannot prove subsequent tasks will observe the new skill

#### Scenario: Worker crashes during apply

- **WHEN** an asynchronous worker writes a real skill during verified apply
- **THEN** AWorld MUST have persisted the original skill backup and apply
  journal before the write
- **AND** the next worker drain MUST recover or report the interrupted apply
  before starting new jobs

### Requirement: Post-run worker MUST run the closed loop asynchronously

AWorld post-run self-evolve jobs MUST be able to execute the full candidate
rerun loop without blocking the original task response.

#### Scenario: Online post-run job drains successfully

- **WHEN** self-evolve is enabled in `online` mode with `auto_verified`
- **AND** a pending job has current trajectory evidence
- **THEN** the job worker MUST generate a candidate, create an overlay, rerun
  the task, evaluate paired trajectories, run gates, and apply or reject the
  candidate according to policy
- **AND** the job status MUST become `succeeded` only when the framework run
  completes and reports final status

#### Scenario: Runtime drain entrypoint is configured

- **WHEN** an application enables post-run self-evolve in `shadow` or `online`
  mode
- **THEN** AWorld MUST provide a real drain entrypoint such as a runtime
  background worker, service hook, or CLI/service command
- **AND** the entrypoint MUST define event-loop handling, timeout handling,
  resource limits, and concurrency limits
- **AND** documentation alone MUST NOT be considered sufficient for automatic
  post-run self-evolve

#### Scenario: Worker encounters replay or evaluator failure

- **WHEN** replay or evaluator execution fails during a background job
- **THEN** the worker MUST mark the job failed or rejected with diagnostics
- **AND** it MUST NOT raise into the original user task path
- **AND** it MUST NOT mutate installed skills unless verified apply already
  completed and post-apply verification passed

### Requirement: Replay prerequisites MUST remain separate from mutation target inference

Self-evolve target inference MUST identify evolvable AWorld capability targets,
not task-specific runtime prerequisites.

#### Scenario: Task requires browser or external runtime state

- **WHEN** a replayed task requires a browser, CDP connection, profile,
  credentials, network access, repository dependencies, or another external
  prerequisite
- **THEN** AWorld MUST treat that requirement as replay environment context
- **AND** it MUST NOT infer a hard-coded config target from the prerequisite
  unless a later implemented and allowlisted config target adapter explicitly
  supports it

#### Scenario: Replay prerequisite is missing

- **WHEN** a required replay environment prerequisite is unavailable
- **THEN** AWorld MUST report a replay prerequisite failure
- **AND** verified apply MUST be blocked
- **AND** target inference MUST remain generic and based on installed target
  inventory plus trajectory evidence

### Requirement: CLI MUST remain a thin surface for candidate rerun self-evolve

`aworld-cli optimize` MUST remain a thin delegation surface. It MAY expose
flags that request or report candidate rerun behavior, but CLI MUST NOT own
overlay, replay, evaluator, gate, apply, or artifact semantics.

#### Scenario: User invokes optimize with candidate rerun enabled

- **WHEN** a user runs `aworld-cli optimize` with trajectory evidence and a
  verified apply policy
- **THEN** CLI MUST delegate rerun behavior to `aworld.self_evolve`
- **AND** CLI output MAY include replay artifact paths and evaluator report
  paths returned by the framework
- **AND** CLI MUST NOT implement a separate rerun loop

#### Scenario: Validation script checks design goal

- **WHEN** the self-evolve trajectory validation script runs in strict verified
  mode
- **THEN** it MUST require evidence that a candidate trajectory was generated
  by framework replay
- **AND** it MUST fail if only a candidate text proposal was produced
