## Context

The repository already has rich product documentation in `README.md` and `docs/`, plus a recent internal workflow under `docs/superpowers/specs` and `docs/superpowers/plans`. What it lacks is a single system that separates stable behavior from proposed changes.

OpenSpec provides that separation, but the local CLI behavior is intentionally lightweight: initialization creates only the directory skeleton, and new changes start with `.openspec.yaml`. The repository therefore needs manual authoring for the first migration artifacts, baseline specs, and assistant instructions.

## Goals / Non-Goals

**Goals:**
- Make `openspec/changes/` the only active place for future change proposals.
- Establish an initial `openspec/specs/` baseline that covers the project's major contributor-facing capabilities.
- Keep assistant instructions separate from product specs by moving them into `AGENTS.md`.
- Preserve old design and plan files as historical references instead of deleting them.

**Non-Goals:**
- Rewrite all tutorial and architecture docs into OpenSpec.
- Encode every internal implementation detail in the first baseline specs.
- Remove historical docs or retroactively convert every past change into OpenSpec archives.

## Decisions

### Decision: Model OpenSpec Around Capability Domains

Use six capability domains:
- `change-governance`
- `agent-runtime`
- `workflow-hooks`
- `cli-experience`
- `skills-system`
- `training-integration`

This gives future changes a stable target without forcing the repository to convert every tutorial or low-level module into its own spec.

Alternative considered:
- Migrate `docs/` wholesale into OpenSpec.
  Rejected because it would blur tutorials, design notes, and current behavioral contracts.

### Decision: Move Assistant Instructions Into `AGENTS.md`

Use `AGENTS.md` for repository-specific guidance about how assistants should work here. Keep `openspec/specs/` focused on system behavior rather than agent workflow rules.

Alternative considered:
- Turn `CLAUDE.md` into a spec source.
  Rejected because the file primarily documents assistant collaboration policy, not product behavior.

### Decision: Keep Legacy Superpowers Docs As Archived Context

Retain `docs/superpowers/` in place, but explicitly mark it historical-only.

Alternative considered:
- Delete `docs/superpowers/`.
  Rejected because the existing files still contain useful design history.

## Risks / Trade-offs

- [Baseline specs too broad] -> Keep each spec minimal and normative, then let future changes add detail.
- [Contributor confusion during transition] -> Add OpenSpec instructions to both `AGENTS.md` and `README.md`, and place a retirement note in `docs/superpowers/`.
- [Bootstrap mismatch between change specs and main specs] -> Use a single migration change and archive it immediately so the baseline ends in canonical main specs.

## Migration Plan

1. Initialize OpenSpec and create the `adopt-openspec` migration change.
2. Write the proposal, design, task list, and delta specs for the six baseline capabilities.
3. Add `AGENTS.md`, update `README.md`, and reduce `CLAUDE.md` to a compatibility note.
4. Validate and archive `adopt-openspec` so `openspec/specs/` is generated from the migration.
5. Replace archive-generated placeholder purposes in the main specs and run final validation.

## Open Questions

- None for this migration. Future refinement should happen through additional OpenSpec changes instead of expanding the bootstrap migration.
