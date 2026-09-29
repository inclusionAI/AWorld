# Execution Semantics

Use this reference when establishing or revising the long-running task's
charter and rolling plan.

## Keep four concepts distinct

### Charter

The charter is a compact interpretation of the user's current request. It
contains the objective, explicit constraints, non-goals when they affect
decisions, and the evidence expected for completion. It may evolve as ambiguity
is resolved, but it must not silently expand the user's scope.

### Milestone

A milestone describes the next meaningful outcome, not a list of every command.
Keep one milestone active. A milestone is complete only when observations
support its outcome; executing all planned actions is not sufficient.

### Rolling plan

The rolling plan contains the next one to three bounded actions, their purpose,
and the observation each is expected to produce. Replace it when its underlying
assumption is disproved or when a more informative action becomes available.

### Evidence

Evidence is an observation returned by an actual tool interaction. Keep its
meaning narrow:

- `observed`: directly present in a tool result;
- `inferred`: a conclusion drawn from one or more observations;
- `claimed`: planned, stated, or reviewed but not observed;
- `unknown`: not yet supported or contradicted.

Never relabel a claim as observed merely because it appears in a plan, summary,
or review.

## Prefer evidence-producing actions

A useful next action should do at least one of the following:

- test a material assumption;
- expose the cause of a contradiction;
- create inspectable progress toward the active milestone;
- distinguish between plausible strategies;
- confirm that a recent change had the intended effect.

When several actions are possible, prefer the smallest action that can change
the current decision. Avoid repeating an action whose operation and result are
already represented in the recovered state unless a relevant condition changed.

## Replan without thrashing

Replan when observations contradict the active approach, progress no longer
produces new information, a key assumption is disproved, or the runtime requests
a checkpoint. A replan should state:

- what was learned;
- which assumption or approach is retired;
- which milestone remains active or replaces it;
- what bounded action will produce the next decision-relevant observation.

Do not rewrite the entire charter merely to make progress look new. Preserve
confirmed facts and successful intermediate results across plan revisions.
