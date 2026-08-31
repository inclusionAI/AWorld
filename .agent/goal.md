# Project Goal

## Problem Statement

AWorld assembles context, captures provider requests, stores Tool output, finalizes trajectories, manages cache identity,
and delegates work through several partially independent paths. The current system cannot consistently prove that the
Context seen by the provider matches the compiled intent, that trajectory persistence completed before task finalization,
or that Context optimizations improve framework capability rather than a benchmark-specific score.

## Desired Outcome

Implement the unified Context Management Harness described in
`docs/superpowers/specs/2026-08-17-unified-context-management-harness-design.md`. All AWorld entry points must share a
versioned Context compilation/control plane, provider-bound request truth, reversible Tool output offload, deterministic
trajectory finalization, and a benchmark-independent evaluation loop.

## Acceptance Criteria

- [ ] Every provider call has one immutable provider-bound request snapshot and an explainable compiler trace.
- [ ] Context compilation enforces scope, authority, trust, budgets, Tool pairing, and deterministic cache identity.
- [ ] `off`, `observe`, `shadow`, and `enforce` modes preserve legacy compatibility and have deterministic tests.
- [ ] Every task emits a typed `TrajectoryBuildResult`; finalize leaves no untracked trajectory update.
- [ ] JSONL v2 trajectory output carries fidelity, counts, revision, checksum, and dual-read/write compatibility.
- [ ] Tool output is bounded before entering model Context, fully retained as a checksummed artifact, and retrievable.
- [ ] CLI, normal Agent, Amni, resume/steering, and subagent paths use equivalent core Context semantics.
- [ ] Evaluation manifests pair Context-only variants while freezing model, prompt, tools, environment, and verifier.
- [ ] Framework benefit claims require request-level effects, independent outcomes, hard gates, statistics, and a second
      non-Terminal-Bench workload.

## Non-Goals

- Task-specific Terminal Bench prompts, answers, routing, verifier branches, or score tuning.
- Modifying mcpgateway, lingguang-bench-runtime-dsh, Harbor, or benchmark task images.
- Replacing `llm_calls`, runtime events, `TrajectoryDataset`, or TaskResponse with a competing semantic truth source.
- Changing business task success solely because an observability/export operation failed.
- Requiring every entry point to migrate atomically; compatibility adapters and rollout modes are mandatory.

## Constraints

- Preserve existing public APIs unless the spec defines an additive compatibility field.
- No provider secret or raw sensitive Tool output in default logs, traces, fixtures, or committed evidence.
- Core policy remains provider-neutral; provider-specific hints belong in lowering adapters.
- Context/trajectory behavior must be deterministic under fixed inputs and configuration.
- The benchmark is an interchangeable validation adapter, never the optimization target.

## Tech Stack

- Python 3.10+
- asyncio event runner and AWorld Context/Memory/Agent architecture
- Pydantic configuration/models
- pytest, including optional real-Docker integration gates
- JSON/JSONL artifacts and MCP Tool servers

