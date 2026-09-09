## 1. Contracts And Artifact Schema

- [x] 1.1 Define `TrajectorySet`, `TrajectorySetMember`, and member provenance
  models.
- [x] 1.1a Define the versioned `aworld.self_evolve.trajectory_set.v1` JSON file
  contract, including required fields, member role enum, path resolution,
  duplicate keys, cross-target handling, and member count limits.
- [x] 1.2 Define lesson record schema for failure causes, failure memories,
  success memories, lean solution paths, and required runtime behaviors.
- [x] 1.3 Define harness diagnostic lesson fields for context, workflow, tool
  protocol, evaluation, memory, permission-boundary, and artifact-lifecycle
  issues, with advisory promotion status.
- [x] 1.4 Reuse existing `EvaluationSummary`, `GateResult`, `TracePack`, feedback
  summaries, and self-evolve store serialization as diagnostic inputs instead of
  creating a separate runtime telemetry contract.
- [x] 1.5 Define self-evolve-owned `HarnessDiagnosticKind`,
  `LessonPromotionStatus`, and `HarnessDiagnostic` contracts.
- [x] 1.6 Define candidate strategy and patch intent schema.
- [x] 1.7 Define lineage memory schema with content, semantic, and lesson-set
  fingerprints.
- [x] 1.8 Define release normalization input/output metadata.
- [x] 1.9 Add artifact layout documentation and report links for trajectory
  sets, lessons, population, lineage, and normalized release content.
- [x] 1.10 Define evidence minimization, redaction, and untrusted-input handling
  contracts for lessons, optimizer prompts, reports, candidate content, and
  released skill content.

## 2. Trajectory Set Input

- [x] 2.1 Support building a trajectory set from one explicit trajectory for
  backward compatibility.
- [x] 2.2 Support loading a trajectory set from a framework-owned JSON file.
- [x] 2.2a Validate malformed trajectory-set files with diagnostics that include
  member index, field name, failure reason, and repair hint.
- [x] 2.2b Automatically group multi-record `--from-trajectory` logs by inferred
  target/task family so users do not need to author trajectory-set JSON for
  normal multi-trajectory usage.
- [x] 2.3 Support including prior self-evolve runs for the same target when
  requested by framework configuration or CLI.
- [x] 2.4 Normalize framework-owned prior accepted, rejected, replay, and
  follow-up production trajectories into derived set members without requiring
  users to hand-author those roles.
- [x] 2.5 Add tests for invalid, missing, duplicate, and mixed-source trajectory
  set members.
- [x] 2.6 Add tests for path traversal, absolute paths outside trusted roots,
  unsupported member roles, duplicate member keys, and cross-target advisory
  handling.

## 3. Lesson Extraction

- [x] 3.1 Implement extraction from evaluator reports into normalized metrics,
  failed gates, evidence issues, and required behaviors.
- [x] 3.2 Implement extraction from trajectory evidence into bounded failure and
  success memories without copying raw trace transcripts into prompts.
- [x] 3.3 Implement lean solution path extraction for successful trajectories.
- [x] 3.4 Ensure each lesson links to source run ids, task ids, and evidence
  refs.
- [x] 3.5 Add compaction rules for large tool outputs, artifact-backed evidence,
  and replay diagnostics.
- [x] 3.5a Add redaction rules for secret-like tokens, authorization headers,
  cookies, private local paths, personal identifiers, and prompt-injection-like
  evidence.
- [x] 3.6 Extract harness diagnostics from evidence gaps, artifact lifecycle
  issues, replay setup problems, tool protocol issues, evaluator inconsistencies,
  repeated rejected variants, and protected-path attempts.
- [x] 3.7 Implement `extract_harness_diagnostics(...)` under
  `aworld.self_evolve` using evaluation summaries, gate results, feedback
  summaries, trace packs, replay diagnostics, and prior-run lineage as inputs.
- [x] 3.8 Persist harness diagnostics as advisory lesson memory and keep source
  evidence refs attached.
- [x] 3.9 Add tests for evidence compaction, incomplete evidence, replay
  failures, high score successes, and mixed success/failure inputs.
- [x] 3.10 Add tests for context, workflow, tool protocol, evaluation, memory,
  permission-boundary, and artifact-lifecycle harness diagnostics.
- [x] 3.11 Add tests proving sensitive raw evidence and prompt-injection text are
  not copied into lessons, candidate prompts, reports, or released skill
  instruction bodies.

## 4. Candidate Population Generation

- [x] 4.1 Add a population-generation optimizer path that consumes lesson
  records rather than raw trajectory text alone.
- [x] 4.2 Generate multiple candidate strategy records with addressed lessons,
  considered harness diagnostics, preserved success behaviors, risk notes, and
  replay priority.
- [x] 4.3 Support no-op recommendations when no lesson-backed safe delta exists.
- [x] 4.4 Add high-baseline conservative-delta behavior that preserves lean
  solution paths and only proposes small verified improvements.
- [x] 4.5 Rank candidates before replay so only a bounded subset enters the
  expensive replay/evaluation loop.
- [x] 4.5a Implement deterministic population defaults and ranking semantics:
  default population size, max replayed candidates, hard replay limit, ranking
  weights, tie breakers, no-op threshold, and budget-exhausted behavior.
- [x] 4.6 Persist non-replayed candidate strategies for audit and future runs.
- [x] 4.7 Add tests for population ranking, no-op output, and high-baseline
  conservative candidate generation.
- [x] 4.7a Add tests for deterministic tie-breaking and candidates persisted as
  `not_replayed_due_to_budget`.
- [x] 4.8 Ensure harness diagnostics can influence candidate strategy hints but
  cannot be copied directly into production skill instructions.

## 5. Patch-Oriented Materialization

- [x] 5.1 Add patch intent application for local skill edits.
- [x] 5.2 Materialize patch candidates into full `SKILL.md` content before
  existing candidate replay.
- [x] 5.3 Validate front matter, reference links, protected files, and skill
  size after patch application.
- [x] 5.4 Reject or repair invalid patch outputs without entering replay.
- [x] 5.5 Add tests for minimal patch application, invalid references,
  protected-file edits, and whole-file rewrite prevention.

## 6. Lineage And Lesson Memory

- [x] 6.1 Persist lineage records for generated, replayed, rejected, and
  accepted candidates.
- [x] 6.2 Import prior single-run reports into lineage memory lazily for a
  target.
- [x] 6.3 Use exact content fingerprints to avoid exact duplicate candidates.
- [x] 6.4 Use semantic and lesson-set fingerprints to avoid repeated weak
  variants while allowing materially different candidates.
- [x] 6.5 Feed prior rejected/accepted lessons into future population generation.
- [x] 6.6 Add tests for repeated optimize on the same trajectory and for new
  candidates that reuse old lessons without matching old rejected behavior.

## 7. Release Normalization

- [x] 7.1 Add release normalization pass before verified apply.
- [x] 7.2 Remove task ids, source trajectory ids, baseline/candidate scoring
  language, and evaluator rubric details from runtime instruction bodies.
- [x] 7.3 Preserve release metadata in front matter or sidecar metadata.
- [x] 7.4 Preserve runtime behavior constraints that caused gates to pass.
- [x] 7.5 Preserve a mapping from each release-normalized runtime constraint back
  to lesson ids, including harness diagnostics when they were converted into a
  runtime-safe behavior delta.
- [x] 7.6 Make post-apply verification compare runtime-loaded content against
  normalized release content.
- [x] 7.6a Verify normalized release content before apply by semantic
  equivalence for all gate-critical runtime constraints or by replay/gate
  fallback when equivalence cannot be established.
- [x] 7.7 Add tests proving production skills do not expose self-evolve internal
  wording after apply.
- [x] 7.8 Add tests proving raw harness diagnostic labels and evidence ids are not
  leaked into runtime instruction bodies.
- [x] 7.9 Add tests proving apply is rejected when release normalization removes a
  behavior constraint required by accepted gates.
- [x] 7.10 Record pre-normalization fingerprints, normalized release fingerprints,
  preserved runtime constraints, and normalization verification status in run
  reports.

## 8. Runner, Gates, And Reports

- [x] 8.1 Integrate trajectory-set learning before existing replay selection.
- [x] 8.2 Ensure existing replay/evaluator/gate/apply loop is reused unchanged
  after candidates are materialized.
- [x] 8.3 Add `FilesystemSelfEvolveStore` support for writing harness diagnostics
  under the existing `.aworld/self_evolve/<run_id>/` artifact layout.
- [x] 8.4 Add report sections for trajectory set, lesson extraction, population,
  harness diagnostics, lineage, no-op, and release normalization.
- [x] 8.5 Label acceptance confidence as single-trajectory, repeated
  single-case replay, trajectory-set validation, or held-out validation.
- [x] 8.6 Ensure no-op optimize results do not report `succeeded` as if a skill
  was applied.
- [x] 8.7 Add tests for no-op reporting, trajectory-set reporting, and
  normalized accepted candidate reporting.
- [x] 8.8 Add tests for harness diagnostic counts, advisory diagnostics not
  promoted, and diagnostics that were converted into candidate strategy hints.

## 9. CLI And Slash Command

- [x] 9.1 Add optional `aworld-cli optimize --from-trajectory-set` thin argument.
- [x] 9.2 Add optional `--include-prior-runs` thin argument if framework support
  exists.
- [x] 9.3 Add equivalent slash command forwarding without duplicating framework
  logic.
- [x] 9.4 Display progress for trajectory-set loading, lesson extraction,
  population generation, candidate replay, evaluation, and release
  normalization.
- [x] 9.5 Add CLI tests proving arguments are forwarded to framework APIs and
  CLI does not implement learning logic.

## 10. Documentation And Built-In Skill Guidance

- [x] 10.1 Document single-trajectory versus trajectory-set optimize modes.
- [x] 10.2 Document trajectory-set file format and prior-run inclusion.
- [x] 10.3 Document lesson memory, harness diagnostics, lineage, no-op, and
  release normalization.
- [x] 10.4 Update built-in `self_evolve` skill guidance after framework tests
  pass.
- [x] 10.5 Document that domain-specific skills such as web-content grounding are
  examples of learned targets, not framework logic.

## 11. Verification

- [x] 11.1 Run focused self-evolve unit tests.
- [x] 11.2 Run CLI optimize and slash command tests.
- [x] 11.3 Run evaluator runtime tests to confirm existing replay/evaluator
  behavior is not regressed.
- [x] 11.4 Run skill release visibility tests.
- [x] 11.5 Manually verify a small trajectory-set optimize flow using a
  user-authored baseline trajectory input plus framework-owned prior accepted,
  rejected, replay, or follow-up members imported from self-evolve run history.
- [x] 11.6 Run strict OpenSpec validation for the trajectory-set learning change.

## 12. Member Replay And Runtime Candidate Follow-Up

- [x] 12.1 Execute paired baseline/candidate replay independently for each
  replayable trajectory-set member.
- [x] 12.2 Persist and restore member-scoped replay manifests, including reused
  baseline artifacts for candidate populations.
- [x] 12.3 Map only each member's own trajectories into evaluator cases and
  preserve train/validation/held-out split membership.
- [x] 12.4 Count independent held-out members separately from replay
  repetitions and skip evaluator calls for empty splits.
- [x] 12.5 Materialize bounded runtime behavior deltas without task ids,
  evidence ids, gate names, scores, or prior feedback text in candidate bodies.
- [x] 12.6 Add tests for member isolation, partial failure diagnostics, stored
  replay recovery, path collisions, split isolation, and runtime-only
  candidates.
- [x] 12.7 Preserve task-level baseline failures as typed comparable outcomes
  when candidate replay succeeds and source trajectory evidence exists, while
  keeping infrastructure failures as verified-apply blockers.
