# Self-Evolve Member Replay And Runtime Candidate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute paired replay independently for every trajectory-set member and keep self-evolve internal context out of candidate skill runtime instructions.

**Architecture:** Extend replay results with member-level records while retaining the existing top-level aggregate for gates and single-case compatibility. Materialize candidate skill changes from normalized runtime behavior rules only; keep trace and evaluation context in existing lesson and lineage artifacts.

**Tech Stack:** Python dataclasses, asyncio, filesystem replay artifacts, pytest.

---

### Task 1: Member-Aware Replay Contract

**Files:**
- Modify: `aworld/self_evolve/replay.py`
- Test: `tests/self_evolve/test_replay_overlay.py`

- [x] Add a failing test proving two dataset members execute separate baseline/candidate requests in separate artifact directories.
- [x] Add a failing test proving paired evaluation cases receive only their own member trajectories.
- [x] Add a failing test proving a failed member is identified by `case_id` and cannot be masked by another successful member.
- [x] Add member replay result records, per-member request derivation, aggregate replay metrics, and member-specific paired dataset construction.
- [x] Run `pytest tests/self_evolve/test_replay_overlay.py -q`.

### Task 2: Stored Multi-Member Replay

**Files:**
- Modify: `aworld/self_evolve/replay.py`
- Test: `tests/self_evolve/test_replay_overlay.py`

- [x] Add a failing test that reloads a multi-member replay directory and preserves member mapping and repetition trajectories.
- [x] Persist a versioned member manifest and load task-scoped member results while preserving the legacy single-case layout.
- [x] Run the stored replay tests and the full replay overlay test module.

### Task 3: Runtime-Only Candidate Materialization

**Files:**
- Modify: `aworld/self_evolve/runner.py`
- Modify: `tests/self_evolve/test_runner.py`
- Modify: `tests/skills/test_builtin_self_evolve_skill.py` if its contract asserts internal candidate text.

- [x] Replace existing expectations for trace ids and validation summaries in candidate bodies with assertions for bounded runtime rules and absence of internal fields.
- [x] Run focused tests and verify they fail against the current `Self-Evolve Trace Guidance` implementation.
- [x] Build runtime rules from normalized repair-plan actions, cap their count, and return unchanged content when no actionable rule exists.
- [x] Keep optimizer prompts, lessons, and lineage unchanged so internal provenance remains available to self-improvements.
- [x] Run focused runner and built-in skill tests.

### Task 4: Integration Verification

**Files:**
- Modify only files required by failures found in the scoped test suites.

- [x] Run `pytest tests/self_evolve/test_replay_overlay.py tests/self_evolve/test_evaluation_backend.py -q`.
- [x] Run focused runner tests for replay, candidate generation, lessons, and auto-verified apply.
- [x] Run `pytest tests/skills/test_builtin_self_evolve_skill.py -q`.
- [x] Run `git diff --check` and inspect the final diff for unrelated changes.
