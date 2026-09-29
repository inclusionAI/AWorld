# Checkpoints and Review

Use this reference at semantic checkpoints, after context recovery, and before
returning a candidate final result.

## Checkpoint contract

A useful checkpoint is concise and decision-oriented. It should recover:

- the current objective and milestone;
- confirmed observations relevant to that milestone;
- unresolved claims and assumptions;
- contradictions or blockers;
- retired approaches and why they were retired;
- the next bounded action and its expected evidence.

Do not copy raw command history into a checkpoint. Retain identifiers or short
summaries sufficient to locate important observations when the runtime makes
them available. Do not invent timestamps, statuses, or evidence that the
runtime did not supply.

## Recovery contract

On recovery, use the latest supplied checkpoint as an index, then reconcile it
with newer observations and feedback. Resolve conflicts in this order:

1. newer direct observations;
2. older direct observations that have not been invalidated;
3. inferences supported by those observations;
4. plan, checkpoint, and review claims.

If required state is missing, mark it unknown and choose a bounded action that
can recover it. Do not repeat a costly prior attempt solely because its details
were compacted.

## Final review contract

Review the candidate result against the public charter and return one of these
semantic outcomes:

- **ready**: current evidence supports the material completion claims;
- **repairable**: a specific observed contradiction or material evidence gap
  can be addressed by a bounded action;
- **uncertain**: evidence is incomplete, ambiguous, unavailable, or the review
  itself could not complete.

For `repairable`, identify the supporting observation, the affected claim, and
one bounded next action. Do not request broad reimplementation or indefinite
investigation at the final boundary. For `uncertain`, preserve the candidate
result and describe the uncertainty; absence of proof is not automatically a
contradiction.

After a repair action, reassess only the affected claims plus anything the
repair may have invalidated. Reuse still-current evidence rather than restarting
the task.

## Fail-open contract

The semantic review advises execution; it is not an authority that may discard
the current result. If review is unavailable, inconclusive, contradictory, or
out of budget:

- preserve completed work and direct observations;
- avoid additional unbounded actions;
- return the best current result with accurate limitations;
- do not reinterpret internal uncertainty as an external execution failure.
