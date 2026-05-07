## 1. Scope Freeze

- [ ] 1.1 Confirm this change is a new follow-up to
  `2026-04-28-aworld-cli-memory-hybrid-provider`, not an extension of that
  change.
- [ ] 1.2 Confirm scope stays limited to governed session-log promotion,
  explainability, quality metrics, and rollout thresholds.
- [ ] 1.3 Confirm taxonomy expansion, cache strategy, trajectory changes, and
  runtime-memory rewrite remain out of scope.

## 2. Promotion Source And Policy

- [ ] 2.1 Add stable candidate identity for promotable session-log records.
- [ ] 2.2 Replace boolean auto-promotion control with governance modes:
  `off`, `shadow`, `governed`.
- [ ] 2.3 Extend promotion decisions to support explicit outcomes:
  `durable_memory`, `session_log_only`, `rejected`.
- [ ] 2.4 Require explanation payloads and source linkage for every decision.
- [ ] 2.5 Add duplicate / temporary / eligibility gates before governed
  promotion.

## 3. Review And Correction

- [ ] 3.1 Add append-only review records keyed by `decision_id`.
- [ ] 3.2 Add minimal operator surfaces for listing, accepting, rejecting, and
  reverting governed promotions.
- [ ] 3.3 Ensure reverted promotions are excluded from active durable recall
  without deleting historical source records.

## 4. Metrics And Rollout

- [ ] 4.1 Extend promotion metrics beyond raw counts to include reviewed,
  confirmed, reverted, and pending-review states.
- [ ] 4.2 Compute precision and pollution proxies from explicit review labels.
- [ ] 4.3 Report governance mode and rollout-threshold readiness through the
  memory command surface.
- [ ] 4.4 Keep default mode at `shadow` until rollout thresholds are met.

## 5. Validation

- [ ] 5.1 Add unit coverage for policy modes, outcomes, explanation payloads,
  and review labels.
- [ ] 5.2 Add hook/provider integration coverage for shadow and governed flows.
- [ ] 5.3 Add command coverage for review/status surfaces.
- [ ] 5.4 Add acceptance coverage proving explicit `/remember` writes still
  bypass governed auto-promotion gates.
- [ ] 5.5 Add regression coverage proving runtime message-memory behavior is
  unchanged under the hybrid provider path.

## 6. Implementation Guardrails

- [ ] 6.1 Keep governed-promotion policy inside CLI durable-memory modules and
  plugin surfaces.
- [ ] 6.2 Keep bootstrap and prompt-neuron changes limited to narrow adapter
  work only.
- [ ] 6.3 Do not modify `AworldMemory` runtime message-memory semantics as part
  of this change.
