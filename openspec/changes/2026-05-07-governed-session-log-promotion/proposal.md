# Proposal

## Why

`2026-04-28-aworld-cli-memory-hybrid-provider` deliberately stopped at a
session-log-first memory MVP plus the `llm_calls` truth-source /
cache-observability precondition milestone.

That branch now has the right raw ingredients:

- append-only workspace session logs
- extracted promotion candidates
- append-only `llm_calls` truth records with `request_id` linkage
- basic promotion outcome counters

What it does **not** yet have is governed promotion:

- auto-promotion is still an env-flagged heuristic path
- durable promotion is not anchored to a persisted session-log candidate record
- decision explanations are too thin for review and correction workflows
- current metrics count decisions, but do not measure promotion quality
- rollout has no explicit precision / pollution threshold

That means the current branch can persist candidates, but it cannot yet claim
that workspace durable memory is promoted by policy.

## Scope

This change introduces governed promotion from workspace session logs into
durable memory.

In scope:

- session-log-backed promotion candidate identity and decision records
- governed promotion modes: `off`, `shadow`, `governed`
- explainable promotion decisions with stable source linkage
- minimal operator review / reject / revert workflow
- promotion quality metrics, including precision and pollution proxies
- rollout thresholds that block default broad auto-promotion until quality is
  proven

Out of scope:

- durable-memory taxonomy expansion beyond current memory types
- prompt-cache optimization policy or provider-specific cache strategy
- trajectory semantic changes
- broad runtime-memory rewrite
- background batch promotion, timed promotion, or sync protocols

## Outcome

After this change:

- every promotable memory candidate is governed from a persisted session-log
  source, not a transient turn-end heuristic only
- automatic durable promotion becomes policy-driven and explainable
- uncertain or unsafe candidates stay out of active durable memory
- operators can inspect and correct governed promotions without mutating the
  append-only source history
- rollout to default auto-promotion remains blocked until measured precision
  and pollution thresholds are met
