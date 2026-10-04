---
name: long-running-agent
description: Organize extended, multi-stage AWorld tasks with a rolling charter, evidence-backed checkpoints, context recovery, and a bounded final review. Use when a task is expected to require sustained tool use, several dependent milestones, or repeated replanning; do not use for short, direct tasks.
default_enabled: true
execution_assets:
  - references/execution-semantics.md
  - references/checkpoints-and-review.md
---

# Long-Running Agent

Use this skill to keep a long task coherent without turning its initial plan into
an inflexible script. The AWorld framework inside the wheel owns protocol state,
review scheduling, and repair policy while consuming the caller-provided task
deadline and capabilities. This skill guides how to plan and reason when those
capabilities are enabled; the outer harness does not select those task
strategies.

Read [references/execution-semantics.md](./references/execution-semantics.md) when
creating or revising the charter and rolling plan. Read
[references/checkpoints-and-review.md](./references/checkpoints-and-review.md)
when responding to a checkpoint, recovering context, or reviewing a candidate
final result.

## Bypass short work

For a direct task that can be completed and checked in a few actions, execute it
normally. Do not manufacture a charter, milestones, or checkpoints merely
because this skill is enabled. Adopt the long-running workflow only when the
AWorld execution engine requests a checkpoint or final review, or when sustained
dependent work actually needs a recoverable rolling plan.

## Declare a long horizon without a separate turn

When the first actual Tool call exposes the optional
`__aworld_execution_profile` parameter, include it when the current plan
credibly needs multiple dependent milestones or at least six Tool actions.
Include the profile in the same real tool call that you already need; the
AWorld removes it before the Tool executes. Omit it when the work is direct or
the horizon is genuinely uncertain. Do not make a separate Tool call merely to
classify the task, and do not delay useful work to produce the profile.

Set `horizon` to `long` only for sustained dependent work, such as multiple
milestones or an expected sequence of at least several Tool actions. Report a
bounded estimate through `confidence`, `milestone_count`,
`expected_tool_actions`, and `verification_required`. Use `short` or omit the
profile when the work is direct or uncertain. This is an advisory planning
assessment and is not evidence of completion. Tool counts and stagnation never
silently override an explicit `short` decision. They may request that you
reassess the horizon; only your later structured plan update may change it.
The deadline reserve is a mechanical stop boundary, not a long-task
classification.

## Establish a rolling charter

Restate the requested outcome as a compact working charter:

- the objective and explicit constraints;
- the current milestone and its intended outcome;
- open assumptions or uncertainties;
- observable evidence that would support completion.

Treat the charter and plan as working claims, not established facts. Preserve
the user's scope and revise the plan when observations disprove an assumption.
Do not spend the opening of the task designing every later step.

## Work in bounded steps

Maintain only the next one to three useful actions. For each action, know what
new observation would justify continuing, revising the plan, or abandoning the
approach. Prefer actions that reduce uncertainty or produce inspectable progress
over broad exploration.

When the request names a concrete deliverable or observable state change,
do not reserve all writes for the end of the investigation. Once the core format
and safety constraints are known, create the smallest inspectable candidate and
persist useful calculations or scripts. Refine that candidate as later evidence
arrives. A draft is not completion evidence, but it prevents repeated analysis
from consuming the task without producing anything that can be checked.

At a meaningful milestone boundary or a framework-requested checkpoint:

1. Compare the expected outcome with actual tool observations.
2. Separate confirmed observations from interpretations and unresolved claims.
3. Retire disproved assumptions and record approaches that should not be
   repeated without new evidence.
4. Update the current milestone and choose the next bounded action.

When a real Tool schema exposes `__aworld_plan_update`, attach a bounded update
to that Tool call when the checkpoint would materially help recovery or change
the plan. Set `decision` to `continue` when the current approach remains sound,
or `replan` when evidence justifies changing it. Record the current horizon,
milestone, next action, verification plan, completion assessment, assumptions,
retired approaches, evidence references, and selected candidate id. AWorld
removes this object before dispatch. Reading checkpoint guidance alone does not
count as replanning; the structured update records your actual decision.

Do not create a checkpoint after every tool call. Let AWorld record
low-level events; keep the semantic checkpoint small enough to survive context
pressure.

## Preserve evidence discipline

Only tool observations establish external state. Plans, summaries, reviewer
opinions, and your own statements remain claims until supported by an
observation.

Before an action can mutate, consume, delete, or replace the only copy of
important input or evidence, prefer a read-only inspection or create a
recoverable checkpoint. Treat commands and APIs by their possible effects, not
their names: an operation described as a read may still trigger implicit state
changes. If recovery is unavailable, make the risk explicit and choose the
smallest action that can resolve the current uncertainty.

After a state-changing action, prefer fresh evidence before relying on an older
result. A successful command status proves that command completed; it does not
by itself prove the broader objective. When evidence is partial, state exactly
what it supports instead of promoting it to full completion.

When a Tool schema exposes `__aworld_public_probe`, you may attach a
model-designed smoke, regression, invariant, or counterexample probe to the
real Tool call that executes it. State the hypothesis and highest-risk
counterexample. AWorld binds the separately observed result to the public
request and current candidate in the evidence ledger. The receipt is local
self-check evidence only; it is never hidden-grader evidence, canonical
acceptance, or benchmark reward. Rerun a probe after later candidate or
artifact changes make its receipt stale.

## Recover before continuing

After context loss, compaction, interruption, or handoff, first recover the
charter, current milestone, latest observations, retired approaches, unresolved
assumptions, and any review feedback supplied by AWorld. Resume from that
state rather than reconstructing the task from memory or repeating prior work.

When the runtime supplies a structured previous-attempt receipt, use its
acceptance disposition and unsatisfied evidence codes to select the next
bounded action. Treat an iteration or attempt limit only as a resource halt,
never as proof that the objective succeeded. Do not repeat a completion claim
when the receipt says verification failed or evidence is missing; produce new
evidence or report the unresolved gap accurately.

## Review before declaring completion

When preparing a candidate final result:

1. Reconcile every public requirement in the charter with current evidence.
2. Check whether later changes made earlier evidence stale.
3. Identify direct contradictions and material evidence gaps.
4. When a fresh-context `verifier` collaborator is actually available and an
   independent check could change the completion decision, delegate the public
   objective, candidate locations, and concrete claims to check. Treat its
   report as advice, not hidden-grader evidence or completion authority.
5. When comparison or implementation can be safely isolated, use the
   fresh-context `evaluator` or `developer` collaborator only if its result can
   materially change the next decision. Give it a bounded public subtask and
   integrate its answer; availability never requires delegation.
6. If a candidate-management Workbench is available and the task has multiple
   valuable artifact candidates, use it to snapshot, validate, compare, and
   publish the selected candidate. Do not manufacture a Workbench contract for
   direct work. Its receipts remain agent self-check evidence.
7. If a concrete gap can be resolved within the remaining budget, resume the
   normal bounded-step workflow, use the Tools needed to finish it, and
   reassess before producing a new candidate final result.
8. Otherwise return the best current result and describe material uncertainty
   accurately.

Keep the review itself bounded. A model decision that work is incomplete may
resume normal execution under the original task deadline and step budget; it
does not grant a new budget or permission for open-ended exploration. Do not
manufacture evidence to make the result appear complete.

## Fail open

An inconclusive review, unavailable reviewer, protocol error, or exhausted
review budget does not erase useful work and does not justify converting the
task into a new failure. Preserve the best current result, report known limits,
and let the caller apply its own acceptance criteria.
