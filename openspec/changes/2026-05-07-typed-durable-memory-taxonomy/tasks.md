## 1. Scope Freeze

- [x] 1.1 Confirm this change is a new follow-up to
  `2026-04-28-aworld-cli-memory-hybrid-provider` and
  `2026-05-07-governed-session-log-promotion`.
- [x] 1.2 Confirm scope stays limited to typed durable-memory taxonomy,
  kind-aware recall / injection rules, and compatibility behavior.
- [x] 1.3 Confirm runtime-memory rewrite, trajectory changes, cache strategy,
  HUD expansion, and instruction-surface expansion remain out of scope.

## 2. Taxonomy Model

- [x] 2.1 Add additive `memory_kind` support for new durable records and
  governed decisions.
- [x] 2.2 Define canonical kinds: `preference`, `constraint`, `workflow`,
  `fact`, `reference`.
- [x] 2.3 Preserve compatibility behavior for existing records that do not
  carry `memory_kind`.
- [x] 2.4 Keep legacy `memory_type` reads stable while making typed kinds the
  preferred semantic surface for new writes.

## 3. Kind-Aware Behavior

- [x] 3.1 Define which kinds are instruction-eligible versus recall-only.
- [x] 3.2 Add kind-aware relevant recall behavior for durable memory.
- [x] 3.3 Add kind-aware prompt injection rules without forcing facts or
  references into standing instruction text.
- [x] 3.4 Refine governed promotion eligibility to consider `memory_kind`
  alongside existing safety gates.

## 4. Operator Surface And Compatibility

- [x] 4.1 Extend `/remember` to accept typed durable-memory writes.
- [x] 4.2 Expose typed durable-memory information in `/memory view`,
  `/memory status`, and promotion listing surfaces.
- [x] 4.3 Keep existing command usage and legacy durable-memory records working
  without a required migration step.

## 5. Validation

- [x] 5.1 Add unit coverage for taxonomy normalization and compatibility reads.
- [x] 5.2 Add recall/injection coverage proving kind-aware behavior.
- [x] 5.3 Add command coverage for typed explicit writes and typed inspection
  surfaces.
- [x] 5.4 Add governed-promotion coverage proving `memory_kind` influences
  eligibility without breaking legacy behavior.
- [x] 5.5 Add regression coverage proving runtime message-memory behavior is
  unchanged.

## 6. Implementation Guardrails

- [x] 6.1 Keep taxonomy logic inside CLI durable-memory modules and plugin
  surfaces.
- [x] 6.2 Avoid broad rewriting of historical durable-memory files.
- [x] 6.3 Do not change `AworldMemory`, `llm_calls`, or trajectory semantics as
  part of this change.
