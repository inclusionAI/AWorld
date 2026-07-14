# Self-Evolve Replay Adaptation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compile trajectory datasets into deterministic replay plans and run baseline/candidate repetitions from identical isolated workspace seeds.

**Architecture:** Add a focused `replay_adaptation` module that owns dependency classification, path abstraction, snapshot manifests, adapter bindings, and per-variant workspace materialization. Thread its versioned bundle through replay requests, enforce fingerprint equality in comparability and baseline reuse, and surface a blocking runner gate for unresolved or non-deterministic adaptations.

**Tech Stack:** Python 3.12 dataclasses/protocols, pathlib/shutil/hashlib, existing AWorld self-evolve dataset/replay/runner contracts, pytest.

---

### Task 1: Replay adaptation compiler and persistence

**Files:**
- Create: `aworld/self_evolve/replay_adaptation.py`
- Modify: `aworld/self_evolve/__init__.py`
- Create: `tests/self_evolve/test_replay_adaptation.py`

- [ ] **Step 1: Write failing compiler tests**

```python
def test_compiler_normalizes_workspace_paths_and_persists_bundle(tmp_path):
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    (workspace / "input.txt").write_text("fixture", encoding="utf-8")
    dataset = _dataset("Read /Users/old/Documents/workspace/demo/input.txt")
    bundle = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )
    case = bundle.case("task-1")
    assert "${AWORLD_REPLAY_WORKSPACE}/input.txt" in case.adapted_task_input["content"]
    assert Path(bundle.workspace_seed).is_dir()
    assert Path(bundle.manifest_path).is_file()
    assert bundle.ready is True
```

Add separate tests for continuation inputs, local endpoints, missing absolute files,
bounded external regular-file fixtures, secret-like file exclusion, and a custom
deterministic adapter binding.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/self_evolve/test_replay_adaptation.py`

Expected: collection failure because `aworld.self_evolve.replay_adaptation` does not exist.

- [ ] **Step 3: Implement versioned adaptation types and compiler**

Implement these public contracts:

```python
@dataclass(frozen=True)
class ReplayDependency:
    kind: str
    identifier: str
    status: str
    deterministic: bool
    adapter_id: str | None = None
    detail: str | None = None

@dataclass(frozen=True)
class ReplayAdapterBinding:
    adapter_id: str
    dependency_id: str
    deterministic: bool
    environment: Mapping[str, str] = field(default_factory=dict)
    fixture_paths: tuple[str, ...] = ()

class ReplayDependencyAdapter(Protocol):
    adapter_id: str
    def bind(self, dependency: ReplayDependency, *, context: ReplayAdapterContext) -> ReplayAdapterBinding | None: ...

@dataclass(frozen=True)
class ReplayCaseAdaptation:
    case_id: str
    adapted_task_input: Any
    task_input_fingerprint: str
    dependencies: tuple[ReplayDependency, ...]
    bindings: tuple[ReplayAdapterBinding, ...]
    readiness: str
    diagnostics: tuple[str, ...] = ()

@dataclass(frozen=True)
class ReplayAdaptationBundle:
    schema_version: str
    source_workspace_root: str
    workspace_seed: str
    workspace_seed_fingerprint: str
    manifest_path: str
    cases: tuple[ReplayCaseAdaptation, ...]
    adaptation_fingerprint: str
    ready: bool
    def case(self, case_id: str) -> ReplayCaseAdaptation: ...
```

`ReplayAdaptationCompiler.compile()` must recursively normalize task input strings,
create a filtered seed and manifest, snapshot only explicitly referenced bounded
regular files, invoke registered adapters, derive stable fingerprints, and write
`bundle.json` through an atomic JSON helper.

- [ ] **Step 4: Run compiler tests and verify GREEN**

Run: `pytest -q tests/self_evolve/test_replay_adaptation.py`

Expected: all compiler tests pass.

- [ ] **Step 5: Commit compiler**

```bash
git add aworld/self_evolve/replay_adaptation.py aworld/self_evolve/__init__.py tests/self_evolve/test_replay_adaptation.py
git commit -m "feat: compile deterministic replay adaptations"
```

### Task 2: Isolated workspace materialization per repetition

**Files:**
- Modify: `aworld/self_evolve/replay_adaptation.py`
- Modify: `aworld/self_evolve/replay.py`
- Modify: `tests/self_evolve/test_replay_adaptation.py`
- Modify: `tests/self_evolve/test_replay_overlay.py`

- [ ] **Step 1: Write failing isolation tests**

```python
def test_materializer_restores_clean_workspace_for_each_variant(bundle, tmp_path):
    first = materialize_replay_workspace(bundle, tmp_path / "baseline")
    (first / "state.txt").write_text("baseline mutation", encoding="utf-8")
    second = materialize_replay_workspace(bundle, tmp_path / "candidate")
    assert (second / "state.txt").read_text(encoding="utf-8") == "seed"
```

Add an executor test that captures subprocess `cwd`, task placeholder expansion, and
`AWORLD_REPLAY_WORKSPACE`; assert baseline and candidate use distinct workspaces with
the same seed fingerprint.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/self_evolve/test_replay_adaptation.py tests/self_evolve/test_replay_overlay.py -k 'materializ or isolated or replay_workspace'`

Expected: failure because materialization and request adaptation fields are absent.

- [ ] **Step 3: Implement workspace materialization and executor wiring**

Add optional adaptation fields to `CandidateReplayRequest` and
`ReplayExecutionRequest`. In `_run_variant`, materialize a fresh workspace under the
repetition artifact directory, select the case adaptation, expand placeholders, merge
non-secret adapter environment variables, and execute with the isolated workspace as
`cwd`. Record adaptation, seed, task-input, and baseline-skill fingerprints in result
metrics.

- [ ] **Step 4: Run isolation tests and verify GREEN**

Run: `pytest -q tests/self_evolve/test_replay_adaptation.py tests/self_evolve/test_replay_overlay.py`

Expected: all tests pass.

- [ ] **Step 5: Commit isolation**

```bash
git add aworld/self_evolve/replay_adaptation.py aworld/self_evolve/replay.py tests/self_evolve/test_replay_adaptation.py tests/self_evolve/test_replay_overlay.py
git commit -m "feat: isolate self-evolve replay workspaces"
```

### Task 3: Strict paired comparability and baseline reuse

**Files:**
- Modify: `aworld/self_evolve/replay.py`
- Modify: `aworld/self_evolve/runner.py`
- Modify: `tests/self_evolve/test_replay_overlay.py`
- Modify: `tests/self_evolve/test_runner.py`

- [ ] **Step 1: Write failing fingerprint tests**

Add tests proving:

```python
assert candidate_replay_is_comparable(dataset=dataset, replay_result=mismatched) is False
```

when baseline/candidate adaptation, seed, or task fingerprints differ; source-history
fallback is not a strict pair; a prior baseline is not reused after skill content,
dataset input, or adaptation fingerprint changes.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/self_evolve/test_replay_overlay.py tests/self_evolve/test_runner.py -k 'fingerprint or comparable or reusable_baseline'`

Expected: the new mismatch/reuse tests fail under case-id-only behavior.

- [ ] **Step 3: Implement strict comparability and reuse metadata**

Require matching deterministic adaptation metrics in strict replay pairs. Extend
serialized requests with `dataset_fingerprint`, `baseline_skill_fingerprint`, and
`adaptation_fingerprint`. Update `_find_reusable_baseline_replay_dir()` and legacy
loading so only current-schema requests with matching fingerprints are reused;
legacy artifacts remain loadable but non-reusable.

- [ ] **Step 4: Run strict-pair tests and verify GREEN**

Run: `pytest -q tests/self_evolve/test_replay_overlay.py tests/self_evolve/test_runner.py -k 'fingerprint or comparable or reusable_baseline'`

Expected: all selected tests pass.

- [ ] **Step 5: Commit strict pairing**

```bash
git add aworld/self_evolve/replay.py aworld/self_evolve/runner.py tests/self_evolve/test_replay_overlay.py tests/self_evolve/test_runner.py
git commit -m "fix: require equivalent replay environments"
```

### Task 4: Runner compiler integration and gates

**Files:**
- Modify: `aworld/self_evolve/runner.py`
- Modify: `aworld/self_evolve/gates.py`
- Modify: `aworld/self_evolve/types.py`
- Modify: `tests/self_evolve/test_runner.py`
- Modify: `tests/self_evolve/test_gates.py`

- [ ] **Step 1: Write failing runner/gate tests**

Add tests asserting the compiler runs before `build_replay_request`, its bundle is
persisted, deterministic adaptations proceed, unresolved/context-incomplete cases
produce a failed `replay_adaptation` gate without invoking replay, proposal mode keeps
the candidate, and `auto_verified` rejects it.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/self_evolve/test_runner.py tests/self_evolve/test_gates.py -k 'replay_adaptation'`

Expected: failure because no runner adaptation stage or gate exists.

- [ ] **Step 3: Integrate compiler and reporting**

Inject a `ReplayAdaptationCompiler` into `SelfEvolveRunner`, compile once per run and
dataset, pass the bundle into replay requests, emit progress events, and expose
readiness/dependency/fingerprint diagnostics in iteration and release reports. Add a
small `ReplayAdaptationGate` that blocks unresolved or non-deterministic adaptations
for verified apply.

- [ ] **Step 4: Run runner/gate tests and verify GREEN**

Run: `pytest -q tests/self_evolve/test_runner.py tests/self_evolve/test_gates.py -k 'replay_adaptation'`

Expected: all selected tests pass.

- [ ] **Step 5: Commit runner integration**

```bash
git add aworld/self_evolve/runner.py aworld/self_evolve/gates.py aworld/self_evolve/types.py tests/self_evolve/test_runner.py tests/self_evolve/test_gates.py
git commit -m "feat: gate replay on deterministic adaptation"
```

### Task 5: Documentation and full verification

**Files:**
- Modify: `docs/Agents/Self Evolve.md`
- Modify: `docs/AWorld CLI/Commands/Optimize.md`

- [ ] **Step 1: Document replay adaptation artifacts and semantics**

Document the three-trajectory model, deterministic adapter requirement, workspace
seed layout, unresolved dependency behavior, and strict baseline reuse semantics.

- [ ] **Step 2: Run formatting/static checks**

Run: `git diff --check`

Expected: exit 0 with no output.

- [ ] **Step 3: Run focused verification**

Run: `pytest -q tests/self_evolve/test_replay_adaptation.py tests/self_evolve/test_replay_overlay.py tests/self_evolve/test_runner.py tests/self_evolve/test_gates.py`

Expected: all focused tests pass.

- [ ] **Step 4: Run the full self-evolve suite**

Run: `pytest -q tests/self_evolve`

Expected: all tests pass.

- [ ] **Step 5: Commit documentation**

```bash
git add docs/Agents/'Self Evolve.md' docs/AWorld\ CLI/Commands/Optimize.md
git commit -m "docs: explain deterministic replay adaptation"
```
