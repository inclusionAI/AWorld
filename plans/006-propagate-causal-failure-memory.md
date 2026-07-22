# Plan 006: Propagate causal failure events into diagnostics and lesson memory

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan in
> `plans/README.md` unless a reviewer told you they maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat 70cb5c9a..HEAD -- aworld/self_evolve/failure_events.py aworld/self_evolve/diagnostics.py aworld/self_evolve/lessons.py aworld/self_evolve/evolution_context.py aworld/self_evolve/runner.py aworld/self_evolve/store.py tests/self_evolve/test_diagnostics.py tests/self_evolve/test_lessons.py tests/self_evolve/test_evolution_context.py tests/self_evolve/test_runner.py tests/self_evolve/test_framework_contract_matrix.py`
> `failure_events.py` may not exist yet. If plan 004 used a different module for
> typed failure events, use that module and update this plan's path before
> editing; do not create a competing event type.

## Status

- **Implementation**: DONE at `41ae5be2` after three architecture review cycles
- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: `plans/004-unify-replay-lifecycle-semantics.md`
- **Category**: tech-debt
- **Planned at**: commit `70cb5c9a`, 2026-07-21

## Why this matters

The framework already observes actionable replay failures, but the information
is flattened to failed gate names before it reaches harness diagnostics and
lesson memory. Repeated failures then produce duplicate lesson rows instead of
one causal memory with occurrence evidence. This plan makes typed failure events
the shared, sanitized currency from replay through feedback, diagnostics,
lessons, and optimizer context, with aggregation that works the same for one
trajectory, repeated failures across many trajectories, and heterogeneous
multi-trajectory failures.

## Current state

- `aworld/self_evolve/runner.py:6188-6220` builds typed feedback fragments, but
  `runner.py:6158-6185` prefers the candidate slot whenever it exists and can
  discard a candidate-owned root cause that surfaced during baseline preflight.
- `aworld/self_evolve/diagnostics.py:61-75` turns any candidate-replay or replay-
  confidence failure into one generic workflow diagnostic.
- `aworld/self_evolve/diagnostics.py:205-223` preserves only replay statuses and
  repetition counts; it does not consume nested failure owner/stage/code.
- `aworld/self_evolve/lessons.py:32-96` appends one record per feedback item and
  returns the list without semantic aggregation.
- `aworld/self_evolve/lessons.py:116-129` computes identical IDs for identical
  semantic payloads, but callers and persistence do not merge those records.
- `aworld/self_evolve/lessons.py:142-187` keeps score/evidence counters and gate
  names but loses most causal failure fields.
- `aworld/self_evolve/store.py:142-150` writes every lesson directly to JSONL.
- `aworld/self_evolve/evolution_context.py:1033-1050` sends the first bounded
  slice of lessons to the optimizer without deduplication or severity ranking.

Aggregation must use a semantic failure key, not case count. The same failure
across ten trajectories becomes one memory with ten occurrences; two different
failure codes across the same ten trajectories remain two memories.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Diagnostic units | `python -m pytest tests/self_evolve/test_diagnostics.py -q` | all pass |
| Lesson units | `python -m pytest tests/self_evolve/test_lessons.py -q` | all pass |
| Prompt context | `python -m pytest tests/self_evolve/test_evolution_context.py -k "lesson or diagnostic or repair_conformance" -q` | all selected tests pass |
| Runner persistence | `python -m pytest tests/self_evolve/test_runner.py -k "lesson or harness_diagnostic or typed_gate_feedback" -q` | all selected tests pass |
| Contract matrix | `python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q` | all pass |

## Scope

**In scope**

- The typed failure-event module introduced by plan 004
- `aworld/self_evolve/diagnostics.py`
- `aworld/self_evolve/lessons.py`
- `aworld/self_evolve/evolution_context.py`
- `aworld/self_evolve/runner.py`
- `aworld/self_evolve/store.py`
- `aworld/self_evolve/types.py` only if shared serialization types require it
- `tests/self_evolve/test_diagnostics.py`
- `tests/self_evolve/test_lessons.py`
- `tests/self_evolve/test_evolution_context.py`
- `tests/self_evolve/test_runner.py`
- `tests/self_evolve/test_framework_contract_matrix.py`

**Out of scope**

- Copying raw replay responses, tool transcripts, secrets, or full stderr into
  lessons/prompts.
- A diagnostic rule tied to a specific error string, target, transport, or run.
- Increasing prompt limits to hide duplicate memory.
- Using trajectory count alone to promote an advisory into runtime behavior.
- Persisting separate duplicate lessons for each case.

## Git workflow

- Branch: `codex/006-causal-failure-memory`
- Suggested commit: `fix(self-evolve): preserve causal diagnostics in lesson memory`
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Define the semantic event identity used across layers

Extend the typed event from plan 004 with a stable, sanitized semantic key based
on fields that describe the failure itself:

- owner;
- stage;
- stable code;
- target scope and capability/requirement identity when available;
- repairability;
- bounded protocol/contract category.

Exclude occurrence-specific fields from the semantic key:

- case ID, task ID, run ID, candidate ID;
- baseline/candidate slot;
- timestamps and artifact paths;
- raw reason text and payload excerpts.

Store occurrence metadata separately: bounded case/task/run/candidate IDs,
artifact refs, first/last seen markers if available, and occurrence count. The
event serializer must enforce the existing sanitization and size limits.

**Verify**: add tests proving that the same semantic event from different cases
has the same key, while different stage/code/owner values do not; run
`python -m pytest tests/self_evolve/test_diagnostics.py tests/self_evolve/test_lessons.py -k "semantic or event" -q`
→ all selected tests pass.

### Step 2: Select causal events by ownership, not replay slot

Replace `_candidate_repair_diagnostic_view` slot preference with event
selection rules:

- candidate-owned failures are candidate repair evidence even when surfaced
  while starting the baseline slot;
- shared infrastructure events become harness diagnostics, not candidate repair
  instructions;
- task-owned failures remain task-quality evidence;
- blocked/not-run variants contribute the causal `blocked_by` event but do not
  create a new execution-failure event.

For multiple members, collect events through the normalized iterator from plan
004 and aggregate by semantic key. Preserve affected member counts and bounded
case IDs.

**Verify**:
`python -m pytest tests/self_evolve/test_runner.py -k "typed_gate_feedback or baseline_preflight or repair_diagnostic" -q`
→ candidate-owned root cause is retained regardless of slot.

### Step 3: Make harness diagnostics causal and typed

Refactor `extract_harness_diagnostics` to consume typed events in addition to
gate summaries:

- protocol/capability events produce `TOOL_PROTOCOL` diagnostics with code,
  stage, owner, repairability, occurrence count, and artifact refs;
- shared infrastructure events produce `WORKFLOW` diagnostics with shared-run
  scope;
- insufficient comparable evidence remains a workflow diagnostic only when no
  more specific causal event explains it;
- promotion status must be supplied explicitly by a documented rule rather
  than always defaulting through `_diagnostic`.

Retain gate names as affected outputs, but do not make them the diagnostic
identity.

**Verify**:
`python -m pytest tests/self_evolve/test_diagnostics.py -q`
→ tests distinguish protocol, task, and infrastructure causes and verify no raw
evidence is copied.

### Step 4: Aggregate lesson records before persistence and prompting

Update lesson extraction so it returns one `LessonRecord` per semantic lesson
identity. Extend `LessonRecord` additively with:

- `occurrence_count`;
- bounded unique source run/task/candidate IDs;
- bounded affected case count/IDs;
- causal owner/stage/code fields in sanitized metrics;
- distinct-source counts used for confidence/promotion.

Merge evidence refs deterministically and cap them. Report separately:

- raw lesson occurrence count;
- unique lesson count;
- counts by type/code;
- maximum and total occurrence counts.

Persistence must reject or aggregate duplicate IDs rather than writing repeated
JSONL rows. Existing lesson artifacts must remain readable with a default
occurrence count of one.

**Verify**:
`python -m pytest tests/self_evolve/test_lessons.py tests/self_evolve/test_runner.py -k "lesson" -q`
→ duplicate feedback produces one row with the correct occurrence count.

### Step 5: Rank bounded optimizer memory by value

Change `_lesson_payloads` to consume unique records and select them by a stable
policy such as:

1. repairable causal failures relevant to the active target/contract;
2. high-confidence required runtime behavior;
3. recurrent failures from distinct tasks/runs;
4. success-preservation memories;
5. deterministic tie-break by semantic ID.

Do not rank a lesson higher solely because the same iteration emitted it many
times. Include occurrence and distinct-source counts in the bounded payload so
the optimizer can distinguish recurrence from duplication.

**Verify**:
`python -m pytest tests/self_evolve/test_evolution_context.py -k "lesson or diagnostic" -q`
→ unique high-value causal memories are retained under the context limit.

### Step 6: Add single/repeated/heterogeneous trajectory tests

Extend the contract matrix:

- one trajectory, one causal event → one lesson, occurrence one;
- three trajectories, same event → one lesson, occurrence three;
- three trajectories, two event codes → two lessons with exact occurrences;
- one candidate-owned event surfaced in baseline slot plus two blocked members
  → one candidate event, not three execution failures;
- one shared infrastructure event affecting all members → one workflow
  diagnostic with affected-member count;
- duplicate feedback from the same case/iteration must not inflate distinct
  source counts.

**Verify**:
`python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q`
→ all aggregation cells pass.

## Test plan

- Expand `tests/self_evolve/test_lessons.py`, which currently covers mostly
  single feedback items, with duplicate and heterogeneous event sequences.
- Expand `tests/self_evolve/test_diagnostics.py` to assert causal metrics, not
  only diagnostic kind.
- Add legacy lesson deserialization coverage.
- Test deterministic ordering regardless of member input order.
- Test sanitization and bounded IDs/refs for many trajectories.
- Verify prompt payload contains no raw response context.

## Done criteria

- [ ] Replay, feedback, diagnostics, and lessons share one typed event identity.
- [ ] Candidate-owned causes are preserved regardless of baseline/candidate
  slot.
- [ ] Identical events across trajectories aggregate into one lesson.
- [ ] Heterogeneous events remain distinct.
- [ ] Lesson reports expose raw occurrence and unique counts separately.
- [ ] Duplicate lesson IDs are not persisted as duplicate JSONL rows.
- [ ] Optimizer context ranks unique causal memories without increasing limits.
- [ ] One- and multi-trajectory aggregation tests pass.
- [ ] No raw replay/tool payloads enter diagnostics or lessons.
- [ ] Focused and full subsystem tests pass.
- [ ] No files outside Scope and the plan index are modified.

## STOP conditions

Stop and report if:

- Plan 004 did not provide a typed owner/stage/code event and adding one here
  would duplicate another representation.
- A useful semantic identity would require hashing or persisting raw secrets or
  payloads.
- Existing lesson consumers require duplicate JSONL rows for correctness.
- Promotion semantics cannot distinguish repeated writes from distinct runs or
  tasks.
- Artifact compatibility would require silently changing an existing lesson ID
  to mean a different failure.

## Maintenance notes

Future gates should emit typed causal events and then derive user-facing text;
they should not make prose strings the integration API. Reviewers should verify
that recurrence means distinct evidence sources, not repeated serialization of
the same feedback item. Trajectory cardinality affects occurrence metadata, not
the failure taxonomy.
