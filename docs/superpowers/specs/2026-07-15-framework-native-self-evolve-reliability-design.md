# Framework-Native Self-Evolve Reliability Design

## Status

Approved architecture. This design is ready for written-spec review before an
implementation plan is created.

## Context

The AWorld-native self-evolve execution work moved candidate generation, replay,
evaluation, and the outer optimize workflow onto standard AWorld `Task` and Runner
lifecycles. A real trajectory-backed run then exposed the next reliability gap:
the orchestration worked, but every model response used an envelope shape that the
candidate parser rejected. All four slots entered schema repair; a later provider
connection failure at slot zero triggered deterministic fail-fast and left the run
with no replayable candidate.

That run is evidence for a framework problem, not a request for a special-case
browser fix. The solution must improve self-evolve for arbitrary skills and
trajectory datasets. The command below is a black-box acceptance case only:

```bash
aworld-cli optimize \
  --from-trajectory ~/Documents/trajectory.log \
  --apply auto_verified \
  --judge-agent ~/Documents/agent.md \
  --judge-timeout 600
```

No production branch may depend on the target being `agent-browser`, on Browser or
CDP semantics, on URLs or ports from this dataset, on trajectory identifiers, or on
the contents of the configured judge agent.

## Goal

Provide a general, contract-driven self-evolve loop that can turn a trajectory
dataset into validated multi-file skill candidates, let candidates publish any
required skill-owned capabilities through registered protocols, compare baseline
and candidate rollouts in isolated AWorld tasks, use typed failures for bounded
improvement iterations, and atomically auto-apply only a genuinely verified
candidate.

The acceptance run must finish with the actual self-evolve status `succeeded`, an
accepted post-apply result, and a changed target skill package. If an external model,
runner, replay service, or judge prevents a comparable evaluation, the run must end
as `failed`, not as a quality rejection and never as a fabricated success.

## Hard Boundaries

The implementation must obey these constraints:

1. `aworld/self_evolve` owns generic dataset compilation, candidate protocols,
   capability contract discovery, deterministic orchestration, gates, provenance,
   lineage, telemetry, and apply/rollback.
2. Domain behavior remains skill-owned. The framework never implements a browser,
   CDP, website, filesystem-domain, or service-specific replay adapter.
3. The acceptance dataset is input data. It does not authorize target-specific
   branches, fixture constants, judge-specific wording, lowered gates, or fallback
   application of an unverified candidate.
4. Historical source trajectories are generation evidence, not candidate scores.
   The replay baseline and replay candidate still start from the same frozen initial
   state and remain the comparable evaluation pair.
5. Infrastructure failures, candidate protocol failures, and genuine evaluation
   rejections remain distinct outcomes.
6. The change extends AWorld through opt-in APIs and extension classes. It does not
   replace the global Agent, Context, Memory, Runner, hook, or model systems.

## AWorld Framework Reuse

The implementation should add self-evolve domain logic only where AWorld has no
equivalent. Existing framework capabilities remain the execution primitives:

| Concern | Required AWorld capability |
| --- | --- |
| Candidate and repair model calls | `CandidateGenerationAgent` extending `PromptBudgetedAgent` |
| Model configuration | Resolved `ModelConfig` injected by the caller |
| Prompt limit | `BudgetedPromptAssemblyProvider` and `PromptBudgetPolicy` |
| Execution | `Task.runner_cls`, `Runners.run_task()`, and self-evolve Runner extensions |
| Local state isolation | `LocalIsolatedApplicationContext` and context-local in-memory resources |
| Bounded parallelism | `DeterministicTaskBatchExecutor` and `TaskResourceClaim` |
| Lifecycle and accounting | Existing Runner hooks, task responses, and model usage records |
| Extensibility | AWorld `Factory`-style registration for capability contract providers |
| Apply safety | Existing overlay, package fingerprint, apply journal, backup, rollback, and registry refresh |

Self-evolve must not introduce a second event loop, model client, context store,
prompt-budget engine, agent scheduler, or hook system. A small self-evolve-specific
compiler may prepare typed domain input for these framework APIs; it must not
duplicate their runtime responsibilities.

## Chosen Approach

Use a canonical candidate package protocol, deterministic response normalization,
registered capability authoring contracts, registered candidate validators, and a
bounded typed-feedback evolution loop.

Rejected alternatives:

- Only accepting the observed nested response would pass candidate generation but
  would still leave the model without enough information to publish a valid
  skill-owned capability.
- Provider-native structured output alone is not portable across the configured
  OpenAI-compatible providers and cannot replace candidate package validation.
- Adding a framework-owned adapter template for the acceptance dataset violates the
  skill-owned boundary and would make a passing evaluation non-attributable.
- Relaxing replay, judge, held-out, or post-apply gates would produce `succeeded`
  without proving an improved skill.

## Architecture

### 1. Evolution Context Compiler

Introduce a typed `EvolutionContext` constructed from the existing optimizer request
and dataset artifacts. It contains only bounded, provenance-bearing fields:

- target identity, current package inventory, and target fingerprint;
- trainable cases and source trajectory evidence summaries;
- normalized lesson records and prior validation feedback;
- preserved behaviors and required behavior deltas;
- replay or other external capability requirements;
- applicable capability authoring contracts;
- canonical candidate output contract;
- validation and acceptance constraints.

The compiler replaces long prompt branches of the form “if feedback mentions this
string, emit this wording.” Existing lesson extraction and feedback normalization
remain the source of truth. The compiler converts them into typed signals such as
`observed_failures`, `required_behaviors`, `preserved_behaviors`,
`capability_requirements`, and `acceptance_constraints`.

`EvolutionContext` is self-evolve domain state. It is serialized as the current Task
input and passed through the standard AWorld prompt assembly path. It does not own
memory retrieval, token counting, prompt reduction, model invocation, or retries.

### 2. Canonical Candidate Package Protocol

Define a versioned canonical model response with fields at the top level:

```json
{
  "schema_version": "aworld.self_evolve.candidate.v1",
  "content": "optional complete primary target content",
  "patch_intent": {"operations": []},
  "rationale": "bounded explanation",
  "files": []
}
```

Exactly one of `content` or `patch_intent` is required. `files` is always an array.
Population strategy and lineage metadata remain framework-owned and are not trusted
from model output.

The mutation prompt calls this object `expected_output` and explicitly says to return
its value, not an object containing an `expected_output` or
`candidate_output_contract` property.

The normalizer accepts these deterministic variations:

- the canonical top-level object;
- the legacy envelope whose candidate fields are under
  `candidate_output_contract`;
- a single Markdown JSON fence;
- one bounded JSON object surrounded by non-JSON explanation.

If direct and envelope fields conflict, normalization fails. The normalizer never
merges conflicting candidate content, executes model text, or accepts multiple JSON
objects. Patch intent and candidate files still pass existing package validators.

### 3. Compact Protocol Repair

Local normalization happens before any repair model call. A repair Task is permitted
only when the response cannot be deterministically normalized or validated.

The repair Task uses the same slot's `CandidateGenerationAgent`, standard AWorld Task
lifecycle, isolated local Context, model configuration, prompt budget, output limit,
and one-attempt policy. Its input contains only:

- the canonical candidate schema;
- bounded validation codes and field paths;
- a bounded copy of the invalid response.

It does not resend the full `EvolutionContext`. Repair corrects representation; it
does not invent new trajectory evidence. One slot receives at most one repair Task.

### 4. Registered Capability Authoring Contracts

Add a self-evolve capability-contract provider protocol registered through an AWorld
`Factory`-style registry:

```python
class CandidateCapabilityContractProvider(Protocol):
    capability_type: str

    def applies_to(self, requirements: Sequence[object]) -> bool: ...
    def authoring_contract(self, requirements: Sequence[object]) -> Mapping[str, object]: ...
    def validate_candidate(self, candidate: CandidateVariant) -> CapabilityValidationResult: ...
```

Providers return schemas, supported requirement kinds, package paths, protocol
versions, compile interfaces, and validation constraints. They do not return a
domain implementation or a prebuilt candidate patch.

The existing skill-owned Replay Capability becomes the first provider. Its
authoring contract is derived from the protocol constants and parsers already in
`replay_capability.py`, including:

- manifest path and schema/protocol versions;
- supported generic requirement kinds;
- compiler `--request` and `--output` interface;
- request and result field contracts;
- evidence provenance, fixture, service, determinism, and concurrency constraints.

The contract contains no Browser/CDP or target-specific implementation. The
candidate decides whether and how its domain can satisfy the generic requirements
and writes the resulting manifest/compiler/runtime files into its own package.

### 5. Registered Candidate Validation

Candidate validation is staged and attributable:

1. canonical response validation;
2. patch and file-delta validation;
3. materialized overlay/package validation;
4. applicable capability manifest validation;
5. deterministic capability compile and freeze;
6. isolated replay and evaluation gates;
7. post-apply load and registry validation.

Each validator emits bounded typed diagnostics with a stable code, stage, field or
package path, and repairability classification. Diagnostics never include prompt
contents, credentials, fixture contents, or dependency values.

Capability validators are registered; `SelfEvolveRunner` does not switch on target
or domain names. Existing replay discovery, double compilation, fingerprinting,
freezing, service startup, overlay, and provenance checks remain authoritative.

### 6. General Candidate Population Strategies

Population diversity uses reusable strategy dimensions:

- `minimal_behavior_delta`;
- `missing_capability_completion`;
- `quality_regression_repair`;
- `efficiency_and_robustness`.

The strategy planner selects applicable dimensions from typed `EvolutionContext`
signals. When no capability is required, capability completion is omitted. When a
capability is required, at least one population slot prioritizes satisfying its
registered authoring contract. Strategies never name a target, tool, URL, endpoint,
or judge rubric.

Slots remain standard AWorld Tasks with one Agent and isolated Context per slot.
`DeterministicTaskBatchExecutor` preserves concurrency limits, stable reduction, and
indexed fail-fast behavior.

### 7. Bounded Typed-Feedback Iteration

The deterministic stage order remains:

```text
dataset
  -> evolution context
  -> candidate population
  -> package and capability validation
  -> isolated replay
  -> evaluation and gates
  -> typed feedback
  -> optional next iteration
  -> auto-apply and post-apply validation
```

When no explicit iteration option is supplied, `auto_verified` has a total budget of
two iterations: one initial iteration plus at most one additional iteration when the
surviving failures are actionable candidate failures, such as protocol, package,
missing capability, capability compilation, replay adaptation, or evaluator
feedback with explicit required behaviors. Proposal mode keeps its existing total
budget of one iteration. When an iteration option is supplied, its value is the
exact maximum total iteration count; the framework never silently exceeds it.

Infrastructure failure does not become candidate feedback and is not retried through
the evolution loop. A second iteration receives only typed diagnostics and prior
candidate lineage, not held-out data or unbounded raw logs.

### 8. Status Semantics

Final status follows these rules:

- `succeeded`: a selected candidate passed every required gate, auto-apply completed,
  and post-apply verification accepted the installed package;
- `rejected`: at least one model-generated candidate reached a deterministic
  candidate-owned result, but no candidate passed the required protocol, package,
  capability, quality, safety, replay, or verification gates;
- `failed`: infrastructure prevented every candidate from reaching a deterministic
  candidate-owned result or comparable evaluation. Examples include model access,
  Runner execution, replay service, and judge infrastructure failures.

A candidate protocol or package defect is a candidate rejection after its bounded
repair/iteration budget, even though that candidate cannot reach replay. A provider
connection or timeout is an infrastructure failure. In a mixed population, a
deterministic candidate rejection remains attributable and may produce `rejected`;
the run is `failed` only when infrastructure leaves no deterministic candidate
outcome and no comparable evaluation.

### 9. Telemetry and Reports

Candidate population reports must distinguish logical candidates from physical model
tasks. Required bounded counters include:

- initial slot count;
- repair attempt, success, protocol-invalid, and infrastructure-failure counts;
- configured, effective, and maximum observed concurrency;
- failure cutoff and cancellation counts;
- initial and repair queue/execution/wall time;
- complete initial and repair token usage;
- candidate validation stage counts and typed failure codes.

Add bounded usage metadata to `TaskBatchResult` so a completed response that is later
logically discarded by indexed fail-fast retains accounting without retaining or
reporting its answer. Stage aggregation uses that metadata rather than response
content.

Release and content-quality checklists use `not_run` when no candidate reached their
stage. They must not report `passed` solely because every check was skipped.

## Data Flow and Ownership

1. The CLI resolves `.env` and options into `ModelConfig` and judge configuration.
2. The CLI injects resolved objects into self-evolve; the framework remains CLI-free.
3. Dataset loading, target inference, splitting, and trajectory context reconstruction
   produce the existing self-evolve dataset.
4. The Evolution Context Compiler builds typed candidate input and queries registered
   capability contract providers.
5. CandidateGenerationAgent Tasks produce normalized candidate packages.
6. Existing overlay and capability machinery validates candidate-owned files.
7. Baseline and candidate replay repetitions use identical frozen adaptation and
   isolated AWorld Tasks.
8. Existing evaluation Tasks and judge configuration produce comparable summaries.
9. Deterministic gates select or reject candidates. An actionable typed failure may
   feed one bounded next iteration.
10. Existing apply journal, backup, rollback, normalization, and registry refresh
    atomically publish only a verified candidate.

## Error Handling

- Model response representation errors use stable candidate protocol diagnostics.
- Candidate code and manifests are never imported into the self-evolve process.
- Capability compilation remains a bounded subprocess and is run twice for
  determinism.
- Provider exceptions retain a safe category and root exception type. Reports omit
  credentials and request content.
- Infrastructure failures cancel pending higher-index work through the existing
  batch executor and terminate with `failed` if no comparable candidate survives.
- Apply or post-apply failure uses the existing rollback path and cannot end as
  `succeeded`.

## Testing Strategy

All production changes follow red-green TDD.

### Protocol tests

- canonical direct candidate output;
- legacy `candidate_output_contract` envelope;
- fenced JSON and one JSON object with bounded surrounding prose;
- conflicting direct/envelope fields;
- multiple JSON objects and malformed package fields;
- compact repair excludes the original EvolutionContext and respects token limits.

### Capability contract tests

- registration and applicability through the AWorld-style registry;
- replay authoring contract is derived from current protocol constants;
- no domain or target identifiers occur in the framework contract;
- a generated generic fixture candidate can discover, compile twice, freeze, and bind;
- invalid manifests and compiler results produce typed actionable diagnostics.

### Orchestration tests

- normalized outputs avoid unnecessary repair calls;
- repair uses the same slot Agent in a new isolated Task;
- capability-focused population is selected from requirements, not target names;
- a typed capability failure feeds one bounded next iteration;
- infrastructure failure ends as `failed` when no comparable candidate survives;
- genuine completed gate failure ends as `rejected`;
- verified candidate ends as `succeeded` and is atomically applied;
- explicit serial policy remains a rollback path.

### Telemetry tests

- initial and repair tokens are both included;
- logically discarded completed work retains usage but not answer content;
- repair attempt and success counts are distinct;
- queue, execution, elapsed, cancellation, and failure cutoff are accurate;
- skipped release checks report `not_run`.

### Regression and acceptance

Run the existing AWorld Runner, Context, Memory, Agent, hook, self-evolve, and CLI
optimize suites. Then run the real acceptance command. Acceptance requires:

- CLI and report status `succeeded`;
- a non-null selected candidate;
- all blocking gates passed;
- comparable baseline/candidate replay provenance;
- held-out and judge verification passed;
- `post_apply.status == "accepted"`;
- the installed skill package fingerprint equals the verified candidate package;
- the installed package is loadable after registry refresh.

If the live run is blocked solely by an external dependency, the implementation is
not declared end-to-end complete. The report must show `failed` with a bounded
infrastructure diagnostic, and the same acceptance command is rerun after the
dependency recovers.

## Non-Goals

- Adding an adapter or skill implementation for the acceptance target.
- Encoding a solution from the source trajectories into framework code.
- Replacing AWorld Agent, Context, Memory, Runner, Factory, hook, or model systems.
- Guaranteeing a particular remote model response.
- Applying a candidate that did not pass replay, evaluation, held-out, and post-apply
  validation.
- Making non-self-evolve agents opt into the new candidate or capability contracts.
- Redesigning the general trajectory strategy warning path; it is non-causal for the
  identified rejection and can be handled independently.

## Repair Conformance Extension

A repair iteration must not trust the model rationale. The focused failed candidate
package and its typed validation evidence are compiled into a candidate-specific
`RepairConformanceContract` before generation. The bounded contract records:

- the failed candidate package fingerprint and the replay implementation paths declared
  by its capability manifest;
- the typed failure codes and interaction frontier;
- an exact failed probe kind, path, and expected fixture-derived response token when
  available;
- the bounded late observed operation names for a progressing task-plane timeout; and
- whether a non-empty fixture-derived data-plane probe is mandatory.

The generated package passes a two-stage conformance check before any task rollout:

1. Static source conformance verifies that the candidate materially changes the focused
   replay implementation branch (or redirects the manifest to a new non-empty runtime),
   rather than only changing its rationale or unrelated files.
2. Dynamic conformance compiles and freezes the candidate-owned capability using the
   existing replay compiler, verifies that its declared probes satisfy the exact or late
  operation constraints, and starts the isolated replay service so those declared probes
  execute immediately. An exact probe's previous expected preview is bounded diagnostic
  evidence, not an allowed hard-coded repair value: when it is not a recorded scalar value,
  the repaired compiler may replace it with a value-derived fixture leaf.

For a task-plane timeout, at least one declared probe must cover a late observed operation
and assert a non-empty `response_contains` value already proven by the replay capability
validator to occur in the selected recorded fixture. Operation names and opaque request
text remain protocol-neutral evidence; the framework does not implement Browser/CDP or
any other domain adapter. Recursive fixture interpretation and protocol-valid response
construction remain candidate/skill-owned behavior. Mapping keys, raw-byte regex matches,
placeholder literals, and hard-coded diagnostic previews do not count as recorded values.
For JSON request/response probes, dynamic conformance also requires matching correlation,
a non-error non-empty result, and the selected recorded value inside that result rather
than in unrelated envelope metadata.

Conformance failure is a typed, repairable candidate failure and feeds the next bounded
repair iteration. It never starts baseline/candidate Tasks, so rationale-only or
readiness-only candidates are normally rejected in seconds instead of consuming the
task-plane timeout.
