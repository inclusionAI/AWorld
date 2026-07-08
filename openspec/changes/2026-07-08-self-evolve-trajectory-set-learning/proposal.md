## Why

AWorld self-evolve has proven the end-to-end runtime loop for skill targets:
baseline trajectory evidence can produce a candidate skill, candidate replay can
run through the real runtime, evaluator gates can accept the candidate, and
post-apply verification can make the skill visible to later tasks.

The next bottleneck is no longer replay plumbing or gate wiring. Recent
trajectory comparisons show that a single trajectory can expose a useful
direction, but it is too narrow to reliably produce transferable runtime
skills. A single case can overfit candidate content to one task, carry
self-evolve scoring language into released skills, and produce candidates that
do not consistently improve high-baseline runs.

This change moves self-evolve from "single trajectory mutation" toward
"trajectory set learning": multiple related trajectories are analyzed into
failure memories, success memories, and lean solution paths before candidate
generation. Candidate generation then works from normalized lessons and a
population of local patches, not from raw task traces alone.

This direction is informed by the observed AWorld runs and by the Trace2Skill
pattern of:

1. analyzing trace pools into reusable lessons
2. proposing local patches from independent trace evidence
3. merging and validating non-conflicting skill changes
4. selecting evolved skills through validation rather than assuming every edit
   is useful

## What Changes

- Add trajectory set inputs to self-evolve optimize so a run can learn from a
  curated set of baseline, failed candidate, accepted candidate, and follow-up
  production trajectories.
- Add a lesson extraction layer that converts trajectories and evaluator
  reports into normalized failure memories, success memories, lean solution
  paths, and required runtime behaviors.
- Add harness diagnostics as advisory lesson memory so context, workflow, tool
  protocol, evaluation, memory, permission-boundary, and artifact-lifecycle
  issues are recorded without immediately becoming runtime skill instructions.
  This reuses existing self-evolve summaries, gates, trace packs, feedback, and
  artifact storage as inputs while adding a self-evolve-owned diagnostic record
  and extraction layer.
- Add candidate population generation from lesson records instead of producing
  only one direct mutation from one trajectory.
- Add lineage and lesson memory so repeated optimize runs know which lessons,
  candidates, and failure modes were already tried.
- Add a patch-oriented candidate generation path that can produce minimal,
  targeted skill edits before materializing a full `SKILL.md` candidate.
- Add a release normalization pass so accepted production skills contain
  runtime-facing instructions, not self-evolve internal scoring language,
  source task ids, or candidate/baseline details.
- Add trajectory-set reports that explain which lessons drove a candidate,
  which successful behaviors were preserved, and which failed behaviors were
  prevented.
- Keep replay, evaluator, gate, apply, and runtime loader semantics in the
  existing `aworld.self_evolve` framework. This change improves learning and
  candidate quality; it does not move framework logic into the built-in
  `self_evolve` skill.

## Capabilities

### New Capabilities

- `self-evolve-trajectory-set-learning`: AWorld can optimize from a set of
  related trajectories rather than a single trajectory.
- `self-evolve-lesson-extraction`: AWorld can normalize trajectory and
  evaluator evidence into failure memories, success memories, lean solution
  paths, and required runtime behaviors.
- `self-evolve-harness-diagnostics`: AWorld can record evidence-backed harness
  diagnostics as advisory lessons before any framework or runtime behavior is
  changed.
- `self-evolve-candidate-population`: AWorld can generate and rank multiple
  candidate patch strategies before choosing candidates for replay.
- `self-evolve-lineage-memory`: AWorld can persist candidate ancestry,
  rejected/accepted variants, and lesson-to-candidate provenance.
- `self-evolve-release-normalization`: AWorld can convert accepted candidate
  skill content into runtime-facing skill guidance before making it visible to
  normal skill loading.

### Modified Capabilities

- `self-evolve-framework`: candidate generation expands from single-pass
  mutation to lesson-backed population search.
- `aworld-cli-self-evolve`: CLI may accept trajectory-set inputs and reports
  lesson/candidate lineage, but remains a thin wrapper over framework APIs.
- `built-in-self-evolve-skill`: guidance may describe trajectory-set operation
  and boundaries, but it MUST NOT own lesson extraction, population ranking,
  replay, gates, or release writes.

## Impact

- Affected framework areas:
  - `aworld.self_evolve.runner`
  - `aworld.self_evolve.optimizers`
  - `aworld.self_evolve.datasets`
  - `aworld.self_evolve.trace_pack`
  - `aworld.self_evolve.evaluation`
  - `aworld.self_evolve.store`
  - `aworld.self_evolve.gates`
  - `aworld.self_evolve.targets`
  - `.aworld/self_evolve/` artifact layout
- Affected CLI/docs areas:
  - `aworld-cli optimize`
  - `/optimize` slash command
  - `docs/AWorld CLI/Commands/Optimize.md`
  - `docs/Agents/Self Evolve.md`
  - `aworld-skills/self_evolve/SKILL.md`

## Safety Constraints

- Single trajectory optimize MUST remain supported as the simplest input mode,
  but reports must label the evidence scope clearly.
- A trajectory set MUST NOT bypass replay, evaluator, or apply gates.
- Lesson extraction MUST be evidence-backed. It may summarize raw traces, but
  every lesson must link to source trajectory ids, evaluator reports, or
  replay diagnostics.
- Candidate generation MUST avoid copying task-specific facts, URLs, ids, or
  evaluator scoring details into production skill instructions unless they are
  explicitly part of a general runtime rule.
- Release normalization MUST preserve behavior required by passed gates while
  removing self-evolve internal language.
- Duplicate rejected and duplicate accepted candidate handling MUST work at
  both content-fingerprint and semantic-lineage levels.
- Failed or low-confidence lessons MUST be persisted as advisory memory, not
  silently promoted into runtime skills.
- Harness diagnostics MUST remain advisory unless a later candidate converts
  them into a runtime-safe behavior delta backed by replay and gates.
- Candidate population replay MUST remain budgeted. The framework should rank
  local candidates before replaying only the most promising bounded subset.

## Non-Goals

- Do not reimplement the replay harness, evaluator runtime, or apply gates.
- Do not make `aworld-cli` own trajectory-set learning logic.
- Do not require Trace2Skill as a runtime dependency.
- Do not require a fixed evaluator agent such as `~/Documents/agent.md`.
- Do not make all self-evolve tasks require 30 independent held-out tasks.
  Single-case repeated replay can still be a valid limited-scope verification
  mode when policy allows it.
- Do not optimize a specific web page, podcast source, browser setup, or tool
  command. Web-content grounding is one observed case; the framework capability
  must stay domain-general.
- Do not allow this change to mutate `aworld.self_evolve` harness/framework code
  from harness diagnostics. Diagnostics are recorded for visibility and future
  candidate guidance; framework self-mutation requires a separate change.
