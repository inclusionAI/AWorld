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

Every execution decision includes a typed delivery choice and its rationale;
delivery debt or an approaching candidate-decision reserve makes that choice
especially urgent. The valid choices are continued exploration, candidate
production, candidate validation, current-result submission, and uncertain
submission. A continued-exploration choice should name the one
discriminating observation expected next. A candidate-production choice is not
fulfilled by an unrelated workspace mutation; rely on an inspectable public
candidate observation when one is available.

Treat a plan/action mismatch receipt as a request to reconsider the declared
choice, not as a verdict that the Tool action was useless or that the task
failed. Where the environment cannot expose candidate or validation evidence,
keep that alignment unknown rather than inventing a match.

The typed next action has an exact JSON/null invariant. For a Tool-backed
choice (`continue_exploration`, `produce_candidate`, or `validate_candidate`),
`next_action_tool` is the exact offered function name and
`next_action_arguments` is a JSON string encoding the complete argument object
for that call; both are non-null. For `submit_current` or `submit_uncertain`,
both fields are null and AWorld enters Tool-free finalization. Use
`submit_current` for a ready best-current result without falsely claiming
uncertainty. The next Tool call for a Tool-backed choice should use the same
identity and arguments. AWorld strips framework control fields, hashes the
identity and canonical JSON arguments,
and retains only the SHA-256 signature for alignment; it does not persist the
raw declared arguments in protocol records or telemetry.

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

Every candidate produced while this Skill is active receives the same final
review, even when the initial model-owned horizon choice was `short` or
`unknown`. An explicit public deliverable also participates in the existing
candidate-decision reserve without being reclassified as long-horizon work.
These are generic delivery boundaries: they do not add benchmark-specific
acceptance rules. Named-file presence is only an inspectable milestone;
content correctness still comes from a registered completion validator when
one exists, otherwise from the model-owned review with accurate uncertainty.

Review the candidate result against the public charter and return one of these
semantic outcomes:

- **ready**: current evidence supports the material completion claims;
- **repairable**: a specific observed contradiction or material evidence gap
  can be addressed within the remaining task budget;
- **uncertain**: evidence is incomplete, ambiguous, unavailable, or the review
  itself could not complete.

For `repairable`, identify the supporting observation and affected claim, then
resume the normal bounded-step workflow. Use only the Tool actions needed to
finish and verify the missing work within the original task budget; the review
does not create a second budget. Do not request broad reimplementation or
indefinite investigation at the final boundary. For `uncertain`, preserve the
candidate result and describe the uncertainty; absence of proof is not
automatically a contradiction.

After repair work, reassess only the affected claims plus anything the repair
may have invalidated. Reuse still-current evidence rather than restarting the
task.

## Fail-open contract

The semantic review advises execution; it is not an authority that may discard
the current result. If review is unavailable, inconclusive, contradictory, or
out of budget:

- preserve completed work and direct observations;
- avoid additional unbounded actions;
- return the best current result with accurate limitations;
- do not reinterpret internal uncertainty as an external execution failure.
