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
an inflexible script. The AWorld runtime owns protocol state, budgets, review
scheduling, and safety. This skill guides how to plan and reason when those
capabilities are enabled.

Read [references/execution-semantics.md](./references/execution-semantics.md) when
creating or revising the charter and rolling plan. Read
[references/checkpoints-and-review.md](./references/checkpoints-and-review.md)
when responding to a checkpoint, recovering context, or reviewing a candidate
final result.

## Bypass short work

For a direct task that can be completed and checked in a few actions, execute it
normally. Do not manufacture a charter, milestones, or checkpoints merely
because this skill is enabled. Adopt the long-running workflow only when the
runtime requests a checkpoint or final review, or when sustained dependent work
actually needs a recoverable rolling plan.

## Declare a long horizon without a separate turn

When an actual Tool call exposes the optional
`__aworld_execution_profile` parameter, use it only when you can make a
high-confidence estimate from the task and current plan. Include the profile in
the same real tool call that you already need; the runtime removes it before
the Tool executes. Do not make a separate Tool call merely to classify the
task, and do not delay useful work to produce the profile.

Set `horizon` to `long` only for sustained dependent work, such as multiple
milestones or an expected sequence of at least several Tool actions. Report a
bounded estimate through `confidence`, `milestone_count`,
`expected_tool_actions`, and `verification_required`. Use `short` or omit the
profile when the work is direct or uncertain. This is an advisory planning
assessment: the runtime may still arm from observed execution, stagnation, or
the deadline reserve, and your declaration is not evidence of completion.

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

At a meaningful milestone boundary or a framework-requested checkpoint:

1. Compare the expected outcome with actual tool observations.
2. Separate confirmed observations from interpretations and unresolved claims.
3. Retire disproved assumptions and record approaches that should not be
   repeated without new evidence.
4. Update the current milestone and choose the next bounded action.

Do not create a checkpoint after every tool call. Let the runtime record
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

## Recover before continuing

After context loss, compaction, interruption, or handoff, first recover the
charter, current milestone, latest observations, retired approaches, unresolved
assumptions, and any review feedback supplied by the runtime. Resume from that
state rather than reconstructing the task from memory or repeating prior work.

## Review before declaring completion

When preparing a candidate final result:

1. Reconcile every public requirement in the charter with current evidence.
2. Check whether later changes made earlier evidence stale.
3. Identify direct contradictions and material evidence gaps.
4. If a concrete gap can be resolved by one bounded action within the remaining
   budget, perform it and reassess.
5. Otherwise return the best current result and describe material uncertainty
   accurately.

Keep review bounded. Do not restart open-ended exploration from the completion
boundary, and do not manufacture evidence to make the result appear complete.

## Fail open

An inconclusive review, unavailable reviewer, protocol error, or exhausted
review budget does not erase useful work and does not justify converting the
task into a new failure. Preserve the best current result, report known limits,
and let the caller apply its own acceptance criteria.
