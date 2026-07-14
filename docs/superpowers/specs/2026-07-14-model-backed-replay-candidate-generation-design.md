# Model-Backed Replay Candidate Generation Design

## Goal

Make the default `aworld-cli optimize` path capable of generating a complete
multi-file skill candidate when a trajectory requires skill-owned replay support.
The candidate may contain `SKILL.md` changes and files below `replay/`; the
self-evolve framework remains domain-neutral and does not implement Browser/CDP
behavior.

## Configuration Boundary

Mutation and judging are separate concerns.

- `aworld-cli` resolves the mutation model with the existing
  `resolve_model_profile("default")` path. The default profile may be constructed
  from the loaded `.env` configuration.
- Judge selectors and `judge_config.model_profile` affect only evaluation. They
  are never consulted when selecting the mutation model.
- The CLI injects the resolved `ModelConfig` into `optimize_from_cli_request()`.
  Code below `aworld/self_evolve` does not import `aworld-cli`.
- Evaluator-only reruns do not resolve or initialize a mutation model.
- A caller that does not inject a model retains the existing deterministic,
  text-only proposal behavior.

## Candidate Generation

`TraceReflectiveLLMMutator` continues to own the candidate contract. Its request
contains the current skill, trace evidence, trainable cases, replay requirements,
package inventory, and a structured output schema. Candidate generation runs
through a dedicated AWorld `Agent` with an isolated lightweight `Context` rather
than calling the model provider directly. This keeps model invocation, prompt
assembly, framework retries, hooks, and token accounting on the standard AWorld
path without attaching each stateless population member to global persistent
agent memory. The agent accepts one JSON object with:

- complete `content` or a `patch_intent`;
- a concise `rationale`;
- a `files` array using the existing bounded candidate-file schema.

The parser accepts raw or fenced JSON, validates the top-level shape, and retries
once with a contract-repair instruction. It does not synthesize replay files in
framework code. The candidate-generation agent enforces a bounded output-token
budget at its AWorld model-call boundary. A typed agent/model infrastructure
failure stops the remaining candidate population and records only a safe failure
code, stage, and exception type. Provider exceptions are converted before the
generic Agent terminal-diagnostic boundary, and the specialized agent suppresses
the generic `failed_requests` artifact because the self-evolve report already
owns the bounded failure record. A repeated schema failure still produces no
candidate, so the existing candidate-generation gate rejects verified application
rather than silently using a domain-specific fallback.

The existing AWorld provider/model observability policy is unchanged. Self-evolve
does not replace provider implementations or model-layer request tracing; any
framework-wide log redaction belongs at that shared framework boundary rather than
in this agent.

## Context Ownership

Self-evolve reconstructs and labels trajectory evidence but does not implement a
second context-management stack. In particular, it does not add private
summarization, truncation, or model-selection logic for repeated prior feedback
and lessons. Context lifecycle and future compression/retrieval policy belong to
the candidate-generation AWorld agent and its context configuration. Self-evolve
retains only protocol-specific normalization and sanitization needed to keep
untrusted trajectory content and secrets out of the candidate prompt and report.

## Dependency Analysis

Preflight and compilation use the same dependency analyzer. Dependency discovery
uses the current task portion of a reconstructed trajectory case; recorded prior
turns remain provenance-bearing evidence but their obsolete paths and URLs are not
treated as resources required by the new rollout.

The shared analyzer covers runtime endpoints, HTTP resources, stateful tools, and
absolute local files. Bounded current local files are snapshotted and therefore do
not become capability requirements. Missing, secret-like, or oversized files are
reported as unresolved requirements. Detected URLs are normalized to remove
sentence punctuation before requirement identity is calculated.

## Capability Gate

When non-deterministic replay requirements exist, the selected skill package must
provide `replay/capability.json` unless a trusted constructor-injected generic
adapter is registered. A missing candidate capability fails before rollout with a
`replay_capability` gate containing requirement counts, kinds, and the preflight
fingerprint. Capability contents are still compiled, frozen, and validated by the
existing replay-adaptation protocol.

## Verification

Regression coverage verifies:

- the CLI resolves only the default mutation profile, independently of judge
  options;
- evaluator-only reruns skip mutation-model resolution;
- invalid model JSON receives one repair retry and a structured multi-file
  candidate is persisted;
- candidate generation uses the AWorld agent/runtime/context path and always
  supplies an output-token limit;
- one infrastructure failure stops the remaining candidate population without
  adding raw exception text or secrets to self-evolve reports or generic Agent
  `failed_requests` artifacts;
- prior-turn paths do not appear as current local-file requirements;
- preflight and compile agree on missing current local files;
- URL punctuation does not alter dependency identity;
- missing skill-owned capability fails before rollout;
- existing generic adapters, local-file snapshots, and no-model proposal behavior
  remain compatible.
