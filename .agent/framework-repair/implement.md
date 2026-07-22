# Framework Repair Subagent Workflow

1. Read `.agent/framework-repair/goal.md`, `plans.md`, `standards.md`, and `progress.md`.
2. Read the assigned plan completely from `plans/`.
3. Work only in the assigned git worktree and task scope.
4. Add a failing generic regression test before production changes.
5. Implement the framework abstraction; never patch a historical run symptom.
6. Run focused tests and the contract matrix available in that worktree.
7. Review the diff for cardinality branches, free-form error matching, leaked payloads, and weakened gates.
8. Commit one logical change and report files, tests, assumptions, and risks.

If a plan STOP condition is encountered, stop and report instead of improvising.
