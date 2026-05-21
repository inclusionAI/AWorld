# CLI Memory Follow-Ups Backlog

## Purpose

This document organizes the work that remains **outside** the currently
delivered scope of
`openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider`.

It is not a new implementation spec. It is a grounded backlog extracted from:

- the current OpenSpec proposal and design
- the implemented branch boundary
- the completed `llm_calls` truth-source and cache-observability milestone

The goal is to separate:

1. what this branch already finished
2. what the current change explicitly deferred
3. what should become the next smallest follow-up changes

## Current Change Boundary

The current branch has already delivered:

- hybrid provider seam for CLI durable memory + runtime memory coexistence
- layered workspace instruction loading under `~/.aworld/AWORLD.md`,
  `W/.aworld/AWORLD.md`, and compatibility read from `W/AWORLD.md`
- built-in `memory` plugin commands and turn-end hook integration
- explicit durable writes through `/memory` and `/remember`
- workspace-scoped append-only session logs
- selective relevant recall from session logs
- append-only `llm_calls` capture as the truth source for real model requests
- internal + provider `request_id` propagation
- normalized usage + raw usage preservation
- trajectory preference for `llm_calls[*].request.messages`
- cache usage exclusion from trajectory semantic messages
- request-linked cache observability in logs, HUD, hook payloads, session logs,
  and the new read-only `/memory cache` summary output

That means the current branch has already closed the phase-1 memory MVP plus
the narrow truth-source / cache-observability precondition milestone.

## Deferred Work Extracted From OpenSpec

The following items are explicitly outside the current branch scope and should
be treated as follow-up work rather than unfinished implementation bugs.

### 1. Durable Memory Governance

Not yet delivered:

- governed auto-promotion from session logs into durable memory
- promotion precision metrics
- promotion pollution metrics
- explainable promotion thresholds and operator-facing promotion decisions
- rollback / correction workflows for bad promotions

Why it matters:

- the current branch captures enough session-log and `llm_calls` truth data to
  support governance work, but does not yet decide when memory should become
  durable by policy
- current auto-promotion behavior is intentionally limited and not yet a full
  governed memory product

### 2. Typed Durable-Memory Taxonomy

Not yet delivered:

- richer durable-memory types beyond workspace instruction text
- explicit durable-memory classes such as preference / constraint / workflow /
  project-fact / reference-like memory
- retrieval policies that differ by durable-memory type
- storage and prompt-injection rules that vary by type

Why it matters:

- current memory is still effective, but coarse
- better taxonomy is required before high-confidence auto-promotion can safely
  expand

### 3. Instruction Surface Expansion

Not yet delivered:

- `AWORLD.local.md`
- `W/.aworld/rules/*.md`
- multi-file precedence and merge rules for local/private instruction layers
- guided or automatic migration from compatibility file shapes into richer
  instruction surfaces

Why it matters:

- phase 1 intentionally kept the instruction surface small to reduce merge and
  explainability complexity
- richer instruction layering is useful, but should land only after precedence
  rules are explicit

### 4. Richer Memory Product UX

Not yet delivered:

- optional memory HUD or richer plugin-local workflow state
- operator-friendly inspection of promotion candidates before acceptance
- explainability views for "why was this recalled" or "why was this promoted"
- richer review / prune / expire flows for durable and session-log memory

Why it matters:

- the branch now has strong observability and storage primitives
- product-grade operator control still needs dedicated UI/UX work

Current evaluation result:

- no additional memory-specific HUD surface is required for the current branch
- current HUD already exposes live session / task / token / context state and
  request-linked usage observability
- current `/memory status`, `/memory promotions`, and `/memory cache` surfaces
  already cover governance state, review actions, and cache analysis without
  introducing another control plane
- richer plugin-local workflow state should stay deferred until there is a
  concrete need for interactive candidate queues, explainability drill-down, or
  durable-memory prune / expire workflows

Decision for now:

- treat memory HUD or plugin-local workflow state as a later UX follow-up, not
  a blocker for the governed-promotion milestone
- prefer extending existing `/memory` command surfaces before inventing a new
  plugin-local state model
- revisit only after typed durable-memory taxonomy and explainability UX needs
  become concrete

### 5. Prompt Cache Strategy Beyond Observability

Not yet delivered:

- prompt-cache optimization policy
- provider-specific cache strategy
- request-shape normalization specifically to maximize cache reuse
- prompt-prefix stabilization logic that mutates prompt building
- cache-aware prompt planning or cache-hit targeting

Why it matters:

- the current branch only provides a safe read-only basis for analysis
- it intentionally does **not** mutate runtime behavior in pursuit of cache hits
- any future cache optimization should be driven by the new observability data,
  not by speculative prompt rewrites

### 6. Broader Runtime-Memory Rewrite

Not yet delivered:

- replacing `AworldMemory` as the runtime message-memory engine
- broad rewrite of summary generation
- broad rewrite of semantic message reconstruction beyond the
  `llm_calls` preference/fallback rule
- unifying all memory shapes into one storage model

Why it matters:

- the current architecture intentionally keeps runtime message memory stable
- broader rewrites should only happen with separate justification and isolated
  risk management

### 7. Promotion Scheduling / Background Jobs / Sync

Not yet delivered:

- timed promotion
- background batch rewriting of primary memory files
- team-memory synchronization protocols
- cross-workspace or repo-identity-backed shared durable memory

Why it matters:

- these are larger product and governance changes, not phase-1 extensions

## Recommended Next Change Split

The backlog should not be implemented as one large continuation of the current
change. The smallest clean split is:

### Change A: Governed Session-Log Promotion

Recommended scope:

- define promotion candidate review rules
- define explicit thresholds for promote / keep-in-session-log / reject
- add promotion quality metrics
- add operator-visible promotion explanations
- preserve current session-log-first safety until governance is proven

Why first:

- it builds directly on the session-log and candidate pipeline already in place
- it improves memory quality without touching trajectory or provider behavior

### Change B: Typed Durable-Memory Taxonomy

Recommended scope:

- define a durable-memory type system
- define storage / retrieval / prompt-injection semantics by type
- add tests proving type-specific recall behavior

Why second:

- governance quality depends on taxonomy quality
- promotion without better types is more likely to pollute durable memory

### Change C: Memory Explainability And Review UX

Recommended scope:

- `/memory` subcommands or plugin state for reviewing recalled items
- "why recalled" and "why promoted" explanations
- durable-memory inspection / pruning workflows

Why third:

- once governance exists, operators need a good control plane
- this change should consume the outputs of A and B rather than invent its own
  state model first

### Change D: Cache-Aware Prompt Analysis To Action Loop

Recommended scope:

- continue from read-only `/memory cache`
- turn repeated-prefix observations into explicit optimization candidates
- define safe experiments for prompt-prefix stabilization
- compare cache-hit opportunities without changing trajectory semantics

Why fourth:

- the current branch already completed the precondition milestone
- this work should remain analytically driven and must not regress fidelity

### Change E: Instruction Surface Expansion

Recommended scope:

- `AWORLD.local.md`
- `rules/*.md`
- precedence and merge semantics
- migration guidance

Why later:

- it is useful, but not the highest-risk or highest-value next step
- governance and explainability should be clearer first

## Priority Recommendation

If only one follow-up thread should be opened next, the recommended order is:

1. governed session-log promotion
2. typed durable-memory taxonomy
3. explainability / review UX
4. cache-aware prompt optimization follow-up
5. instruction-surface expansion

## Suggested Next Minimal Thread

The cleanest next thread is:

`governed session-log promotion for workspace durable memory`

That thread should use the already-delivered session logs, promotion metrics,
and `llm_calls` truth data, but it should still avoid:

- changing trajectory semantics
- changing provider cache strategy
- rewriting runtime message memory

## Out Of Scope For This Backlog Document

This document does not redefine the current change scope, reopen already closed
phase-1 tasks, or propose a broad memory rewrite.

It is a backlog organization artifact intended to make the next OpenSpec split
cleaner.
