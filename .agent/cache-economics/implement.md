# Implementation Workflow

1. Read `.agent/cache-economics/goal.md`,
   `.agent/cache-economics/plans.md`,
   `.agent/cache-economics/standards.md`, and
   `.agent/cache-economics/progress.md` before each milestone.
2. Inspect existing owners and tests before introducing an abstraction.
3. Add failing behavioral tests first.
4. Implement the smallest provider-neutral change that satisfies the tests.
5. Run focused tests, then the relevant Context/model/CLI regression suite.
6. Review the diff for post-compile mutation, false-zero usage, trust movement,
   task-specific behavior, and accidental unrelated files.
7. Update `.agent/cache-economics/progress.md` after each completed action and
   milestone review.
8. Commit only after tests pass; do not include benchmark outputs unless they are
   intentionally frozen release evidence.
