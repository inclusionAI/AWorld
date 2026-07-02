# AWorld Codex-Style Skill Install MVP Design

## Goal

Make AWorld support a Codex-style skill installation model where skills can be made available by placing them under a fixed user directory, while also providing a first-party `aworld-cli skill ...` command set that installs into the same underlying layout.

## Scope

This MVP covers:

- A fixed user skill installation root for automatic discovery
- A hybrid install model: manual directory placement plus `aworld-cli skill install`
- Support for two source layouts:
  - repository or directory containing `skills/`
  - repository or directory whose root is itself a skill collection
- Install-time scope selection with `global` as the default and optional `agent:<name>`
- Minimal installation metadata to support list, remove, update, and import
- Runtime auto-registration of installed skills without requiring `--skill-path` or `SKILLS_PATH`

This MVP does not cover:

- Marketplace or remote index distribution
- Semantic version resolution or dependency solving
- Signature verification or trust policy
- A full plugin marketplace redesign
- A full end-state refactor of the framework skill model in the same change

## Problem Statement

AWorld already supports skills from local directories and GitHub URLs, but the current model is still source-registration driven:

1. Skills are primarily injected with `--skill-path`, `SKILLS_PATH`, `SKILLS_DIR`, or loader-specific configuration.
2. Plugin installation and skill registration are related but not unified into one user-facing installation model.
3. A user can point AWorld at a skill source, but there is no durable notion of "installed skill package" with lifecycle commands.
4. Pure skill packages do not have a clean first-class path that mirrors Codex's "put skills in a known directory and let the runtime discover them" model.

The result is that AWorld can load skills, but it does not yet provide the simple operational model described by Superpowers for Codex:

- there is a fixed user skill root
- the runtime scans it automatically
- manual installation and CLI installation converge on the same runtime behavior

## Requirements

### Functional Requirements

1. AWorld must automatically discover installed skills from a fixed user directory at startup.
2. Users must be able to install skills manually by placing a directory or symlink under that fixed root.
3. Users must also be able to install skills through `aworld-cli skill install <source>`.
4. Both manual and CLI-driven installation must converge on the same runtime discovery logic.
5. The runtime must support both of these source layouts:
   - `<root>/skills/<skill-name>/SKILL.md`
   - `<root>/<skill-name>/SKILL.md`
6. Installed skills must be globally visible by default.
7. Users must be able to choose an install scope of either `global` or `agent:<name>`.
8. AWorld must provide `skill list`, `skill remove`, `skill update`, and `skill import` commands for installed entries.
9. Existing `--skill-path` and `SKILLS_PATH` behavior must continue to work as a temporary or advanced override path.

### Non-Functional Requirements

1. The MVP should require minimal changes to current callers.
2. Discovery and scope rules must be deterministic.
3. The design should preserve a migration path toward the planned framework-owned skill provider model.
4. The solution must remain compatible with existing filesystem-backed `SKILL.md` packages.

## Design Overview

The MVP uses a hybrid model:

- The fixed user install directory is the source of truth for installed skills.
- `aworld-cli skill install` is the official installer, but it installs into that same directory model.
- Manual installation is not a second-class workflow; it simply writes to the same directory tree that the runtime already scans.

This is intentionally close to Codex's native skill discovery model while still giving AWorld a productized CLI lifecycle.

## Installation Model

### Fixed User Skill Root

Introduce a durable installed-skill root:

```text
~/.aworld/skills/installed/
```

Each installed entry gets its own directory under that root:

```text
~/.aworld/skills/installed/<install_id>/
```

The runtime scans this root automatically during skill registry initialization. Users should not need `--skill-path` or `SKILLS_PATH` just to make an installed skill package available.

### Hybrid Entry Modes

The system supports two ways of populating that root:

1. CLI-managed install
   - `aworld-cli skill install <git-url-or-local-path>`
   - the CLI clones, copies, or symlinks into the installed root and records metadata

2. Manual install
   - the user manually places a directory or symlink under `~/.aworld/skills/installed/`
   - the runtime discovers it automatically
   - `aworld-cli skill import <path>` can optionally register it into manifest metadata for better management

Manual installation and CLI installation must share the same discovery and path-resolution behavior.

## Source Resolution Rules

Each installed entry resolves to exactly one effective skill source root using this precedence:

1. If `<entry>/skills/` exists and contains skill directories, use `<entry>/skills/`.
2. Otherwise, if `<entry>/` itself contains skill directories, use `<entry>/`.
3. Otherwise, the entry is invalid and should be skipped with a warning.

A valid skill directory is a directory containing `SKILL.md` or `skill.md`.

This supports both:

- repository layout

```text
repo/
  skills/
    brainstorming/
      SKILL.md
```

- direct collection layout

```text
collection/
  brainstorming/
    SKILL.md
  writing-plans/
    SKILL.md
```

## Scope Model

### Supported Scopes

Each installed entry declares one of these scopes:

- `global`
- `agent:<name>`

Default scope is `global`.

### Runtime Behavior

Scope filtering happens during skill assembly, before skills are exposed to a specific agent runtime.

- `global` entries are available to all skill-aware agents.
- `agent:<name>` entries are only available when building skill lists for that agent.

The MVP does not add a broader namespace policy system. It only adds an install-time filter that is stable and easy to reason about.

## Minimal Install Metadata

Add a lightweight installation manifest:

```text
~/.aworld/skills/.manifest.json
```

Each entry stores:

- `install_id`
- `name`
- `source`
- `installed_path`
- `resolved_skill_source_path`
- `install_mode` (`clone`, `copy`, `symlink`, `manual`)
- `scope` (`global` or `agent:<name>`)
- `installed_at`

The manifest is intentionally minimal. In this MVP it is not a package manager database. It exists to support:

- listing installed entries
- removing installed entries
- updating git-backed entries
- importing manually placed entries into managed state

## CLI Surface

### `aworld-cli skill install <source>`

Supported sources:

- Git URL
- local directory

Supported options:

- `--scope global|agent:<name>`
- `--mode clone|copy|symlink`

Default behavior:

- install into `~/.aworld/skills/installed/<install_id>/`
- resolve the effective skill root using the source resolution rules
- write a manifest entry

### `aworld-cli skill list`

Shows:

- install id
- source
- scope
- install mode
- installed path
- resolved skill source path
- number of discovered skills

### `aworld-cli skill remove <install_id|name>`

Behavior:

- delete the manifest entry
- delete the managed entry under the installed root
- if the managed entry is a symlink, remove only the symlink and never delete the external source directory

### `aworld-cli skill update <install_id|name>`

MVP behavior:

- supported only for git-backed installs
- perform `git pull` for cloned entries or re-clone if needed
- recompute the resolved skill source path if the layout changes

### `aworld-cli skill import <path>`

Behavior:

- require that the path already exists under the installed root
- resolve the effective skill source path
- create a manifest entry with `install_mode=manual`

This command is for management completeness, not for runtime correctness. The runtime should still be able to discover valid unmanaged entries placed under the installed root.

## Runtime Integration

### Registry Initialization

Extend skill registry initialization so it assembles sources in this order:

1. explicit command-line `--skill-path` values
2. sources from `SKILLS_PATH`
3. source from `SKILLS_DIR`
4. local `./skills`
5. installed skill entries from `~/.aworld/skills/installed/`

This keeps the installed root as the default persistent baseline while preserving explicit caller- and project-level overrides. Installed entries should still be auto-registered without requiring caller configuration.

### Deterministic Conflict Handling

Keep the current "first registered wins" policy for name conflicts in the MVP.

That means installation ordering and source assembly order must be explicit and documented. The installed-skill scan should use deterministic ordering, such as lexicographic order by `install_id`.

### Scope Filtering

When skills are collected for a specific agent:

- include all `global` installed entries
- include `agent:<name>` entries matching the current agent
- exclude other scoped entries

This filtering should happen before prompt assembly so the runtime does not surface inaccessible skills and then rely on later-stage logic to hide them.

## Relationship To Existing Plugin And Skill Paths

This MVP does not replace plugins or ad hoc skill sources.

Instead:

- installed skills become the default persistent user-facing mechanism
- `--skill-path` and `SKILLS_PATH` remain available for temporary, development, and debugging workflows
- plugin-managed skills continue to work, but they are no longer the primary answer for "how does a user install a skill package?"

This avoids a large plugin-system rewrite while still giving users a Codex-style operational model.

## Implementation Boundaries

### In Scope

- new `skill` CLI command group
- installed root scanning
- manifest read/write
- source layout resolution
- scope-aware assembly
- documentation updates
- tests for install, discovery, scope, and conflict behavior

### Out of Scope

- marketplace registration
- package signatures
- dependency solving
- fully replacing the current `skill_path` runtime contract everywhere
- fully migrating all runtime callers to the future framework-owned provider model in this same MVP

## Testing Strategy

### Unit Tests

1. Source layout resolution
   - repo with `skills/` resolves to `skills/`
   - direct collection resolves to root
   - invalid layout is rejected

2. Manifest behavior
   - install writes manifest entry
   - remove deletes manifest entry
   - import creates `manual` manifest entry

3. Scope filtering
   - global skills appear for all agents
   - `agent:<name>` skills appear only for the matching agent

4. Conflict handling
   - deterministic first-wins behavior is preserved

### CLI Tests

1. install from local directory
2. install from git URL
3. list shows discovered skill counts and scope
4. remove deletes managed entry
5. update works for git-backed install and fails cleanly for unsupported modes

### Integration Tests

1. A skill placed manually under the installed root is discovered without `--skill-path`
2. A CLI-installed skill is discovered without `--skill-path`
3. Existing `--skill-path` flow still works

## Migration And Rollout

Roll out in two phases:

1. Add installed-root discovery and new CLI lifecycle without removing existing environment-variable paths.
2. Update documentation so the installed-root model becomes the recommended user workflow and `--skill-path` becomes the advanced path.

This keeps backward compatibility while shifting the default mental model toward installed skills instead of transient source registration.

## Risks And Mitigations

### Risk: Two Models Remain Confusing

Users may be confused by installed skills versus `--skill-path`.

Mitigation:

- make installed skills the documented default
- describe `--skill-path` as temporary or development-only
- ensure both models share the same filesystem-backed skill parsing rules

### Risk: Installed Root And Plugin Skills Diverge

If plugin-provided skills and installed skills use different discovery assumptions, future migration gets harder.

Mitigation:

- keep effective skill source resolution explicit
- keep deterministic ordering rules
- align this MVP with the existing plan to move toward framework-owned skill providers

### Risk: Scope Handling Leaks Into Prompt Layer

If scoped skills are filtered too late, models may still see the wrong inventory.

Mitigation:

- filter at skill assembly time, not only during prompt formatting

## Future Follow-Ups

After the MVP:

1. move skill registry ownership fully into framework code under `aworld/skills/`
2. replace raw `skill_path` assumptions with provider-owned asset resolution
3. add optional remote indexes or marketplace support
4. add version metadata once package lifecycle becomes richer
