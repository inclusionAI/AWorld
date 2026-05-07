# Proposal

## Why

`aworld-cli` currently has memory-adjacent behavior, but not a plugin-backed,
workspace-scoped durable-memory experience:

- shared runtime memory already stores message history, summaries, and
  structured long-term memory
- CLI workspace context is still effectively a single discovered `AWORLD.md`
  injected into the prompt

That leaves several gaps:

- no layered instruction memory under `AWORLD.md / .aworld/`
- no built-in memory plugin with commands and hooks
- no workspace-scoped session-log memory path
- no relevant-memory recall flow for `aworld-cli`
- no durable-memory governance layer for typed memory, promotion, and quality
  metrics

At the same time, AWorld already depends on the current runtime memory contract
for message history, summary generation, tool-call pairing, and long-term
extraction. Replacing that runtime wholesale would be high-risk and is not
required to improve CLI memory.

The desired direction is therefore:

- upgrade `aworld-cli` memory to a layered, Claude Code-like experience
- preserve `AWORLD.md / .aworld/` naming and product language
- keep existing runtime message-memory semantics intact
- introduce a hybrid provider seam so CLI durable memory can be injected into
  the shared stack without a broad `aworld/core` memory rewrite

## Current Branch Scope

This branch is scoped to the phase-1 memory MVP, not the full governed memory
product originally discussed.

This change set also includes one narrow extension to that MVP: `llm_calls`
becomes the append-only truth source for real model requests so session logs,
trajectory generation, and usage observability can rely on final provider-bound
request snapshots without changing runtime memory semantics.

Delivered in this change:

- hybrid provider seam and CLI bootstrap wiring
- layered workspace instruction loading under `AWORLD.md / .aworld/`
- built-in `memory` plugin surface
- explicit durable writes through `/memory` and `/remember`
- workspace-scoped append-only session logs
- selective relevant recall from session logs
- append-only `llm_calls` persistence for model calls within the existing
  memory/session-log path
- final request snapshot preservation, including internal `request_id` and
  provider `request_id` when available
- normalized usage plus raw provider usage preservation for request-linked
  observability
- trajectory preference for `llm_calls[*].request.messages` before any memory
  reconstruction fallback
- explicit exclusion of cache usage from trajectory semantic message content

Deferred beyond this branch:

- typed durable-memory taxonomy beyond plain workspace instruction text
- governed auto-promotion from session logs into durable memory
- promotion precision / pollution metrics
- optional memory HUD or richer plugin-local workflow state
- full prompt-cache redesign or provider-specific cache optimization strategy
- any broad rewrite of runtime memory or message reconstruction semantics

## What Changes

- Add a built-in `memory` plugin for `aworld-cli` instead of keeping memory
  behavior spread across ad hoc console logic.
- Define a phase-1 layered instruction-memory model rooted in AWorld naming
  for the active workspace root `W`:
  - `~/.aworld/AWORLD.md`
  - `W/.aworld/AWORLD.md`
  - `W/AWORLD.md` as compatibility read only
- Add a workspace-scoped durable session-log area for capture and recall.
- Introduce a hybrid provider architecture:
  - existing `AworldMemory` remains the runtime message-memory provider
  - a new CLI durable-memory provider manages layered instruction memory,
    canonical workspace writes, durable logs, and recall
  - a hybrid adapter composes both without changing the meaning of current
    runtime message-memory flows
- Extend the same append-only durable path with `llm_calls` records captured at
  the model boundary, preserving request snapshots, request identifiers, and
  request-linked usage observability for downstream logs and trajectories.
- Replace the current single-file `AWORLDFileNeuron` loading behavior with a
  layered instruction-memory loading path backed by the new durable provider.
- Define a first-phase durable update model:
  - explicit writes (`/memory`, `/remember`) promote immediately
  - turn-end extraction always writes session logs
  - automatic promotion is deferred beyond this branch
  - no timed promotion and no background batch rewriting of main memory files
  - no `AWORLD.local.md` or `rules/*.md` surfaces in phase 1

## Outcome

After this change, `aworld-cli` will have a memory-plugin MVP that is
materially closer to Claude Code while remaining idiomatic to AWorld:

- memory is a built-in plugin capability, not a console special case
- workspace context evolves from one `AWORLD.md` file into layered instruction
  memory
- session observations can accumulate into workspace session logs without
  polluting runtime message memory
- model-call truth data can accumulate as append-only `llm_calls` records
  without redefining runtime memory semantics
- selective recall can pull useful session memories into later prompts without
  replaying everything
- higher-fidelity trajectories can prefer preserved request snapshots while
  keeping cache usage as observability metadata only
- runtime memory behavior remains stable for existing agent execution paths
- the durable-memory provider becomes a reusable seam for future features such
  as typed durable memory, governed promotion, `/dream`, sync, and richer
  recall
