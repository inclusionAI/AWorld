# Skill-Owned Replay Capability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend self-evolve so a multi-file skill candidate can provide a dynamically discovered replay capability whose deterministic frozen output is shared by isolated baseline and candidate rollouts, without modifying the current `agent-browser` skill or adding Browser/CDP semantics to the framework.

**Architecture:** Add a backward-compatible candidate package representation, reconstruct generic trajectory context before compression, expose unresolved replay requirements to the mutator, and discover a versioned `replay/capability.json` from the materialized candidate. Execute the capability through a bounded JSON subprocess protocol twice, freeze matching output, then materialize identical fixtures and service specifications into independent baseline/candidate repetitions. Publish the complete skill package only after existing and new gates pass.

**Tech Stack:** Python 3 dataclasses, pathlib, JSON, asyncio/subprocess, pytest, existing AWorld self-evolve runner/store/replay infrastructure.

---

## File Structure

- Create `aworld/self_evolve/candidate_package.py`: candidate file validation, canonical package fingerprints, and safe materialization.
- Create `aworld/self_evolve/trajectory_context.py`: generic trajectory record context snapshots and continuation reconstruction.
- Create `aworld/self_evolve/replay_capability.py`: manifest discovery, subprocess protocol, deterministic compilation, frozen bundle models, and validation.
- Modify `aworld/self_evolve/types.py`: add `CandidateFileDelta` and extend `CandidateVariant` compatibly.
- Modify `aworld/self_evolve/trace_pack.py`: expose parsed trajectory-log records without discarding record order or raw steps.
- Modify `aworld/self_evolve/datasets.py`: attach generic context snapshots to evaluation cases.
- Modify `aworld/self_evolve/optimizers/base.py`: carry replay requirements and target package inventory in optimizer requests.
- Modify `aworld/self_evolve/optimizers/llm_mutator.py`: accept structured multi-file mutator output and fingerprint the full package.
- Modify `aworld/self_evolve/replay_adaptation.py`: expose preflight requirements and merge frozen skill capability bindings into the generic adaptation bundle.
- Modify `aworld/self_evolve/overlay.py`: copy the existing target package and apply candidate file deltas safely.
- Modify `aworld/self_evolve/replay.py`: start and stop frozen replay services per repetition and record service provenance.
- Modify `aworld/self_evolve/store.py`: persist multi-file candidates and frozen capability artifacts.
- Modify `aworld/self_evolve/targets.py`: stage, apply, verify, and roll back complete skill packages.
- Modify `aworld/self_evolve/runner.py`: run preflight before mutation, compile candidate capability after overlay creation, use candidate-specific adaptation cache keys, and publish package candidates.
- Modify `aworld/self_evolve/gates.py`: validate candidate package shape, size, and capability closure.
- Modify `aworld/self_evolve/__init__.py`: export the public candidate and replay capability types.
- Add focused tests under `tests/self_evolve/` for every new boundary.

### Task 1: Backward-Compatible Multi-File Candidate Packages

**Files:**
- Create: `aworld/self_evolve/candidate_package.py`
- Modify: `aworld/self_evolve/types.py`
- Modify: `aworld/self_evolve/optimizers/llm_mutator.py`
- Modify: `aworld/self_evolve/gates.py`
- Test: `tests/self_evolve/test_candidate_package.py`
- Test: `tests/self_evolve/test_optimizer_contract.py`

- [ ] **Step 1: Write failing candidate package tests**

```python
def test_text_only_candidate_keeps_legacy_shape():
    candidate = CandidateVariant("c1", TARGET, SKILL, "reason")
    assert candidate.files == ()


def test_candidate_package_rejects_path_escape():
    candidate = CandidateVariant(
        "c1", TARGET, SKILL, "reason",
        files=(CandidateFileDelta(path="../escape.py", content="bad"),),
    )
    with pytest.raises(ValueError, match="inside replay"):
        validate_candidate_files(candidate.files)


def test_package_fingerprint_includes_replay_files():
    first = CandidateVariant(
        "c1", TARGET, SKILL, "reason",
        files=(CandidateFileDelta(path="replay/capability.json", content="{}"),),
    )
    second = replace(first, files=(CandidateFileDelta(
        path="replay/capability.json", content='{"v": 1}'
    ),))
    assert candidate_package_fingerprint(first) != candidate_package_fingerprint(second)
```

- [ ] **Step 2: Run tests and verify the missing-type failures**

Run: `pytest -q tests/self_evolve/test_candidate_package.py tests/self_evolve/test_optimizer_contract.py`

Expected: FAIL because `CandidateFileDelta`, `files`, and package helpers do not exist.

- [ ] **Step 3: Implement the minimal package types and validation**

```python
@dataclass(frozen=True)
class CandidateFileDelta:
    path: str
    operation: str = "upsert"
    content: str | None = None
    executable: bool = False


@dataclass(frozen=True)
class CandidateVariant:
    candidate_id: str
    target: SelfEvolveTargetRef
    content: str
    rationale: str
    parent_candidate_ids: tuple[str, ...] = ()
    target_fingerprint: str | None = None
    files: tuple[CandidateFileDelta, ...] = ()
```

Implement `validate_candidate_files()`, `candidate_package_payload()`, and
`candidate_package_fingerprint()` using normalized POSIX paths, `replay/` containment,
duplicate-path rejection, `upsert|delete` validation, UTF-8 byte limits, and sorted
canonical JSON.

- [ ] **Step 4: Extend mutator parsing and package gates**

Parse a structured mutator result with:

```python
raw_files = output.get("files", ()) if isinstance(output, Mapping) else ()
files = tuple(CandidateFileDelta(
    path=str(item.get("path") or ""),
    operation=str(item.get("operation") or "upsert"),
    content=item.get("content") if isinstance(item.get("content"), str) else None,
    executable=bool(item.get("executable", False)),
) for item in raw_files if isinstance(item, Mapping))
validate_candidate_files(files)
```

Use the package fingerprint for candidate ids, duplicate filtering, lineage content
fingerprints, no-op detection, and size gates. A candidate changes the target when
either Markdown or file deltas change it.

- [ ] **Step 5: Run focused tests and commit**

Run: `pytest -q tests/self_evolve/test_candidate_package.py tests/self_evolve/test_optimizer_contract.py tests/self_evolve/test_gates.py`

Expected: PASS.

Commit: `git commit -m "feat: represent multi-file skill candidates"`

### Task 2: Generic Trajectory Context Reconstruction

**Files:**
- Create: `aworld/self_evolve/trajectory_context.py`
- Modify: `aworld/self_evolve/trace_pack.py`
- Modify: `aworld/self_evolve/datasets.py`
- Test: `tests/self_evolve/test_trajectory_context.py`
- Test: `tests/self_evolve/test_datasets.py`

- [ ] **Step 1: Write failing context reconstruction tests**

```python
def test_context_prefers_same_session_predecessor():
    records = (
        record("first", "session-a", "Start", "Finished"),
        record("next", "session-a", "Continue the current task", "Done"),
    )
    snapshots = build_trajectory_context_snapshots(records)
    assert snapshots[1].link_strategy == "same_session_predecessor"
    assert snapshots[1].prior_turns[-1].content == "Finished"


def test_context_uses_adjacent_fallback_only_for_explicit_continuation():
    records = (
        record("first", "session-a", "Start", "Finished"),
        record("next", "session-b", "Continue the current task", "Done"),
    )
    snapshots = build_trajectory_context_snapshots(records)
    assert snapshots[1].link_strategy == "adjacent_record_fallback"


def test_unrelated_adjacent_record_is_not_joined():
    records = (
        record("first", "session-a", "Start", "Finished"),
        record("next", "session-b", "New independent task", "Done"),
    )
    assert build_trajectory_context_snapshots(records)[1].prior_turns == ()
```

- [ ] **Step 2: Run tests and verify missing API failures**

Run: `pytest -q tests/self_evolve/test_trajectory_context.py tests/self_evolve/test_datasets.py`

Expected: FAIL because trajectory records and context snapshots are not represented.

- [ ] **Step 3: Expose trajectory log records and build snapshots**

Add immutable `TrajectoryLogRecord`, `TrajectoryContextTurn`, and
`TrajectoryContextSnapshot` dataclasses. `load_trajectory_log_records()` must preserve
record order, record metadata, and the complete tuple of mapping steps.

Implement precedence as:

```python
if explicit_parent_id in records_by_task:
    predecessor, strategy = records_by_task[explicit_parent_id], "explicit_parent"
elif recorded_prior_turns:
    predecessor, strategy = None, "recorded_message_history"
elif session_id and session_id in latest_by_session:
    predecessor, strategy = latest_by_session[session_id], "same_session_predecessor"
elif is_continuation(task_input) and index > 0:
    predecessor, strategy = records[index - 1], "adjacent_record_fallback"
else:
    predecessor, strategy = None, None
```

Sanitize and bound each stored value, record omissions, and fingerprint the canonical
snapshot payload.

- [ ] **Step 4: Attach snapshots to trajectory-backed `EvalCase` values**

Add `context_snapshot: TrajectoryContextSnapshot | None = None` to `EvalCase`. Build
trace packs and snapshots from the same parsed record sequence. For a reconstructed
continuation, prepend a bounded prior-turn envelope to the original input content
without changing unrelated cases. Include the snapshot fingerprint in dataset and
replay dataset fingerprints.

- [ ] **Step 5: Run focused tests and commit**

Run: `pytest -q tests/self_evolve/test_trajectory_context.py tests/self_evolve/test_trace_pack.py tests/self_evolve/test_datasets.py`

Expected: PASS.

Commit: `git commit -m "feat: reconstruct generic trajectory context"`

### Task 3: Replay Requirement Preflight And Mutation Contract

**Files:**
- Modify: `aworld/self_evolve/replay_adaptation.py`
- Modify: `aworld/self_evolve/optimizers/base.py`
- Modify: `aworld/self_evolve/optimizers/llm_mutator.py`
- Modify: `aworld/self_evolve/runner.py`
- Modify: `aworld/self_evolve/store.py`
- Test: `tests/self_evolve/test_replay_adaptation.py`
- Test: `tests/self_evolve/test_optimizer_contract.py`
- Test: `tests/self_evolve/test_runner.py`

- [ ] **Step 1: Write failing preflight tests**

```python
def test_preflight_returns_unresolved_requirements_without_failing(tmp_path):
    report = ReplayAdaptationCompiler().preflight(dataset=DATASET, workspace_root=tmp_path)
    assert {item.kind for item in report.requirements} == {"local_endpoint"}
    assert report.requirements[0].case_ids == ("case-1",)


def test_optimizer_prompt_contains_replay_requirements():
    request = replace(REQUEST, replay_requirements=(REQUIREMENT,))
    prompt = _build_mutation_prompt(request, candidate_index=0)
    assert '"replay_requirements"' in prompt
    assert REQUIREMENT.requirement_id in prompt
```

- [ ] **Step 2: Run tests and verify missing preflight failures**

Run: `pytest -q tests/self_evolve/test_replay_adaptation.py tests/self_evolve/test_optimizer_contract.py`

Expected: FAIL because preflight and optimizer requirement fields do not exist.

- [ ] **Step 3: Implement generic requirements**

Add `ReplayCapabilityRequirement` and `ReplayPreflightReport`. Refactor dependency
detection so `preflight()` returns requirements without copying a workspace or
requiring adapters, while `compile()` consumes the same detector. Requirement ids are
stable hashes of kind and normalized identifier, and each requirement carries case
ids plus snapshot evidence refs.

- [ ] **Step 4: Feed preflight into candidate generation**

Add these backward-compatible optimizer request fields:

```python
replay_requirements: tuple[ReplayCapabilityRequirement, ...] = ()
target_package_inventory: tuple[str, ...] = ()
```

Run preflight before `optimizer.propose()`, persist `replay_requirements.json`, and add
the sanitized requirements plus the `files` output schema to the mutation prompt.
Do not generate domain-specific files in `_default_cli_skill_candidate`; it remains a
text-only compatible mutator.

- [ ] **Step 5: Run focused tests and commit**

Run: `pytest -q tests/self_evolve/test_replay_adaptation.py tests/self_evolve/test_optimizer_contract.py tests/self_evolve/test_runner.py`

Expected: PASS.

Commit: `git commit -m "feat: expose replay requirements to skill mutation"`

### Task 4: Skill-Owned Capability Discovery And Deterministic Compilation

**Files:**
- Create: `aworld/self_evolve/replay_capability.py`
- Modify: `aworld/self_evolve/__init__.py`
- Test: `tests/self_evolve/test_replay_capability.py`

- [ ] **Step 1: Write failing discovery and protocol tests**

```python
def test_discover_capability_inside_skill_root(tmp_path):
    skill = write_capability_skill(tmp_path)
    discovered = discover_replay_capability(skill)
    assert discovered.manifest.capability_id == "fixture-service"
    assert discovered.entrypoint == skill / "replay/compiler.py"


def test_discovery_rejects_entrypoint_escape(tmp_path):
    skill = write_capability_skill(tmp_path, entrypoint="../outside.py")
    with pytest.raises(ReplayCapabilityError, match="inside skill root"):
        discover_replay_capability(skill)


def test_double_compile_rejects_different_fixture_hashes(tmp_path):
    capability = write_nondeterministic_capability(tmp_path)
    with pytest.raises(ReplayCapabilityError, match="non-deterministic"):
        compile_and_freeze_capability(capability, REQUEST, tmp_path / "out")
```

- [ ] **Step 2: Run tests and verify missing-module failures**

Run: `pytest -q tests/self_evolve/test_replay_capability.py`

Expected: FAIL because the capability module does not exist.

- [ ] **Step 3: Implement manifest discovery and process execution**

Define `ReplayCapabilityManifest`, `DiscoveredReplayCapability`,
`ReplayCapabilityCompileRequest`, `ReplayServiceSpec`,
`ReplayCapabilityCompileResult`, `FrozenReplayCapability`, and
`ReplayCapabilityExecutor`.

The default executor must call an argv list without a shell, use a minimal environment,
set `cwd` to an isolated compiler directory, bound timeout/stdout/stderr, and require
the compiler result at a declared JSON output path.

- [ ] **Step 4: Implement validation, double compilation, and freezing**

Run the executor twice with distinct clean output roots. Validate exact requirement
ids, evidence refs, endpoint replacements, fixture containment, service argv paths,
and reserved environment keys. Compare canonical manifests plus SHA-256 fixture and
runtime file manifests. Copy one matching result to `frozen/` and write a frozen
manifest whose fingerprint includes capability package and request fingerprints.

- [ ] **Step 5: Run focused tests and commit**

Run: `pytest -q tests/self_evolve/test_replay_capability.py`

Expected: PASS.

Commit: `git commit -m "feat: compile skill-owned replay capabilities"`

### Task 5: Candidate Overlay And Candidate-Specific Adaptation

**Files:**
- Modify: `aworld/self_evolve/overlay.py`
- Modify: `aworld/self_evolve/replay_adaptation.py`
- Modify: `aworld/self_evolve/runner.py`
- Modify: `aworld/self_evolve/provenance.py`
- Test: `tests/self_evolve/test_replay_overlay.py`
- Test: `tests/self_evolve/test_replay_adaptation.py`
- Test: `tests/self_evolve/test_runner.py`

- [ ] **Step 1: Write failing overlay and shared-freeze tests**

```python
def test_overlay_copies_target_assets_and_applies_candidate_files(tmp_path):
    skill_root = tmp_path / "skills"
    target_path = make_skill(
        skill_root,
        name="fixture-skill",
        files={"scripts/keep.py": "keep"},
    )
    candidate = candidate_with_capability()
    overlay = create_candidate_skill_overlay(
        workspace_root=tmp_path,
        run_id="run-1",
        candidate=candidate,
        target_skill_path=target_path / "SKILL.md",
        baseline_skill_roots=(skill_root,),
    )
    assert (overlay.candidate_skill_path.parent / "scripts/keep.py").read_text() == "keep"
    assert (overlay.candidate_skill_path.parent / "replay/capability.json").is_file()


async def test_runner_freezes_candidate_capability_before_paired_replay(tmp_path):
    result = await run_with_recording_capability(tmp_path)
    request = result.replay_result.request
    assert request.replay_adaptation.capability_fingerprint is not None
    assert request.replay_adaptation.ready is True
```

- [ ] **Step 2: Run tests and verify current overlay/adaptation failures**

Run: `pytest -q tests/self_evolve/test_replay_overlay.py tests/self_evolve/test_replay_adaptation.py tests/self_evolve/test_runner.py`

Expected: FAIL because the overlay drops target assets and adaptation occurs before
candidate capability discovery.

- [ ] **Step 3: Materialize a complete candidate target package**

Copy the current target skill directory into the overlay target directory, replace
`SKILL.md`, apply validated file deltas, and persist package fingerprints in
`overlay.json`. Never write into the baseline target directory.

- [ ] **Step 4: Reorder replay preparation and key caches by capability**

Create the candidate overlay before final adaptation. Discover the candidate
capability, compile and freeze it, and pass the frozen result into
`ReplayAdaptationCompiler.compile()`. Extend bundle and replay provenance with
context, requirement, capability package, frozen capability, runtime, and fixture
fingerprints. Cache by `(run_id, dataset_fingerprint, candidate_package_fingerprint)`.

Keep constructor-injected adapters as a trusted compatibility path, but never
register Browser/CDP code in the framework.

- [ ] **Step 5: Run focused tests and commit**

Run: `pytest -q tests/self_evolve/test_replay_overlay.py tests/self_evolve/test_replay_adaptation.py tests/self_evolve/test_runner.py`

Expected: PASS.

Commit: `git commit -m "feat: bind candidate replay capabilities"`

### Task 6: Frozen Replay Service Lifecycle Per Repetition

**Files:**
- Modify: `aworld/self_evolve/replay.py`
- Modify: `aworld/self_evolve/replay_adaptation.py`
- Test: `tests/self_evolve/test_replay_capability.py`
- Test: `tests/self_evolve/test_replay_overlay.py`

- [ ] **Step 1: Write failing service lifecycle tests**

```python
async def test_each_variant_gets_separate_service_from_same_frozen_runtime(tmp_path):
    result = await replay_with_fixture_service(tmp_path)
    assert result.baseline.metrics["service_runtime_fingerprint"] == result.candidate.metrics["service_runtime_fingerprint"]
    assert result.baseline.metrics["service_endpoint"] != result.candidate.metrics["service_endpoint"]


async def test_service_is_stopped_after_executor_failure(tmp_path):
    pid_file = tmp_path / "pid"
    await replay_with_failing_executor(tmp_path, pid_file=pid_file)
    assert_process_not_running(int(pid_file.read_text()))
```

- [ ] **Step 2: Run tests and verify missing lifecycle failures**

Run: `pytest -q tests/self_evolve/test_replay_capability.py tests/self_evolve/test_replay_overlay.py`

Expected: FAIL because replay does not start frozen services.

- [ ] **Step 3: Implement service materialization and readiness**

For every repetition, copy frozen files into that repetition workspace, allocate a
loopback port for each logical service, expand only reserved service placeholders,
start argv without a shell, and wait for bounded TCP or HTTP readiness. Rewrite only
the declared dependency identifiers in task input.

- [ ] **Step 4: Guarantee cleanup and comparability provenance**

Wrap executor invocation in `try/finally`. Terminate, wait, then kill on timeout.
Record logical service ids, endpoints, runtime/fixture fingerprints, startup status,
and cleanup status. Comparability requires equal logical ids and fingerprints but
distinct workspace paths and service endpoints.

- [ ] **Step 5: Run focused tests and commit**

Run: `pytest -q tests/self_evolve/test_replay_capability.py tests/self_evolve/test_replay_overlay.py tests/self_evolve/test_replay_adaptation.py`

Expected: PASS.

Commit: `git commit -m "feat: isolate frozen replay services"`

### Task 7: Multi-File Proposal Persistence, Apply, And Rollback

**Files:**
- Modify: `aworld/self_evolve/store.py`
- Modify: `aworld/self_evolve/targets.py`
- Modify: `aworld/self_evolve/runner.py`
- Modify: `aworld/skills/release.py`
- Test: `tests/self_evolve/test_store.py`
- Test: `tests/self_evolve/test_targets.py`
- Test: `tests/self_evolve/test_release_checks.py`
- Test: `tests/self_evolve/test_runner.py`

- [ ] **Step 1: Write failing persistence and rollback tests**

```python
def test_write_candidate_persists_package_without_touching_target(tmp_path):
    target = make_skill(tmp_path)
    stored = store.write_candidate("run", candidate_with_capability())
    assert (stored / "SKILL.md").is_file()
    assert (stored / "replay/capability.json").is_file()
    assert not (target / "replay").exists()


def test_package_apply_failure_restores_complete_skill_directory(tmp_path):
    target = make_skill(tmp_path, files={"scripts/original.py": "original"})
    target.apply_candidate_package(candidate_with_capability())
    target.rollback()
    assert (target.path.parent / "scripts/original.py").read_text() == "original"
    assert not (target.path.parent / "replay").exists()
```

- [ ] **Step 2: Run tests and verify single-file persistence failures**

Run: `pytest -q tests/self_evolve/test_store.py tests/self_evolve/test_targets.py tests/self_evolve/test_runner.py`

Expected: FAIL because store and target adapters only persist/apply Markdown.

- [ ] **Step 3: Persist candidate directories and package backups**

Store candidates at `candidates/<candidate-id>/` with `SKILL.md`, validated replay
files, and `candidate.json`. Keep loading compatibility for existing
`candidates/<candidate-id>.json`. Write full target-directory backups and package
fingerprints into the apply journal.

- [ ] **Step 4: Stage, apply, verify, and roll back packages**

Normalize candidate Markdown, materialize the candidate package in a sibling staging
directory, verify its canonical fingerprint and registry execution-asset digest,
then replace the target package. On any later failure, restore the complete backup.
Post-apply evaluation checks both content and package fingerprints.

- [ ] **Step 5: Run focused tests and commit**

Run: `pytest -q tests/self_evolve/test_store.py tests/self_evolve/test_targets.py tests/self_evolve/test_release_checks.py tests/self_evolve/test_runner.py`

Expected: PASS.

Commit: `git commit -m "feat: publish verified skill packages atomically"`

### Task 8: End-To-End Integration, Reporting, And Regression Verification

**Files:**
- Modify: `aworld/self_evolve/runner.py`
- Modify: `aworld/self_evolve/replay.py`
- Modify: `aworld/self_evolve/store.py`
- Modify: `aworld/self_evolve/__init__.py`
- Test: `tests/self_evolve/test_skill_owned_replay_integration.py`
- Test: `tests/self_evolve/test_provenance.py`
- Test: `tests/self_evolve/test_runner.py`

- [ ] **Step 1: Write a failing domain-neutral integration test**

Create a synthetic skill candidate whose `replay/compiler.py` converts a recorded
generic endpoint response into a frozen loopback fixture service. Assert:

```python
assert current_skill_dir_has_no_replay_files()
assert result.request.replay_adaptation.ready
assert baseline.metrics["frozen_capability_fingerprint"] == candidate.metrics["frozen_capability_fingerprint"]
assert baseline.metrics["service_endpoint"] != candidate.metrics["service_endpoint"]
assert report["replay_capability"]["source"] == "candidate"
```

The fixture and test names must remain domain-neutral and contain no Browser/CDP
implementation.

- [ ] **Step 2: Run the integration test and verify it fails before final wiring**

Run: `pytest -q tests/self_evolve/test_skill_owned_replay_integration.py`

Expected: FAIL until runner reporting, stored loading, and provenance are fully wired.

- [ ] **Step 3: Complete reports and stored compatibility**

Persist context snapshot, requirement, discovery, compile-a, compile-b, frozen bundle,
service lifecycle, and package apply paths. Report bounded ids/fingerprints and gate
reasons without embedding fixture contents or secrets. Load legacy candidates and
replays without capability fields as unadapted compatibility records.

- [ ] **Step 4: Run full verification and inspect scope**

Run:

```bash
pytest -q tests/self_evolve
pytest -q tests/skills tests/aworld_cli 2>/dev/null || pytest -q tests/skills
git diff --check
git status --short
```

Expected: all selected tests pass; diff check exits zero; no file beneath
`aworld-skills/agent-browser` is modified.

- [ ] **Step 5: Commit integration**

Commit: `git commit -m "feat: integrate skill-owned replay capabilities"`

After the commit, run `pytest -q tests/self_evolve tests/skills` once more and record
the exact pass count before completing the branch.
