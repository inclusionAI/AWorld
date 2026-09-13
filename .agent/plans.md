# Project Plan

## Architecture Overview

The corrected integration keeps four ownership boundaries. The independent
`benchmark-datasets/parsebench` project owns ParseBench conversion, private
verification, and scoring. Unmodified lingguang-bench-client owns standard
project validation, ZIP construction, and its existing upload workflow.
Unmodified mcpgateway owns Dataset validation, publication, and Harbor/Arca
orchestration. AWorld/FileX owns generic CLI and skill behavior, while
lingguang-bench-runtime owns the execution adapter and immutable dependencies.

The full data flow is:

`ParseBench@pinned revision -> independent standard Dataset project -> stock lingguang-bench-client package -> stock mcpgateway upload/publish -> Arca Runtime -> generic aworld-cli + FileX skill -> Dataset verifier -> generic results`.

## Branch and Worktree Matrix

| Repository | Base | Feature branch | Worktree |
| --- | --- | --- | --- |
| AWorld | `origin/main` at `b4d24300` | `codex/filex-parsebench-benchmark` | `/private/tmp/aworld-filex-parsebench` |
| mcpgateway (net diff zero) | `origin/codex/fix-harbor-runner-readiness-db-pool` at `b9f54ffe` | `codex/filex-parsebench-benchmark` | `/private/tmp/mcpgateway-filex-parsebench` |
| lingguang-bench-runtime | `origin/codex/fix-arca-task-upload` at `8b28a35` | `codex/filex-parsebench-benchmark` | `/private/tmp/runtime-filex-parsebench` |
| lingguang-bench-client (net diff zero) | `origin/master` at `c3cdc378` | `codex/filex-parsebench-benchmark` | `/private/tmp/lingguang-bench-client-filex-parsebench` |

## Milestones

### Milestone 1: Dataset and scoring foundation

**Goal:** Produce reproducible ParseBench inputs and prove score equivalence on fixtures.
**Depends on:** None.

#### Task 1.1: Pin and model the upstream contracts

- **Parallel:** yes.
- **Files:** independent `benchmark-datasets/parsebench` tooling, provenance manifest, tests.
- **Approach:** Encode the pinned HF/scorer revisions, eight-field source schema, rule decoding, page numbering, five score groups, and overall aggregation contract.
- **Tests:** schema validation, invalid rule JSON, missing resource, stable provenance, dimension completeness.
- **Acceptance criteria:** no network or model call is needed to validate the contract on local fixtures.
- **Status:** completed; pinned contracts and content-bound revision checks are covered by fixture and full-cardinality tests.

#### Task 1.2: Build the executable dataset converter

- **Parallel:** no, depends on Task 1.1.
- **Files:** independent project generator, package templates, tests, docs.
- **Approach:** Group by normalized source document/page execution key, generate deterministic task IDs, place source inputs in `environment/`, private rules in `tests/`, and produce `dataset.yaml`, `dataset.jsonl`, `manifest.json`, and task archives with checksums.
- **Tests:** idempotent output, 2,078 expected full cardinality from metadata, 169,011 preserved rules, path traversal rejection, duplicate IDs, archive layout, smoke selection.
- **Acceptance criteria:** generated fixtures pass mcpgateway package validation.
- **Status:** completed; the generator emits a standard project consumed by the unmodified client's existing packager.

#### Task 1.3: Vendor or bind the pinned official scorer

- **Parallel:** yes with Task 1.2 after Task 1.1.
- **Files:** scorer adapter/dependency metadata, verifier entrypoint, tests.
- **Approach:** Prefer a pinned external checkout/wheel boundary over copied evaluator logic; normalize FileX output to the official ParseBench structures and retain detailed metric inputs for global reduction.
- **Tests:** golden results for all five dimensions and exact reducer parity.
- **Acceptance criteria:** local verifier and official scorer produce identical fixture results.
- **Status:** completed; the locked external scorer boundary and exact dimension-first reducer parity are implemented.

### Milestone 2: FileX capability and generic AWorld skill workflow

**Goal:** Run and report ParseBench deterministically through FileX.
**Depends on:** Milestone 1.

#### Task 2.1: Stabilize generic FileX artifacts for Dataset verification

- **Parallel:** yes.
- **Files:** FileX CLI/provider, reusable FileX skill, Dataset verifier, tests.
- **Approach:** Have the skill emit a generic content-addressed artifact bundle; validate and normalize it inside the Dataset-owned verifier.
- **Tests:** Markdown, table HTML, page index conversion, bbox normalization/clipping, label mapping, cache isolation, retry/error classification.
- **Acceptance criteria:** each fixture yields scorer-compatible output and reproducible metadata.
- **Status:** completed; FileX emits generic hashed Markdown/raw Document IR/provider evidence and the Dataset verifier performs ParseBench normalization.

#### Task 2.2: Integrate FileX through the generic AWorld skill interface

- **Parallel:** yes after the adapter interface is fixed.
- **Files:** existing `aworld-skills/filex`, generic AWorld CLI surface, Runtime AWorld harness.
- **Approach:** Keep FileX as an independent CLI and reusable skill; invoke it through `aworld-cli run --skill filex` without teaching AWorld about ParseBench.
- **Tests:** skill discovery, CLI availability, generic harness command construction, protected model environment mapping.
- **Acceptance criteria:** Arca's AWorld harness can load the FileX skill and run a Dataset-authored instruction.
- **Status:** completed; the dedicated ParseBench CLI was removed and Runtime now uses only the generic skill path.

### Milestone 3: mcpgateway Harbor/Arca integration

**Goal:** Dispatch prepared ParseBench packages through the current Harbor/Arca path and preserve score artifacts.
**Depends on:** Milestones 1 and 2 contracts.

#### Task 3.1: Create isolated mcpgateway branch and verify generic package compatibility

- **Parallel:** no.
- **Files:** only `yolo_scheduler`, tests, and nearby docs if code changes are necessary.
- **Approach:** Start from the published Harbor readiness branch, import the smoke package, and identify the smallest generic contract change needed.
- **Tests:** package validation/publish, managed locator binding, immutable planning.
- **Acceptance criteria:** no modifications under `mcp_gateway/src/mcp_gateway`.
- **Status:** completed; the smoke package is accepted by the unmodified generic gateway package parser, and the Gateway net source diff is zero.

#### Task 3.2: Preserve detailed benchmark metrics and dual model profiles

- **Parallel:** yes if independent.
- **Files:** Harbor harness config/model access/artifact projection and tests.
- **Approach:** Add allowlisted AWorld `llm_profile` and `vlm_profile` references if the existing contract cannot express both; pass ParseBench detailed metrics as artifacts while keeping the scalar reward scheduler-neutral.
- **Tests:** secret/reference validation, agent_only+Arca planning, artifact projection, zero/not-scored/failure distinctions.
- **Acceptance criteria:** gateway results retain enough information for exact official global aggregation.
- **Status:** completed; protected profiles, detailed artifacts, terminal-state projection, idempotency, and exact acceptance checks are covered.

### Milestone 4: Runtime image and harness

**Goal:** Make the matching AWorld/FileX build runnable in the Arca runtime.
**Depends on:** Milestones 2 and 3.

#### Task 4.1: Create isolated runtime branch and update bundled artifacts

- **Parallel:** no.
- **Files:** runtime Dockerfile/build manifests, wheel lock/digest, tests/docs.
- **Approach:** Bundle matching AWorld/aworld-cli/FileX artifacts and required system dependencies without runtime downloads where practical.
- **Tests:** wheel digest, import/CLI health, FileX dependency probe.
- **Acceptance criteria:** image build inputs are immutable and contain no credentials.
- **Status:** completed; the Runtime locks and bundles matching AWorld, CLI, and FileX wheels plus the pinned local layout model contract.

#### Task 4.2: Extend the AWorld Harbor harness

- **Parallel:** yes after contracts stabilize.
- **Files:** AWorld adapter, model-profile mapping, artifact collector, tests.
- **Approach:** Run the generic AWorld task entrypoint, inject the FileX skill and protected LLM/VLM bindings, redact signed URLs, and collect standard trajectories/artifacts.
- **Tests:** command construction, environment mapping, secret redaction, synthetic task prepare/start/collect.
- **Acceptance criteria:** a synthetic ParseBench task completes in local runtime mode with standard Harbor outputs.
- **Status:** completed; the Harbor AWorld adapter installs/exposes both CLIs, uses Python 3.12 constraints, mounts the offline model, and collects standard artifacts.

### Milestone 5: End-to-end verification and reviews

**Goal:** Prove the full workflow on a small representative task subset and document the optional full-run path.
**Depends on:** Milestones 1-4.

#### Task 5.1: Run the local verification ladder

- **Parallel:** no.
- **Files:** test evidence only; code fixes are scoped to the owning milestone.
- **Approach:** Run AWorld/FileX unit suites, converter golden tests, mcpgateway package/Harbor tests, runtime tests, and only a small five-dimension smoke subset.
- **Tests:** all scoped commands plus the nearest practical E2E.
- **Acceptance criteria:** introduced tests pass; pre-existing failures are isolated and documented.
- **Status:** completed; synthetic, fixture, official-scorer, real single-task text, and local chart-path smoke evidence is recorded in `.agent/progress.md`.

#### Task 5.2: Live Arca readiness gate

- **Parallel:** no.
- **Files:** runbook and evidence.
- **Approach:** If credentials and a reachable service are available, run one or a few representative real Arca tasks; otherwise provide exact commands and a blocker report. Do not expand into a full benchmark campaign.
- **Tests:** input mount, FileX invocation, artifact collection, metric parity, cleanup.
- **Acceptance criteria:** either the live smoke passes or the only blockers are external access/cost, with all local contracts proven.
- **Status:** excluded from this local acceptance at the user's direction. Real VLM credibility and Docker sandbox behavior were validated independently; no Arca claim is made.

#### Task 5.3: Architectural review cycles

- **Parallel:** no.
- **Files:** all diffs across the three repositories.
- **Approach:** Review after every milestone, fix material issues, and perform a final cross-cutting review.
- **Tests:** rerun affected quality gates after each fix.
- **Acceptance criteria:** no unresolved critical or important review issue.
- **Status:** completed; milestone reviews were applied and the final frozen commits received cross-repository review.
