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

`TraceReflectiveLLMMutator` continues to own the candidate contract. Its prompt
contains the current skill, trace evidence, trainable cases, replay requirements,
package inventory, and a structured output schema. The CLI-backed mutation
callable sends that prompt to the injected model and accepts one JSON object with:

- complete `content` or a `patch_intent`;
- a concise `rationale`;
- a `files` array using the existing bounded candidate-file schema.

The parser accepts raw or fenced JSON, validates the top-level shape, and retries
once with a contract-repair instruction. It does not synthesize replay files in
framework code. Model-call or repeated schema failure produces no candidate, so
the existing candidate-generation gate rejects verified application rather than
silently using a domain-specific fallback.

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
- prior-turn paths do not appear as current local-file requirements;
- preflight and compile agree on missing current local files;
- URL punctuation does not alter dependency identity;
- missing skill-owned capability fails before rollout;
- existing generic adapters, local-file snapshots, and no-model proposal behavior
  remain compatible.
