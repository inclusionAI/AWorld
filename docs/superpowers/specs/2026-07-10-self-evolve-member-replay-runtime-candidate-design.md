# Self-Evolve Member Replay And Runtime Candidate Design

## Scope

This change completes two missing boundaries in trajectory-set self-evolve:

1. Every replayable trajectory-set member receives its own baseline/candidate paired replay.
2. Candidate skill content contains only runtime-executable behavior rules. Trace ids, evaluator metrics, gate names, and prior-run diagnostics remain in lessons, lineage, and reports.

Artifact retention and lifecycle management are explicitly out of scope for this change.

## Member Replay

`AWorldCliCandidateReplayBackend` remains compatible with the existing single-request protocol, but becomes dataset-aware. For a multi-member dataset it derives one member request per replayable case, runs the configured baseline and candidate repetitions in a task-scoped artifact directory, and returns member results keyed by `case_id`.

The top-level replay result aggregates status and metrics for gates while preserving each member result. `build_paired_replay_dataset` maps only a member's own trajectories back to that member. It must never copy one member's replay trajectories into another member. A member with no successful baseline or candidate trajectory remains an explicit failed member and identifies its `case_id` in replay diagnostics.

Single-case replay keeps the existing artifact layout and stored replay contract. Multi-member stored replay includes a member manifest and task-scoped directories so evaluator-only resume can reconstruct the same mapping.

## Cross-Member Improvement

Raw member trajectories are not concatenated into one optimizer prompt. Each member produces bounded replay/evaluator feedback with source provenance. Existing feedback normalization and lesson extraction combine common failures, preserved successes, metric regressions, and required behaviors across members. Candidate generation consumes this normalized lesson set while lineage retains the originating case ids.

## Runtime-Only Candidate

The default CLI candidate materializer no longer appends `Self-Evolve Trace Guidance` or `Self-Evolve Targeted Delta`. It emits a bounded `Runtime Behavior Delta` containing only tool-agnostic instructions an agent can execute during a task.

Candidate bodies must not contain source task ids, evidence ids, candidate/baseline scores, gate names, evaluator dimension names, or previous validation summaries. Those remain available in optimizer prompts, lesson records, diagnostics, and lineage artifacts. If the normalized repair plan contains no actionable runtime behavior, the materializer returns unchanged content so the existing no-op gate rejects the mutation without replay cost.

## Verification

Tests cover multi-member execution and artifact isolation, exact member-to-trajectory mapping, partial member failure diagnostics, stored replay reconstruction, single-case compatibility, and runtime-only candidate content. Existing replay, runner, evaluator, release-normalization, and skill tests remain green.
