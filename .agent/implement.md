# Subagent Workflow

## Before Starting

1. Read `.agent/goal.md`, `.agent/plans.md`, `.agent/standards.md`, and `.agent/progress.md`.
2. Work only on the assigned task and files. Preserve unrelated changes.
3. State assumptions in the report; do not expand to benchmark-specific behavior.

## Workflow

1. Inspect existing code paths and tests.
2. Add a failing test for the first required behavior.
3. Implement the smallest compatible contract.
4. Add error, timeout, cancellation, and legacy cases as appropriate.
5. Run targeted and affected subsystem tests.
6. Review the diff for architecture, privacy, and compatibility.
7. Commit one logical change if working in an isolated worktree; otherwise report the exact patch scope to the root agent.

## Non-Negotiable Rules

- Do not invent a second provider/event truth source.
- Do not infer trajectory steps from final TaskResponse text.
- Do not hard-code benchmark tasks, answers, prompts, models, or score rules.
- Do not add dependencies unless strictly necessary and justified.
- Do not weaken a hard gate to make tests pass.
- Do not expose secrets/raw sensitive Tool output in logs or fixtures.

## Report

- What changed and why
- Tests run and results
- Files changed
- Assumptions
- Risks or follow-up work

