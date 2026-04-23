# Framework-Owned Skill Model Design

## Goal

Move the source of truth for skill discovery, identity, loading, and asset resolution into framework code under `aworld/skills/`, while preserving the existing runtime-facing `skill_configs` ABI during migration.

## Scope

This design covers:

- introducing framework-owned skill abstractions under `aworld/skills/`
- replacing eager full-content discovery with descriptor-first, lazy-content loading
- defining provider contracts for filesystem, plugin-backed, and compatibility skill sources
- preserving `skill_configs` as the downstream runtime ABI during the migration period
- defining how `amni` and `ContextSkillTool` consume framework-owned skill state
- positioning current CLI-owned resolver and install logic as adapters or consumers of the framework layer

This design does not cover:

- changing user-facing `aworld-cli skill ...` syntax in this phase
- removing compatibility for `skills_path` and `skill_names` immediately
- marketplace, signature, or remote trust policy
- semantic version solving between skill packages
- redesigning prompt-time skill scoring heuristics beyond their new framework boundary

## Problem Statement

The current branch made meaningful progress on installed-skill lifecycle and plugin-backed storage, but the ownership boundary is still wrong for the long-term architecture.

Today:

- `aworld-cli` owns the main skill resolver path
- `aworld.utils.skill_loader.collect_skill_docs()` eagerly reads full `SKILL.md` bodies during discovery
- skill identity is effectively keyed by name within a process
- compatibility inputs still escape directly into eager source loading
- `amni` still only sees ready-made `skill_configs`

This creates three structural problems:

1. discovery, activation, and content loading are coupled together
2. the framework has no formal skill provider abstraction
3. multiple entrypoints still depend on CLI-owned skill state and a global singleton registry

The long-term architecture should make these statements true:

- framework code owns skill descriptors, content loading, and provider contracts
- CLI code assembles providers and applies product-level activation policy, but does not own the skill model
- skills have stable source-qualified identity
- full `SKILL.md` content is loaded only when needed
- `amni` can track available skills, active skills, and loaded content separately

## Design Principles

1. Framework owns the model
   - `aworld-cli` may coordinate and present workflows, but it should not define the canonical skill data model.

2. Descriptor first
   - Discovery should return lightweight descriptors without eagerly loading usage bodies.

3. Stable identity
   - Skill identity must be provider-qualified, not only name-qualified.

4. Lazy content
   - Full skill body and auxiliary assets should be loaded only for active or explicitly requested skills.

5. Compatibility at the boundary
   - Existing `skill_configs`, `skills_path`, and `skill_names` remain supported through adapters, not through duplicated internal models.

## Target Package Layout

Introduce a new framework package:

- `aworld/skills/models.py`
  - `SkillDescriptor`
  - `SkillContent`
  - `SkillSelection`
  - `ResolvedSkillSet` or equivalent framework-owned result types

- `aworld/skills/providers.py`
  - `SkillProvider` abstract base class
  - shared provider utility helpers

- `aworld/skills/registry.py`
  - `SkillRegistry`
  - descriptor indexing
  - provider coordination

- `aworld/skills/loaders.py`
  - descriptor parsing from filesystem
  - lazy content loaders

- optional provider modules
  - filesystem-backed provider
  - plugin-backed provider
  - compatibility adapter provider

The existing `aworld.utils.skill_loader` module becomes transitional and should gradually delegate to the new package.

## Core Abstractions

### `SkillDescriptor`

Represents discoverable metadata without loading the full skill body.

Minimum fields:

- `skill_id`
  - stable provider-qualified identity such as `<provider_id>:<skill_name>`
- `provider_id`
- `skill_name`
- `display_name`
- `description`
- `source_type`
- `scope`
- `visibility`
- `asset_root`
- `skill_file`
- `metadata`
- `requirements`

Important constraints:

- no full `usage` body in the descriptor
- enough metadata to render discovery UIs and to run activation filtering

### `SkillContent`

Represents the heavy content loaded on demand.

Minimum fields:

- `skill_id`
- `usage`
- `tool_list`
- `raw_frontmatter`
- optional parsed auxiliary references

### `SkillProvider`

The framework-owned provider contract.

Suggested shape:

- `provider_id() -> str`
- `list_descriptors() -> Iterable[SkillDescriptor]`
- `load_content(skill_id: str) -> SkillContent`
- `resolve_asset_path(skill_id: str, relative_path: str) -> Path`

Optional capabilities:

- provider priority
- provider health / validation diagnostics
- provider-scoped metadata for install/update lifecycle

This is the missing abstraction that the current branch does not yet provide.

## Provider Types

### Filesystem Skill Provider

Wraps local directories or cached Git working trees.

Responsibilities:

- scan for `SKILL.md` files
- create `SkillDescriptor` values without reading full content bodies into runtime state
- load the body lazily when requested
- expose provider-owned asset resolution rooted to the skill directory

This provider replaces direct broad usage of `collect_skill_docs()` as the discovery primitive.

### Plugin-Backed Skill Provider

Wraps plugin manifests and legacy plugin synthesis from `aworld.plugins.discovery`.

Responsibilities:

- turn plugin `skills` entrypoints into descriptors
- preserve provider identity at the plugin level
- expose plugin-owned asset roots
- bridge plugin metadata such as scope, visibility, and agent selectors into descriptor metadata

This is where the current branch's plugin-backed install and discovery work can be reused.

### Compatibility Skill Provider

Wraps temporary compatibility sources such as markdown-agent `skills_path`.

Responsibilities:

- normalize compatibility inputs into framework providers
- preserve explicit precedence rules
- keep compatibility behavior out of core runtime consumers

This provider exists only as a migration boundary and should be removable later.

## Discovery And Identity Model

Discovery becomes a two-step process:

1. providers emit descriptors
2. framework registry indexes descriptors and applies deterministic precedence

Identity rules:

- registry keys by `skill_id`, not plain skill name
- user-facing selection by plain name remains allowed only when unambiguous
- collisions between providers with the same `skill_name` remain visible at the framework layer even if product surfaces choose a winner

Precedence rules should remain deterministic. The current first-wins behavior can remain as a product policy, but the framework must preserve enough identity to explain what lost and why.

## Lazy Loading Model

### Current Problem

`collect_skill_docs()` currently reads every matching `SKILL.md`, extracts full body text, and returns ready-to-use dicts. That blocks any true descriptor/content split.

### Target Model

Descriptor loading and content loading are separate operations.

At discovery time:

- parse only minimal frontmatter and descriptor metadata
- do not materialize `usage`
- do not build full `skill_configs`

At activation or explicit read time:

- load `SkillContent`
- adapt it to dict-shaped `skill_configs` only for selected skills
- cache loaded content by `skill_id` in framework-managed caches

This is the core architectural change required by the review.

## Framework Registry And State

Introduce a framework-owned `SkillRegistry` that is not a process-global singleton by default.

Responsibilities:

- hold provider instances for one runtime assembly
- enumerate descriptors
- resolve descriptor precedence
- support lookup by `skill_id` and by user-facing name
- lazily cache loaded `SkillContent`

Important rule:

- registry lifetime should be explicit and request- or runtime-scoped
- a process-wide singleton may remain temporarily as a compatibility shim, but it should not be the long-term source of truth

## Runtime Consumption Model

### Product-Level Activation Resolver

The current `aworld_cli.core.SkillActivationResolver` should survive conceptually, but only as a consumer of framework abstractions.

After refactor it should:

- request descriptors from framework providers or registry
- apply product rules such as agent filters, explicit `--skill` selection, and scoring
- choose active skill ids
- ask framework registry to load content for active skills
- build final dict-shaped `skill_configs`

It should no longer:

- call `collect_skill_docs()` directly
- own descriptor construction
- own compatibility source parsing

### `amni` Integration

`amni` currently consumes only `skill_configs`. This remains the compatibility ABI in the first migration wave.

The internal direction should be:

- available skills = descriptor index
- active skills = selected skill ids
- loaded content = lazy cache

Migration path:

1. continue passing `skill_configs` into `amni`
2. add internal support for richer skill state when the framework layer is stable
3. keep compatibility adapters until `ContextSkillTool` and related runtime pieces can consume provider-backed state more directly

## Provider-Owned Asset Resolution

`ContextSkillTool` currently resolves files by `skill_path` and relative filesystem traversal.

Long-term direction:

- asset resolution should be owned by the provider, not by raw path assumptions
- framework should expose `resolve_asset_path(skill_id, relative_path)`
- compatibility `skill_path` can continue to be present in `skill_configs` during migration, but it should become derived data rather than the canonical authority

This addresses the review concern that asset resolution is only partially provider-owned today.

## Compatibility Adapters

### `skill_configs`

Keep `skill_configs` as the downstream compatibility view.

Adapter rule:

- `SkillDescriptor` + lazily loaded `SkillContent` -> `skill_configs` entry

The dict shape remains stable while the upstream model changes.

### `skills_path` And `skill_names`

These remain accepted during migration, but only as inputs to a compatibility provider layer.

They should no longer:

- directly mutate a global singleton registry
- directly trigger eager final skill materialization outside the framework model

### Existing `collect_skill_docs()` API

Treat it as legacy API.

Migration approach:

- keep it for compatibility callers
- gradually reimplement it in terms of `aworld/skills/` descriptor and content loaders
- stop introducing new direct callers

## Migration Plan

### Phase 1: Framework Foundation

- create `aworld/skills/`
- define `SkillDescriptor`, `SkillContent`, `SkillProvider`, and framework `SkillRegistry`
- add filesystem and plugin-backed providers

### Phase 2: Bridge Current CLI Logic

- refactor CLI resolver code to consume framework descriptors instead of constructing candidates directly
- keep scoring, explicit selection, and visibility logic at the product layer
- keep installed-skill lifecycle in CLI for now

### Phase 3: Runtime Integration

- add framework-owned lazy content cache
- adapt `amni` and `ContextSkillTool` to work with provider-backed resolution
- keep `skill_configs` as compatibility output

### Phase 4: Remove Escape Hatches

- retire compatibility source short-circuits in the CLI resolver
- shrink or remove the global singleton `SkillRegistry`
- sunset direct public dependence on `collect_skill_docs()`

## Relationship To Other Specs

- [2026-04-15-aworld-codex-style-skill-install-design.md](/Users/wuman/Documents/workspace/aworld-mas/aworld/docs/superpowers/specs/2026-04-15-aworld-codex-style-skill-install-design.md) defined the user-facing install MVP and explicitly deferred framework ownership.
- [2026-04-21-unify-skill-plugin-model-design.md](/Users/wuman/Documents/workspace/aworld-mas/aworld/docs/superpowers/specs/2026-04-21-unify-skill-plugin-model-design.md) unified installed-skill state onto plugin-backed packaging.
- [2026-04-22-top-level-skill-command-plugin-design.md](/Users/wuman/Documents/workspace/aworld-mas/aworld/docs/superpowers/specs/2026-04-22-top-level-skill-command-plugin-design.md) covers CLI extensibility and top-level command pluginization.

This spec is the missing framework-layer architecture beneath those product- and CLI-level designs.

## Testing Strategy

Automated coverage should include:

1. descriptor discovery
   - filesystem provider returns lightweight descriptors
   - plugin-backed provider preserves provider identity and metadata

2. lazy loading
   - descriptor listing does not load usage content
   - content loads only on explicit request
   - cache behavior is deterministic

3. identity and precedence
   - duplicate skill names across providers remain distinguishable internally
   - product-level precedence remains deterministic

4. compatibility adapters
   - `skill_configs` output remains backward compatible
   - `skills_path` / `skill_names` still work through provider adapters

5. provider-owned asset resolution
   - relative asset reads are confined to provider-owned roots
   - `ContextSkillTool` path traversal guarantees are preserved

6. migration regression
   - current CLI install/list/enable/disable flows continue to work
   - task-time activation still produces expected `skill_configs`

## Risks And Mitigations

### Risk: `collect_skill_docs()` Has Too Many Callers

This is the largest migration obstacle.

Mitigation:

- introduce framework providers first
- reimplement `collect_skill_docs()` as an adapter rather than trying to delete it immediately
- stop adding new direct call sites

### Risk: Too Much Logic Moves At Once

If discovery, scoring, runtime ABI, and CLI UX all change together, the migration becomes hard to validate.

Mitigation:

- move ownership first
- keep scoring and CLI UX stable during the first framework migration
- preserve `skill_configs` until the framework model is proven

### Risk: Provider Metadata Is Incomplete For Legacy Sources

Legacy skill directories may not have enough metadata to fully support all future features.

Mitigation:

- require minimal provider identity even for compatibility providers
- degrade gracefully when advanced metadata is absent

### Risk: Singleton Removal Breaks Existing Entrypoints

Several current entrypoints implicitly rely on global state.

Mitigation:

- keep a compatibility singleton shim temporarily
- migrate runtime callers to explicit registry assembly before deleting the shim

## Acceptance Criteria

The framework architecture is considered complete when all of the following are true:

1. framework-owned skill abstractions exist under `aworld/skills/`.
2. skill discovery can produce descriptors without eagerly loading full `usage` content.
3. skill identity is provider-qualified internally.
4. CLI activation logic consumes framework providers or registry rather than directly calling `collect_skill_docs()`.
5. `skill_configs` remains a compatibility output rather than the primary internal model.
6. asset resolution can be performed by provider identity rather than only by raw `skill_path`.
7. compatibility inputs such as `skills_path` and `skill_names` flow through adapter providers instead of direct eager source loading.
