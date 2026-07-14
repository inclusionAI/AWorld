# Self-Evolve Replay Adaptation Design

## Scope

This change makes trajectory-backed self-evolve comparisons attributable to skill
changes even when the source trajectory and replay run come from different host
environments. It inserts a replay adaptation compiler between dataset construction
and `build_replay_request()`, then runs every baseline and candidate repetition from
the same immutable workspace seed.

The design preserves the three-trajectory model:

- source trajectory: candidate-generation evidence and semantic anchor;
- replay baseline: the current skill executed in the adapted environment;
- replay candidate: the candidate skill executed in the same adapted environment.

Generic emulation of arbitrary authenticated browsers or live services is out of
scope. Such dependencies require a registered adapter. Unknown or unresolved
dependencies remain useful candidate-generation evidence, but cannot authorize an
`auto_verified` apply.

## Chosen Approach

Use a hybrid compiler. Portable path and local-file adaptation is framework-owned;
stateful external dependencies use typed adapters. The compiler may infer
dependencies, but only registered deterministic adapters may claim that a dependency
is simulated.

Rejected alternatives:

- Direct historical-to-candidate comparison does not isolate environment drift.
- Fully model-generated mocks can silently weaken task semantics.
- Hand-authored replay recipes for every dataset are reliable but do not scale and
  make `trajectory.log` a poor default dataset input.

## Replay Adaptation Model

`ReplayAdaptationCompiler.compile()` consumes a `SelfEvolveDataset`, source workspace,
run artifact root, and optional adapter registry. It produces a versioned
`ReplayAdaptationBundle` containing one `ReplayCaseAdaptation` per replayable case.

Each case records:

- the adapted task input and its fingerprint;
- normalized workspace references and snapshotted external-file dependencies;
- detected tools and external prerequisites;
- adapter bindings and deterministic/non-deterministic classification;
- an immutable workspace seed path and content fingerprint;
- a stable adaptation fingerprint;
- readiness status and bounded diagnostics.

Task inputs use `${AWORLD_REPLAY_WORKSPACE}` and `${AWORLD_REPLAY_ARTIFACT_DIR}`
placeholders. Expansion happens only in the variant subprocess, after its isolated
workspace is materialized.

## Dependency Classification

Dependencies use these statuses:

- `portable`: available inside the workspace seed;
- `snapshotted`: an explicitly referenced regular local file was copied into the
  replay seed;
- `adapter_bound`: a registered deterministic adapter supplied replay bindings;
- `runtime_required`: a live prerequisite is still required;
- `unresolved`: the dependency cannot be reproduced from available evidence;
- `context_incomplete`: the task is a continuation whose required prior state is
  absent.

The default compiler detects workspace paths, absolute local paths, local endpoints,
HTTP(S) resources, tool names, and continuation-style inputs. It snapshots bounded
regular files that are explicitly referenced and currently readable. It does not
copy credentials, browser profiles, sockets, device files, or directories outside
the workspace.

An adapter binding may contribute environment variables, fixture paths, and a
determinism declaration. Secret values are never persisted in the bundle.

## Workspace Isolation

The compiler creates one filtered workspace seed per run. It excludes `.git`,
`.aworld`, virtual environments, dependency caches, bytecode, and known replay output
directories. A manifest stores relative paths, sizes, modes, and SHA-256 digests.

Before each baseline or candidate repetition, the replay backend copies the same seed
into that repetition's artifact directory. The subprocess runs with that copy as its
working directory. Baseline writes therefore cannot affect candidate initial state,
and one repetition cannot affect another.

The skill under test remains variant-specific: baseline uses the current skill root;
candidate uses the existing shadow overlay. Both receive identical task input,
workspace seed fingerprint, adapter bindings, limits, and inherited non-secret
runtime configuration.

## Comparability and Reuse

Every replay result records:

- adaptation fingerprint;
- workspace seed fingerprint;
- task-input fingerprint;
- baseline skill fingerprint;
- adapter determinism;
- isolated workspace path.

A baseline/candidate pair is comparable only when adaptation, seed, and task-input
fingerprints match and the adaptation is deterministic. Task failures remain valid
outcomes when they contain replay trajectory evidence. Infrastructure failures and
source-trajectory fallbacks are not strict pairs and cannot authorize
`auto_verified` apply.

Reusable baseline artifacts must additionally match target identity, baseline skill
fingerprint, dataset fingerprint, adaptation fingerprint, and requested repetition
count. A case-id-only match is insufficient.

## Persistence

Each run writes:

```text
.aworld/self_evolve/<run-id>/replay_adaptation/<dataset-fingerprint>/
  bundle.json
  workspace_seed/
  workspace_manifest.json
  environment_snapshot.json
  fixtures/
```

Each repetition writes its isolated workspace under its existing replay artifact
directory. Reports expose readiness, unresolved dependencies, fingerprints, and
adapter ids without embedding secrets or full file contents.

Stored replay loading remains backward compatible. Legacy requests without an
adaptation bundle load successfully but are marked `legacy_unadapted` and are not
eligible for strict cross-run baseline reuse.

## Error Handling

- Compilation failures produce a failed `replay_adaptation` gate before subprocesses
  start.
- Unknown external prerequisites produce bounded diagnostics instead of host repair.
- Snapshot size or file-count limits produce an explicit incomplete-snapshot result.
- Adapter exceptions are infrastructure failures and never become skill feedback.
- `proposal` runs may preserve a candidate when replay is unavailable;
  `auto_verified` requires a ready deterministic adaptation.

## Verification

Tests cover:

- path placeholder normalization and expansion;
- bounded local-file snapshots and secret/path exclusions;
- dependency and continuation classification;
- deterministic custom adapter bindings;
- one immutable seed producing clean baseline/candidate/repetition workspaces;
- side-effect isolation;
- strict pair fingerprint checks;
- baseline reuse rejection after task, skill, dataset, or environment changes;
- persistence and legacy stored-replay compatibility;
- runner gates and report diagnostics;
- the existing self-evolve suite.
