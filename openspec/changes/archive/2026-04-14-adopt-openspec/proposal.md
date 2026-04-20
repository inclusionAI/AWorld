## Why

The repository currently mixes change-management history, assistant workflow notes, and product capability documentation across `docs/superpowers/`, `CLAUDE.md`, and general docs. That makes it hard to tell which documents describe the current contract and which are only historical context.

## What Changes

- Adopt OpenSpec as the only active workflow for future repository changes.
- Create baseline capability specs for repository governance, agent runtime, workflow hooks, CLI experience, skills, and training integration.
- Move assistant workflow instructions into `AGENTS.md` and reduce `CLAUDE.md` to a compatibility pointer.
- Mark `docs/superpowers/` as historical-only and update contributor-facing docs to point at OpenSpec.

## Capabilities

### New Capabilities
- `change-governance`: Defines how this repository proposes, tracks, and archives behavior changes through OpenSpec.
- `agent-runtime`: Defines the stable runtime responsibilities for tasks, runners, events, contexts, and swarm execution.
- `workflow-hooks`: Defines the lifecycle hook model and its integration with runtime execution.
- `cli-experience`: Defines the supported CLI entry points and command-style interactions.
- `skills-system`: Defines how reusable skills are discovered and exposed in the AWorld experience.
- `training-integration`: Defines the stable training integration surface around `AgentTrainer` and backend processors.

### Modified Capabilities
- None.

## Impact

- Adds `openspec/` as the source of truth for stable behavior and proposed changes.
- Adds `AGENTS.md` as the repository-level assistant workflow file.
- Updates contributor guidance in `README.md`.
- Retires `docs/superpowers/` from active change management without deleting historical records.
