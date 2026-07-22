# Plan 002: Establish a self-evolve contract matrix in CI

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan in
> `plans/README.md` unless a reviewer told you they maintain the index.
>
> **Drift check (run first)**:
> `git diff --stat 70cb5c9a..HEAD -- .github/workflows/tests.yml pytest.ini tests/self_evolve`
> If any in-scope file changed since this plan was written, compare the
> "Current state" facts against the live code before proceeding. A semantic
> mismatch is a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `70cb5c9a`, 2026-07-21

## Why this matters

The self-evolve subsystem has hundreds of tests, but the repository CI step
named "Run tests with pytest" does not execute pytest. The upcoming replay,
conformance, diagnostics, and budget changes all alter state-machine semantics;
they need one cardinality-neutral contract matrix that runs for both one
trajectory and multiple trajectories. This plan establishes that verification
baseline without encoding the rejected run or any target-specific behavior.

## Current state

- `.github/workflows/tests.yml:20-42` uses Ubuntu, installs pytest twice, and
  leaves `pytest tests` commented out.
- `pytest.ini:24-31` already defines `slow`, `integration`, `unit`, and optional
  dependency markers, but there is no marker for tests that require the native
  replay sandbox.
- `aworld/self_evolve/replay_capability.py:2450-2461` fails closed unless the
  host provides a supported platform sandbox; the current implementation uses
  macOS `sandbox-exec`. Therefore an Ubuntu-only full replay job is not a valid
  verification strategy.
- Existing positive coverage is split between single-case tests such as
  `tests/self_evolve/test_runner.py:7576` and multi-member tests such as
  `tests/self_evolve/test_replay_overlay.py:3069`. There is no shared matrix
  that requires new framework behavior to work for both cardinalities.
- The repository test convention is pytest functions with explicit dataclass
  fixtures and fake backends; follow the nearby builders in
  `tests/self_evolve/test_runner.py:559-704` and
  `tests/self_evolve/test_replay_overlay.py:2769-3004`.

The invariant to establish is: **trajectory cardinality is test data, not a
control-flow mode**. New framework contracts must be parameterized over at
least `case_count in {1, 3}`. Three cases are used instead of two so tests can
cover both repeated and distinct fixture/requirement shapes in later plans.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Collect suite | `python -m pytest tests/self_evolve --collect-only -q` | exit 0 and a non-zero test count |
| Platform-neutral smoke | `python -m pytest tests/self_evolve/test_lessons.py tests/self_evolve/test_diagnostics.py tests/self_evolve/test_execution_telemetry.py tests/self_evolve/test_evaluation_backend.py tests/self_evolve/test_credit_assignment.py tests/self_evolve/test_gates.py -q` | all pass |
| Replay contract tests | `python -m pytest tests/self_evolve/test_replay_overlay.py tests/self_evolve/test_runner.py -k "single_case or multi_member or baseline_preflight or candidate_screening" -q` | all selected tests pass on macOS |
| Full subsystem | `python -m pytest tests/self_evolve -q` | all pass on a supported replay-sandbox host |

## Scope

**In scope**

- `.github/workflows/tests.yml`
- `pytest.ini`
- `tests/self_evolve/conftest.py` if shared parameterized factories are needed
- A new `tests/self_evolve/test_framework_contract_matrix.py`
- Test-only marker additions to replay tests that truly launch the native
  sandbox

**Out of scope**

- Any production file under `aworld/self_evolve/`.
- Implementing a Linux replay sandbox.
- Marking broad groups of failing tests as skipped or xfailed.
- Tests that reproduce candidate IDs, target names, payload excerpts, or paths
  from any historical run artifact.
- Lowering replay repetitions or acceptance thresholds.

## Git workflow

- Branch: `codex/002-self-evolve-contract-matrix`
- Use conventional commit style, for example:
  `test(self-evolve): enforce trajectory cardinality contracts`
- Do not push or open a PR unless instructed by the operator.

## Steps

### Step 1: Define the cardinality-neutral test vocabulary

Create `tests/self_evolve/test_framework_contract_matrix.py` and, only if
needed, small shared factories in `tests/self_evolve/conftest.py`. Define test
inputs for:

1. one replayable case;
2. three replayable cases with the same capability requirement shape;
3. three replayable cases with two distinct requirement/fixture shapes;
4. mixed member outcomes where one member fails and others are blocked or
   succeed.

The factory must accept arbitrary case IDs and payloads. It must not contain a
special branch for a particular target, protocol, or run artifact. At this
stage, add only characterization assertions that pass against current behavior,
such as deterministic member ordering, dataset fingerprint stability, and
preservation of every case ID. Leave TODO comments pointing to plans 004–007
only when the future assertion cannot pass yet; do not add xfails.

**Verify**:
`python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q`
→ all tests pass.

### Step 2: Separate platform-neutral and native-sandbox verification

Register a `replay_sandbox` marker in `pytest.ini`. Apply it only to tests that
actually start candidate-owned runtime code through
`build_replay_sandboxed_command`; do not mark pure replay aggregation, fake
backend, serialization, compiler, or repair-conformance unit tests.

Validate the selection boundaries:

- `python -m pytest tests/self_evolve -m "not replay_sandbox" --collect-only -q`
  must collect the platform-neutral majority of the suite.
- `python -m pytest tests/self_evolve -m replay_sandbox --collect-only -q`
  must collect at least one test and only native-sandbox tests.

If determining whether a test starts the sandbox requires guessing, STOP and
report the ambiguous tests rather than marking an entire file.

### Step 3: Make CI execute real tests

Replace the no-op pytest step in `.github/workflows/tests.yml` with two jobs or
an explicit matrix:

- Ubuntu runs the platform-neutral self-evolve suite with
  `python -m pytest tests/self_evolve -m "not replay_sandbox" -q`.
- macOS runs the native-sandbox subset with
  `python -m pytest tests/self_evolve -m replay_sandbox -q`, plus the new
  framework contract matrix.

Keep dependency installation identical where possible. Use Python 3.11, the
version already configured by the workflow. Do not silently permit failures
with `continue-on-error`.

**Verify**: validate the YAML with the repository's available YAML parser, or
`python -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml'))"`
when PyYAML is installed → exit 0. Then run both local pytest selections on a
supported macOS host → all pass.

### Step 4: Document the contract for later executors

At the top of `test_framework_contract_matrix.py`, add a short module docstring:

- all lifecycle, conformance, diagnostics, lesson, and budget behavior added by
  plans 004–007 must be exercised with one and multiple cases;
- member aggregation must be order-stable;
- failure semantics may not depend on `member_results` being empty for a
  single case;
- multi-case cost control may deduplicate equivalent requirement shapes, but
  may not omit distinct shapes.

This is a test-maintenance contract, not product documentation.

**Verify**: `python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q`
→ all pass.

## Test plan

- Add positive characterization for `case_count=1` and `case_count=3`.
- Add repeated-shape and distinct-shape datasets for plans 005 and 007 to
  extend.
- Assert deterministic ordering and no case loss.
- Assert the CI marker split collects both groups and does not collect zero
  tests.
- Do not assert the currently incorrect meanings of `replayed_candidate_ids`,
  `candidate_failure_count`, or `baseline_preflight_failed`; plans 004 and 007
  replace those semantics.

## Done criteria

- [ ] CI invokes pytest and fails when a selected test fails.
- [ ] Ubuntu runs a platform-neutral self-evolve selection.
- [ ] macOS runs all native-sandbox tests.
- [ ] A shared contract matrix covers one and three trajectory cases.
- [ ] The matrix includes same-shape and distinct-shape multi-case inputs.
- [ ] No production files changed.
- [ ] `python -m pytest tests/self_evolve/test_framework_contract_matrix.py -q` passes.
- [ ] `python -m pytest tests/self_evolve -m "not replay_sandbox" -q` passes.
- [ ] On macOS, `python -m pytest tests/self_evolve -m replay_sandbox -q` passes.
- [ ] `git diff --name-only` contains only files listed in Scope and the plan
  index status update.

## STOP conditions

Stop and report if:

- The platform-neutral suite needs production changes to pass.
- The native sandbox is unavailable on the macOS runner.
- More than a narrow set of tests cannot be classified without executing
  untrusted candidate runtime code.
- CI installation requires credentials, external services, or live LLM calls.
- Existing tests write outside pytest temporary directories or ignored test
  artifact roots.

## Maintenance notes

Reviewers should reject later self-evolve changes that add only a single-case
or only a multi-case regression test. The two cardinalities share one semantic
contract; the matrix exists to prevent the framework from reintroducing
cardinality branches. Linux sandbox support is intentionally deferred and must
not be simulated with an unsafe no-sandbox fallback.
