# Unify Skill And Plugin Model Design

## Goal

Preserve the user-facing `aworld-cli skill ...` workflow while unifying all installed-skill behavior onto the AWorld plugin model so runtime activation, scope, dependency handling, and discovery are driven by one internal architecture.

## Scope

This design covers:

- retaining the `skill` CLI as a first-class user workflow
- representing installed skills internally as plugin-managed resources
- introducing one runtime resolver path that produces agent-facing `skill_configs`
- mapping current installed-skill scope semantics onto plugin activation and skill entrypoint metadata
- keeping markdown agent `skills_path` and `skill_names` as a temporary compatibility adapter
- defining migration phases from the current dual-path implementation to a single internal model

This design does not cover:

- marketplace or remote catalog UX
- plugin package signing or trust policy
- semantic version solving between skill packages
- a rewrite of `ContextSkillTool` or the `skill_configs` runtime ABI
- removal of markdown frontmatter compatibility in the first implementation wave

## Problem Statement

The current branch successfully adds durable installed-skill lifecycle commands, but it does so through a second internal model:

1. installed skill packages are managed through a dedicated manifest under `~/.aworld/skills/.manifest.json`
2. plugin installation is managed separately under the plugin framework
3. runtime skill assembly is split across multiple call sites
4. agent-specific skill visibility is currently expressed as string scope values such as `agent:<name>`

This is acceptable for an MVP, but it is not a stable long-term architecture.

The primary issues are:

- two installation models exist for closely related resources
- plugin lifecycle concepts such as activation, dependencies, and conflicts are not the source of truth for installed skills
- multiple loaders and agent builders assemble skills independently
- scope concepts are mixed together in a way that cannot scale to workspace/session activation and richer agent targeting

The long-term architecture should make these statements true:

- `skill` remains a product concept
- `plugin` becomes the only internal packaging and activation model
- runtime skill exposure is resolved in one place
- context and agent runtime layers consume resolved `skill_configs` without needing to know where the skill came from

## Design Principles

1. One internal model
   - Installed skills must no longer be a separate managed resource type at runtime.
   - All installed-skill state should be expressible as plugin state.

2. Preserve user workflow
   - Users should continue to use `aworld-cli skill install/list/remove/update`.
   - Existing markdown-agent skill declarations should keep working during migration.

3. Keep runtime ABI stable
   - `BaseAgent`, `AmniContext`, and `ContextSkillTool` should continue to consume `skill_configs`.
   - Refactoring should focus on how `skill_configs` are resolved, not on redefining the downstream ABI.

4. Make migration observable and reversible
   - Compatibility paths should be explicit, logged, and documented as deprecated where appropriate.

## Target Architecture

### Core Boundary

`skill` becomes a user-facing view over plugin capabilities, not an independent runtime resource.

The target model is:

- `aworld-cli skill ...` remains the entrypoint for skill-oriented users
- installed skill sources are stored and activated through the plugin system
- runtime code resolves active skills only through a unified resolver
- agents and context continue to receive only resolved `skill_configs`

In practical terms:

- the plugin model owns installation metadata, activation scope, dependency/conflict handling, and enable/disable state
- the skill model owns only skill-specific metadata and the final `skill_configs` payload exposed to agents

### Skill Package Shapes

Internally, installed skills must resolve to one of two plugin shapes:

1. Manifest plugin
   - contains `.aworld-plugin/plugin.json`
   - declares `entrypoints.skills`

2. Legacy skills-only plugin
   - contains a `skills/` directory but no plugin manifest
   - is wrapped by compatibility logic into a synthetic plugin representation

The long-term preference is manifest plugins. Legacy skills-only plugins remain supported as an adapter format so existing skill repositories and local skill collections can still be installed.

### Installation Model

The `skill` CLI continues to accept local directories and Git URLs, but installation behavior changes:

1. If the source already contains a valid plugin manifest, install it as a plugin.
2. If the source contains `skills/` or is itself a valid skill collection root, install it as a skills-only plugin.
3. If the source contains neither, reject the install.

Under this model:

- `skill install` is syntactic sugar for a plugin-backed install of a skills-capable package
- `skill list` becomes a filtered plugin view
- `skill remove` removes the underlying installed plugin package
- `skill update` updates the underlying installed plugin package when the source is git-backed

The current standalone installed-skill manifest becomes transitional data, not the final source of truth.

## Runtime Resolution Model

### Unified Resolver

Introduce a single runtime component, referred to in this design as `SkillActivationResolver`.

Responsibilities:

1. consume the currently active plugin set
2. find all skills contributed by those plugins
3. apply activation and visibility filtering for the current runtime boundary and agent
4. produce deterministic `skill_configs`

This resolver becomes the only supported path for building agent-visible skills in normal runtime execution.

### Inputs

The resolver should consume:

- the active plugin registry
- runtime boundary information:
  - global
  - workspace
  - session
- current agent identity
- any compatibility-adapter inputs such as markdown `skills_path` and `skill_names`

### Output

The resolver should output:

- `ResolvedSkillSet`
  - internal representation of active skill entrypoints and metadata
- `skill_configs`
  - the existing agent-facing dictionary that includes:
    - name
    - description
    - usage
    - tool_list
    - skill_path
    - other existing skill metadata needed by the runtime

`BaseAgent`, `Sandbox`, `AmniContext`, and `ContextSkillTool` continue to consume this final `skill_configs` output.

### Code Paths To Collapse

The following patterns should be retired in favor of the resolver:

- direct directory scanning in built-in agent builders
- direct `SkillRegistry.register_source(...)` calls from plugin loading paths for normal runtime activation
- markdown-agent loaders that independently register skill sources and independently filter names

Those callers should instead pass their inputs into the unified resolver and consume its output.

## Scope And Visibility Model

The current branch mixes activation semantics and agent filtering into one string field such as `global` or `agent:developer`. That does not scale.

This design replaces that with two explicit layers.

### Layer 1: Plugin Activation Scope

Plugin activation continues to use the plugin model:

- `global`
- `workspace`
- `session`

This answers:

- when is the plugin active
- in which runtime boundary should its capabilities be considered

This must be driven by plugin manifest data or plugin-state metadata, not by a skill-only custom scope string.

### Layer 2: Skill Exposure Scope

Each skill entrypoint can then define its own exposure policy inside plugin metadata.

Recommended shape:

```json
{
  "entrypoints": {
    "skills": [
      {
        "id": "browser",
        "target": "skills/browser/SKILL.md",
        "scope": "workspace",
        "visibility": "public",
        "metadata": {
          "agent_selectors": ["developer", "aworld"],
          "default_enabled": true
        }
      }
    ]
  }
}
```

Interpretation:

- `activation_scope` controls when the plugin is active
- skill entrypoint `scope` controls the runtime boundary in which the skill may be exposed
- `visibility` controls display and discovery policy
- `metadata.agent_selectors` controls which agents may receive the skill

### Mapping From Current Scope Model

Current installed-skill semantics map as follows:

- `global`
  - plugin activation scope: `global`
  - skill entrypoint metadata: no agent selectors

- `agent:<name>`
  - plugin activation scope: `global`
  - skill entrypoint metadata: `agent_selectors=["<name>"]`

This migration preserves behavior while removing the overloaded string model from the public long-term architecture.

## Markdown Agent Compatibility Adapter

### Why Compatibility Exists

Markdown agents currently allow:

- `skills_path`
- `skill_names`

These are widely useful for local and project-scoped authoring. Removing them abruptly would force immediate bulk migration of existing agent assets.

### Adapter Rules

The compatibility adapter remains available, but its role changes.

It must no longer:

- directly register arbitrary sources into the global runtime model for normal skill activation
- independently perform final skill filtering outside the unified resolver

Instead it should:

1. normalize `skills_path` into temporary skill-source descriptors or temporary plugin-like descriptors
2. pass those descriptors into `SkillActivationResolver`
3. pass `skill_names` as a filter expression into the same resolver
4. receive final `skill_configs` from the resolver

This preserves compatibility while ensuring all skill activation logic still flows through one code path.

### Deprecation Policy

`skills_path` and `skill_names` remain supported during migration, but:

- documentation should mark them as deprecated compatibility inputs
- logs and CLI help should point users toward plugin-backed and `skill install` workflows
- the adapter should be implemented as a boundary layer that can be deleted cleanly in a later release

## CLI Design

### `aworld-cli skill install`

Behavior:

- preserve current user-facing syntax
- install plugin-backed skill packages
- when the source is a raw skill collection rather than a manifest plugin, wrap it as a skills-only plugin
- store enough metadata to support list/remove/update

### `aworld-cli skill list`

Behavior:

- read plugin-managed installed state
- filter to installed packages that expose the `skills` capability
- present a skill-oriented view that includes:
  - install identifier
  - source
  - activation scope
  - skill count
  - agent selector information when applicable

### `aworld-cli skill remove`

Behavior:

- remove the underlying installed plugin package
- preserve current safety guarantees for symlink-backed installs

### `aworld-cli skill update`

Behavior:

- update the underlying installed plugin package if the source is git-backed
- keep current constraints for non-git-backed sources

### Relationship To `plugins` CLI

The two command groups remain different views over overlapping resources:

- `plugins list` shows plugin-oriented state
- `skill list` shows skill-oriented state for plugins that provide skills

The same installed package may appear in both views, rendered differently.

## Migration Plan

### Phase 1: Internal Unification

Goals:

- introduce `SkillActivationResolver`
- keep `skill` CLI surface stable
- stop adding new direct directory-scan code paths

Changes:

- move built-in agent skill assembly to the resolver
- move plugin runtime skill assembly to the resolver
- keep `skill_configs` ABI unchanged
- continue supporting the current installed-skill data while the plugin-backed model is introduced

### Phase 2: Compatibility Adapter

Goals:

- preserve markdown-agent behavior without preserving duplicate resolution logic

Changes:

- replace markdown loader source-registration logic with adapter logic feeding the resolver
- add deprecation messaging for `skills_path` and `skill_names`

### Phase 3: State Consolidation

Goals:

- remove the dual-source-of-truth problem

Changes:

- migrate installed-skill management from standalone manifest ownership to plugin-owned metadata
- replace public `agent:<name>` scope semantics with plugin activation scope plus skill metadata selectors
- reduce or remove compatibility-only code that is no longer needed

## Non-Goals

This design intentionally does not require:

- changing the structure of `skill_configs`
- rewriting `ContextSkillTool`
- redesigning sandbox skill transport
- implementing a public plugin marketplace
- solving complex selector languages beyond a simple `agent_selectors` list

## Acceptance Criteria

The architecture is considered complete when all of the following are true:

1. `aworld-cli skill install` results in internally plugin-backed installed state.
2. Runtime code has one supported path for resolving active skills into `skill_configs`.
3. Built-in agents no longer independently scan skill directories.
4. Plugin loading no longer relies on separate normal-runtime skill registration logic outside the unified resolver.
5. Markdown-agent compatibility paths still work, but only as adapters into the resolver.
6. Plugin activation scope is the sole authority for global/workspace/session activation semantics.
7. Agent-specific skill exposure is expressed through skill entrypoint metadata rather than `agent:<name>` strings.
8. `BaseAgent`, `AmniContext`, and `ContextSkillTool` continue to work with resolved `skill_configs` without needing source-specific branching.
9. `plugins list` and `skill list` can describe the same installed skills-capable package from different user views.
10. Documentation clearly explains the compatibility window and the eventual retirement path for markdown-agent legacy inputs.

## Validation Strategy

Validation for the eventual implementation should cover:

- plugin-backed install, remove, update, and list flows
- manifest plugin and legacy skills-only plugin installation
- resolver output for:
  - global activation
  - workspace activation
  - session activation
  - agent selector filtering
- built-in agent skill visibility
- markdown-agent compatibility behavior through the adapter path
- context file browsing for resolved skills via `ContextSkillTool`

## Risks And Mitigations

### Risk: Hidden coupling in current skill assembly

Mitigation:

- introduce the resolver behind existing interfaces first
- migrate one caller family at a time

### Risk: Compatibility paths linger indefinitely

Mitigation:

- mark them deprecated in docs and runtime messaging from the first implementation release
- keep the adapter small and explicitly separate from the core resolver

### Risk: Scope migration breaks current `agent:<name>` installs

Mitigation:

- provide explicit mapping rules during migration
- keep compatibility parsing for stored legacy values until data migration is complete
