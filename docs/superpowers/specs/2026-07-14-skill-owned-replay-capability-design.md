# Skill-Owned Replay Capability Design

## Scope

This change lets trajectory-backed self-evolve runs acquire domain-specific replay
support from the skill candidate being evaluated. The self-evolve framework owns
only generic trajectory context reconstruction, capability discovery, capability
execution, frozen replay materialization, rollout isolation, and provenance gates.

The existing `aworld-skills/agent-browser` package is not modified by this change.
When Browser/CDP replay support is required, the self-evolve mutator may propose a
multi-file `agent-browser` candidate that contains a Recorded Browser/CDP capability.
The capability exists only in the candidate overlay until the candidate passes all
verification and release gates.

The design preserves the three-trajectory model:

- source trajectory: historical evidence used for context reconstruction and
  candidate generation;
- replay baseline: current skill behavior executed against a frozen replay
  environment;
- replay candidate: candidate skill behavior executed against the same frozen
  replay environment.

The capability may compile the source trajectory into a replay harness, but it is
not itself a fourth evaluation trajectory. Its frozen output is evaluation
infrastructure shared by both variants.

## Boundary

Framework-owned responsibilities are:

- reconstructing bounded, provenance-bearing context from generic trajectory data;
- detecting generic replay requirements such as local endpoints, HTTP resources,
  stateful tools, local files, and incomplete conversation context;
- representing a skill candidate as `SKILL.md` plus bounded skill-owned file deltas;
- discovering a versioned replay capability manifest from a candidate or baseline
  skill package;
- invoking a capability through a generic subprocess protocol;
- validating, compiling twice, fingerprinting, and freezing capability output;
- starting isolated replay services from the frozen output for each rollout;
- guaranteeing that baseline and candidate receive equivalent replay bindings;
- atomically publishing or rolling back a verified multi-file skill candidate.

Skill-owned responsibilities are:

- deciding whether its domain can satisfy the generic replay requirements;
- interpreting domain-specific trajectory evidence;
- creating domain fixtures and domain-specific replay services;
- declaring which dependencies it handled and the evidence used;
- shipping its compiler and runtime with the skill if the candidate is accepted.

Browser, CDP, X/Twitter, DOM, Playwright, and `agent-browser` command semantics must
not appear in `aworld/self_evolve`. The framework treats a Recorded Browser/CDP
adapter exactly like any other skill-owned replay capability.

## Chosen Approach

Use a multi-file skill candidate, a skill-local versioned manifest, a subprocess JSON
protocol, and a frozen compilation shared by paired rollouts.

Rejected alternatives:

- Importing candidate Python in the self-evolve process gives unverified candidate
  code access to framework state and makes cleanup unreliable.
- A framework-owned declarative Browser/CDP DSL moves domain behavior into
  `aworld/self_evolve` and cannot cover independent skill domains without becoming a
  second skill runtime.
- Loading the capability only for the candidate makes a missing baseline adapter
  indistinguishable from a skill improvement and produces incomparable rollouts.
- Hand-editing the current `agent-browser` skill bypasses candidate generation and
  would treat evaluation infrastructure as an already-accepted behavior change.

## Candidate Package Model

`CandidateVariant` remains backward compatible with text-only candidates and gains a
tuple of bounded skill file deltas. A delta contains:

- a normalized relative path;
- an `upsert` or `delete` operation;
- UTF-8 text content for an upsert;
- a portable executable flag when required by a capability entrypoint.

`SKILL.md` remains in `CandidateVariant.content` and cannot also appear in file
deltas. The first implementation permits candidate-owned replay files only beneath
`replay/`. Paths must not be absolute, escape the skill root, traverse symlinks, or
target protected package files. File count, individual byte size, aggregate byte
size, and executable modes are bounded.

Candidate identity, no-op detection, duplicate detection, optimizer lineage, token
or size gates, proposal persistence, and release provenance use a canonical package
fingerprint covering both `SKILL.md` and file deltas. Legacy candidates without file
deltas retain their existing serialization and behavior.

The candidate overlay is built by copying the current target skill package and then
applying candidate deltas. Other skills are copied as today. This preserves existing
target assets while allowing a candidate to add or replace replay capability files.

## Generic Trajectory Context Reconstruction

Dataset construction produces a bounded `TrajectoryContextSnapshot` for each
trajectory-backed case before trace-pack compression. The snapshot is generic and
contains:

- case id, task id, source record index, and source fingerprint;
- recorded session id, step ids, agent ids, predecessor agent ids, and timestamps
  when present;
- the original task input;
- bounded state/action/reward events, messages, tool calls, and tool results;
- source-backed artifact references and content fingerprints;
- reconstructed prior conversation turns and the rule used to link them;
- truncation, omission, and sanitization diagnostics.

Context linkage follows deterministic precedence:

1. explicit parent or previous-task metadata;
2. prior turns already recorded in the case message history;
3. the chronological predecessor in the same recorded session;
4. for an explicit continuation marker only, the adjacent source record, marked as
   `adjacent_record_fallback` provenance.

If none applies, the case remains `context_incomplete`. Reconstruction never infers
Browser/CDP state or manufactures missing tool output.

Snapshots are stored as run artifacts and supplied to capabilities by path and
fingerprint. They retain more replay evidence than the bounded optimizer
`TracePack`, while still enforcing secret sanitization and total-size limits.

## Replay Requirement Preflight

Before candidate generation, the existing replay adaptation analysis runs in
preflight mode. It creates generic `ReplayCapabilityRequirement` records from the
replayable dataset without requiring all dependencies to be resolved. Each record
contains:

- requirement id, generic kind, and normalized identifier;
- affected case ids;
- evidence references into trajectory context snapshots;
- current status and bounded diagnostic text.

The optimizer request includes these requirements, the target skill package
inventory, and the candidate file-output contract. When unresolved requirements are
relevant to the selected skill, the mutator may return structured output containing
both `content` and `files`. The framework does not prescribe a Browser/CDP
implementation; it asks the candidate to provide a capability that satisfies the
generic requirements and protocol.

A text-only mutator response remains valid. If unresolved requirements remain after
candidate generation, the existing replay-adaptation gate rejects strict replay.

## Capability Discovery

A replay capability is declared at this fixed skill-relative path:

```text
replay/capability.json
```

The initial manifest schema contains:

```json
{
  "schema_version": "aworld.skill.replay_capability.v1",
  "capability_id": "skill-owned-id",
  "protocol": "aworld.replay.subprocess.v1",
  "entrypoint": "replay/compiler.py",
  "handles": ["local_endpoint", "http_resource", "stateful_tool"],
  "runtime_files": []
}
```

All declared paths must resolve to regular files inside the materialized skill root
and must be included in the candidate package fingerprint. Unknown schema versions,
duplicate capability ids, path escapes, missing files, unsupported protocols, or
undeclared runtime files fail closed.

For each selected candidate, discovery first inspects the materialized candidate
package. If it has no capability manifest, discovery may use the baseline target
skill capability. It never combines two competing manifests for one target. The
selected capability id, source, and package fingerprint are persisted.

The existing constructor-injected `ReplayDependencyAdapter` mechanism remains a
backward-compatible trusted integration and test hook. It does not participate in
skill capability discovery, and no Browser/CDP implementation is registered through
that framework-owned path.

## Capability Subprocess Protocol

The framework invokes the declared compiler without a shell. The compiler receives a
read-only JSON request path and a writable output directory. The request contains:

- protocol and schema versions;
- sanitized requirements;
- paths and fingerprints for context snapshots;
- generic task inputs with workspace placeholders;
- a capability-local source root;
- explicit resource and timeout limits.

The compiler writes a result manifest containing:

- handled and unhandled requirement ids;
- source evidence references for every handled requirement;
- fixture paths relative to its output root;
- evidence references that bind every fixture byte-for-byte to a recorded context
  value or task input;
- endpoint replacements limited to identifiers present in requirements;
- generic service specifications;
- bounded diagnostics and a deterministic declaration.

A service specification declares a framework-owned generic fixture transport, a
provenance-verified response fixture, a TCP or HTTP readiness probe, a startup
timeout, and a logical endpoint placeholder. Candidate-owned runtime code is never
executed during evaluation; the skill owns compilation into a generic recorded
transport plan, while the framework owns the byte-serving process. It may not add
arbitrary agent prompts, change expected outputs, change verification commands, or
inject environment variables.

The subprocess runs with a minimal environment, an isolated working directory,
bounded time and output, and no shell interpolation. Execution and isolation are
behind a `ReplayCapabilityExecutor` interface so tests and stricter platform sandbox
backends can replace the default process executor. Failure to enforce configured
isolation policy rejects the capability rather than silently weakening it.

## Deterministic Compilation And Freezing

Candidate capability compilation happens after the candidate overlay exists and
before either paired rollout starts. The framework runs the compiler twice in clean
output directories using identical requests. It canonicalizes both results and
compares:

- handled dependency ids;
- task endpoint replacements;
- service specifications;
- fixture manifests and content hashes;
- evidence-reference mappings.

Any difference rejects the capability as non-deterministic. The framework also
rejects undeclared files, path escapes, missing evidence refs, fixture bytes that do
not exactly match a value in the cited context/input evidence, attempts to handle
unknown requirement ids, unrestricted task mutations, and unresolved required
dependencies.

On success, one canonical result is copied into an immutable frozen capability
bundle. Its fingerprint covers the capability package, context snapshots,
requirements, compiler output, provenance-bound fixtures, generic service plans, and
the workspace seed. Replay cache keys include this fingerprint and therefore cannot reuse an
adaptation compiled by a different candidate capability.

## Paired Rollout Lifecycle

The frozen capability bundle is shared by the baseline and candidate request. The
bundle is never recompiled between variants or repetitions.

Each repetition receives:

- a clean copy of the same workspace seed;
- a clean copy of the same frozen fixtures;
- the same adapted task input and logical endpoint replacements;
- an independently started service instance with a repetition-local port;
- identical limits and readiness rules;
- a guaranteed shutdown attempt after completion, timeout, or cancellation.

Framework-owned service processes and writable state are never shared across
variants, cannot initiate outbound connections, and cannot read or write rollout
workspaces/evidence. A port number may differ, but the logical service id, frozen bundle fingerprint, fixture
fingerprint, and adapted task-input fingerprint must match. Startup failure is a
replay infrastructure failure, not candidate feedback.

Paired replay is comparable only if every baseline/candidate repetition records the
same context, requirement, capability, frozen bundle, workspace seed,
fixture, and task-input fingerprints. Variant-specific capability compilation or
bindings fail the comparability gate.

## Publication And Rollback

Proposal runs persist the multi-file candidate under the run artifact directory and
never alter the target skill package. Candidate overlays remain disposable.

For `auto_verified`, release normalization applies to `SKILL.md`, while replay files
are validated against their accepted package fingerprint. Apply uses a staged skill
directory and atomic replacement where supported. The apply journal records a full
skill-package backup, not only a Markdown backup. Post-apply verification reloads the
skill registry and checks both normalized content and execution-asset fingerprints.

If post-apply evaluation, activation, registry refresh, or fingerprint verification
fails, the complete original skill directory is restored. A rejected or rolled-back
candidate must not leave `replay/` files in the current `agent-browser` skill.

## Gates And Anti-Gaming Constraints

New or extended gates enforce:

- valid and bounded candidate file deltas;
- canonical package fingerprint and meaningful package change;
- manifest schema and asset closure;
- capability subprocess policy compliance;
- source-backed requirement and evidence references;
- deterministic double compilation;
- immutable frozen bundle integrity;
- identical paired rollout bindings;
- complete package-level apply and rollback verification.

Capability output cannot change the semantic task except by replacing a dependency
identifier already declared by preflight with its isolated replay endpoint. It cannot
inject candidate-only instructions or variant-specific data. Because the same frozen
harness is supplied to both variants, any recorded environment data benefits both
equally; measured differences remain attributable to the skill package used during
the rollout.

## Persistence And Reporting

Run artifacts add:

```text
.aworld/self_evolve/<run-id>/
  trajectory_context/<case-id>.json
  replay_requirements.json
  candidates/<candidate-id>/
    candidate.json
    SKILL.md
    replay/...
  replay_capabilities/<candidate-id>/
    discovery.json
    compile-a/
    compile-b/
    frozen/
    frozen_manifest.json
```

Reports expose capability ids, handled and unresolved requirement ids, evidence refs,
all comparability fingerprints, service startup diagnostics, and gate reasons. They
do not embed secrets, full browser profiles, authentication state, or unrestricted
tool output.

Stored text-only candidates and replay bundles remain loadable. They simply have no
candidate package files or skill-owned capability fingerprint and cannot satisfy a
new unresolved dependency unless the baseline skill already supplies a compatible
capability.

## Error Handling

- Context reconstruction failure leaves an explicit incomplete context requirement.
- Invalid candidate file output rejects the candidate before capability execution.
- Missing or invalid manifests leave requirements unresolved.
- Capability process failure, timeout, excessive output, or protocol violation is an
  infrastructure gate failure.
- Non-deterministic compilation rejects the capability before rollout.
- Service startup and readiness failures reject replay without host repair or live
  network fallback.
- Cleanup failures are persisted and block automatic apply.
- Proposal artifacts may be retained for diagnosis, but only a fully verified
  candidate package may modify the target skill.

## Verification

Tests will cover:

- backward-compatible text-only candidate serialization;
- bounded multi-file candidate parsing, fingerprinting, persistence, and path guards;
- candidate overlay deltas without modifying the baseline skill;
- generic context reconstruction from explicit parent, message history, session
  order, adjacent continuation fallback, and irrecoverable context;
- replay-requirement preflight without domain-specific Browser/CDP logic;
- manifest discovery from candidate and baseline skill packages;
- subprocess request/result validation and path containment;
- deterministic double compilation and mismatch rejection;
- frozen bundle integrity and candidate-specific cache keys;
- per-repetition service startup, readiness, endpoint expansion, and cleanup;
- identical frozen bindings with isolated baseline/candidate service state;
- rejection of prompt mutation, unknown dependencies, arbitrary environment
  injection, and missing evidence refs;
- package-level proposal persistence, atomic apply, post-apply verification, and full
  rollback;
- stored legacy candidate/replay compatibility;
- the complete self-evolve, skill registry, CLI replay, and release suites.

An integration fixture may define a synthetic skill-owned recorded service to prove
the protocol end to end. No test or production implementation adds Browser/CDP logic
to `aworld/self_evolve`, and this change does not edit the current
`aworld-skills/agent-browser` files.
