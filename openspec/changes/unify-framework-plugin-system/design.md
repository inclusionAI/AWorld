## Context

AWorld already has plugin-related code, but it is fragmented. Today the primary model is:

- `aworld-cli` installs plugins into `~/.aworld/plugins`
- plugin loading assumes a directory convention such as `agents/` and `skills/`
- runtime behavior is still largely unaware of plugins as first-class framework entities
- context is not modeled as an explicit plugin extension surface

At the same time, `aworld-cli` already has a fixed bottom toolbar implemented directly in `console.py`. It renders a small set of built-in fields such as agent, mode, cron status, workspace, and git branch. That makes the status bar useful, but not extensible.

In contrast, the `claudecode` implementation uses a more mature plugin approach with:

- explicit plugin metadata and validation
- typed loaded-plugin models
- per-component loaders
- scope, policy, versioning, and plugin-private data handling

The `claude_hud` implementation provides a second useful reference point. It shows a strong pattern for:

- collecting structured status inputs
- rendering status as ordered lines
- keeping rendering separate from data collection

That line-oriented HUD pattern is useful for AWorld, but AWorld should not copy the standalone subprocess model directly. The better fit is a framework-collected status snapshot plus plugin-contributed HUD line providers, with `aworld-cli` retaining final rendering control.

That model is useful as reference, but it is still application-centric. AWorld needs a framework-centric plugin model where CLI is only one consumer.

## Goals / Non-Goals

**Goals:**
- Define a framework-level plugin abstraction that can extend AWorld capabilities dynamically.
- Define reusable plugin framework primitives so one plugin model can support command-driven, hook-driven, context-driven, and HUD-driven extensions.
- Make `context` a first-class plugin surface alongside agents, swarms, tools, hooks, skills, runners, and CLI commands.
- Introduce manifest-driven plugin validation instead of relying only on directory conventions.
- Define source, scope, lifecycle, dependency, conflict, and policy concepts for plugins.
- Define a composable HUD extension model for the `aworld-cli` bottom status bar.
- Preserve a path for existing CLI-managed plugins to migrate into the unified model.

**Non-Goals:**
- Fully implement every plugin-backed capability in a single iteration.
- Replace all existing loader code immediately.
- Design a public marketplace or registry protocol in this first change.
- Lock in the final on-disk manifest location if a temporary compatibility bridge is needed.
- Reproduce Claude Code's on-disk markdown frontmatter or shell invocation model byte-for-byte.
- Allow a single plugin to replace the entire bottom toolbar renderer in V1.

## Decisions

### Decision: Make Plugins A Framework Capability, Not A CLI Feature

The center of the system is a new framework-level plugin manager and capability registry. CLI plugin commands become an adapter that installs, enables, disables, lists, and reloads framework plugins.

Why:
- AWorld needs plugins to affect runtime and context, not just CLI-visible features.
- Treating CLI as the source of truth would continue the current architectural skew.

Alternative considered:
- Extend the current `aworld-cli` plugin manager only.
  Rejected because it leaves runtime and context as secondary consumers.

### Decision: Use A Unified Plugin Manifest And Loaded Model

Each plugin should declare metadata and contributed capability surfaces explicitly, following the same broad lesson as `claudecode`:

- manifest-driven metadata
- component-aware loading
- explicit source identity
- explicit enablement and error handling

For AWorld, the loaded model should at least include:
- plugin identity and source
- version
- scope
- declared capabilities
- resolved configuration
- activation status
- validation and load errors

Alternative considered:
- Keep implicit directory-based loading as the primary model.
  Rejected because it cannot scale cleanly to context, runtime, and conflict management.

### Decision: Make Capabilities Multi-Surface

A single plugin may contribute to one or more of these surfaces:

- agents
- swarms
- tools
- mcp servers
- runners
- hooks
- contexts
- hud
- skills
- cli commands

This intentionally mirrors the multi-component loading pattern seen in `claudecode`, but applies it to framework objects instead of only app features.

### Decision: Define The Plugin System Around Shared Framework Primitives

The plugin framework should not be defined around HUD or any single consumer. It should be defined around a small set of primitives that every plugin surface shares:

- manifest
- lifecycle
- typed entrypoints
- packaged assets
- scoped state
- policy and permissions

This makes HUD one case of the framework instead of the driver of the framework.

Each plugin entrypoint should be represented as a typed descriptor owned by one plugin, for example:

- command entrypoints
- hook entrypoints
- context entrypoints
- hud entrypoints
- skill entrypoints
- agent or swarm entrypoints

Each descriptor should expose at least:

- stable entrypoint id
- entrypoint type
- activation scope
- metadata visible to the consuming surface
- runtime target or handler reference
- declared permissions or allowed effects
- references to packaged assets or plugin state when needed

This model allows AWorld to support Claude-style command plugins, Ralph-style hook plugins, and future runtime extensions without defining a separate plugin mechanism for each one.

### Decision: Commands Are Plugin Entrypoints, Not Loose Markdown Files

CLI commands should be modeled as one kind of plugin entrypoint rather than as a directory convention discovered independently.

The command contract should support at least:

- public slash-command name
- description and discoverability metadata
- optional argument hint or argument schema
- hidden or internal command visibility
- execution target such as a prompt template, script, or framework handler
- tool-permission policy for the command session
- access to plugin-packaged assets and plugin-scoped state

This is the capability needed to support plugins shaped like `code-review`, where the important abstraction is not "a markdown file exists" but "the plugin contributes a command with metadata, execution policy, and packaged resources."

### Decision: Hooks Need Structured Inputs And Typed Control Results

Hook entrypoints should be powerful enough to participate in workflow control, not just fire side effects.

The hook contract should support:

- structured event input from the hook point
- read and write access to plugin-scoped state where allowed
- typed results such as allow, mutate, deny, or block-and-continue
- optional follow-up payloads such as updated input or system messages
- deterministic ordering and failure handling

That contract is necessary for plugins shaped like `ralph-wiggum`, where a stop hook does not merely observe a stop event but can block termination and inject the next loop input back into the session.

### Decision: Plugins Need Shared Scoped State And Packaged Assets

Many useful plugins require coordination across more than one entrypoint. A command may initialize state, a hook may continue the workflow, and a HUD provider may display status for the same plugin.

The framework should therefore define plugin-owned resources explicitly:

- packaged assets under a stable plugin root
- persistent plugin data directories
- scoped state stores for `global`, `workspace`, and `session`

The resource contract should give entrypoints stable references such as:

- plugin root path
- plugin data path
- plugin state path for the current scope

This is the missing primitive required for cases like `ralph-wiggum`, where a command initializes loop state and a later stop hook consumes and mutates that same state.

### Decision: Policy Must Apply At Plugin Level And Entrypoint Level

Enablement at the whole-plugin level is necessary but not sufficient.

The framework should also support entrypoint-level policy, including:

- whether a command is exposed in the CLI
- which tool families a command is allowed to invoke
- which hook actions are allowed at a given hook point
- whether a plugin may read or write session-scoped state

This keeps the plugin framework broad enough for powerful extensions without making every active plugin equally privileged.

### Decision: Use Composable HUD Line Providers

For bottom status bar customization, AWorld should adopt the line-oriented rendering lesson from `claude_hud`, but expose it through plugins as composable `HudLineProvider` units.

The V1 design should be:

- AWorld core builds a read-only `HudContext` snapshot
- plugins register `HudLineProvider` objects
- each provider returns zero or more HUD lines
- `aworld-cli` owns final ordering, truncation, coloring, refresh cadence, and rendering

Why:
- it preserves multi-plugin composition
- it avoids letting one plugin monopolize the toolbar
- it lets AWorld reuse the same status model in future terminal or web surfaces

Alternative considered:
- allow one plugin to replace the whole toolbar renderer
  Rejected for V1 because it makes conflicts and composition much harder.

Alternative considered:
- let plugins contribute only tiny text segments
  Rejected for V1 because it creates tighter coupling between providers and layout logic than line providers do.

### Decision: Core Collects HUD State, Plugins Render HUD Lines

The data-collection boundary should stay in AWorld core. Plugins should consume a stable `HudContext` rather than scraping console output, reading large transcripts, or probing runtime internals directly.

The first `HudContext` version should cover at least:

- workspace identity
- git branch and related status
- active agent and mode
- cron and notification state
- session identity
- context usage
- active tools
- active agents or subagents
- todo or task progress
- plugin-contributed context snapshot data

This borrows the separation-of-concerns lesson from `claude_hud` while staying aligned with AWorld's framework-first architecture.

### Decision: HUD Uses A Snapshot Contract With Deterministic Assembly

The HUD contract should be explicit enough that plugins can extend the status bar without taking control of terminal rendering.

The V1 `HudContext` snapshot should be read-only and assembled once per refresh cycle. It should include stable buckets rather than exposing arbitrary runtime objects:

- `workspace`: workspace name, cwd, repository availability, branch, and dirty/attached status when available
- `session`: session id, active agent, active mode, start time, and current activation scope
- `notifications`: cron availability, unread counts, and other notification-center summary fields
- `context`: token or memory usage summaries, current context profile, and plugin-contributed context snapshot fields
- `activity`: currently running tool calls, active agents or subagents, and recent execution state
- `tasks`: todo counts, active task summary, and other lightweight progress indicators

Unavailable data should be omitted or represented as unknown rather than forcing providers to re-query it.

Each plugin HUD contribution should be a `HudLineProvider` with a deterministic identity:

- `provider_id`
- `section`
- `priority`
- optional `is_enabled(context)` gate
- `render_lines(context) -> list[HudLine]`

Each returned `HudLine` should carry:

- `line_id`
- `section`
- `priority`
- `text`
- optional `ttl_ms`
- optional visibility hints such as compact-only or active-only

V1 should keep the payload plain-text only. Styled prompt-toolkit rendering remains CLI-owned so plugins cannot inject raw terminal formatting or bypass width management.

### Decision: HUD Ordering, Conflict, And Performance Rules Must Be Deterministic

HUD composition only remains usable if plugin behavior is predictable.

The V1 section order should be fixed:

1. `identity`
2. `session`
3. `context`
4. `activity`
5. `tasks`
6. `custom`

Within a section, ordering should be stable:

- lower `priority` renders earlier
- ties break by plugin id
- ties then break by provider id
- ties then break by line id

Conflict rules should also be explicit:

- duplicate plugin ids are already invalid at the plugin layer
- duplicate `provider_id` values inside one plugin are validation errors
- duplicate `line_id` values returned by one provider in a single render pass are provider errors
- content overlap across different providers is allowed; AWorld does not attempt semantic deduplication in V1

Performance rules should be enforced at the contract boundary:

- providers must not perform network access, git subprocesses, filesystem scans, or transcript parsing in `render_lines`
- expensive collection belongs in core collectors or background runtime state updaters that feed `HudContext`
- the CLI renders from one shared snapshot per refresh cycle and may cache provider output according to `ttl_ms`
- provider failures degrade to omitted lines plus surfaced plugin errors, not toolbar failure

### Decision: Add Context As A First-Class Plugin Surface

Plugins must be able to extend context behavior explicitly. The first version should support declarations for:

- context schema and typed state
- context enrichment or injection
- context propagation rules
- context serialization and persistence adapters
- retrieval or indexing adapters used by context-backed systems

Alternative considered:
- Treat context as runtime internals and postpone plugin support.
  Rejected because context is one of AWorld's core extensibility layers and was explicitly identified as missing.

### Decision: Context Management Must Be Phase-Aware

Adding `context` as a plugin surface is not enough by itself. AWorld also needs a predictable model for when context plugins participate.

The V1 context management lifecycle should be phase-aware:

1. schema registration: plugins declare typed context state and validation rules
2. bootstrap: plugins provide default values or persisted state restoration
3. enrichment: plugins can add derived or retrieved context before execution
4. propagation: plugins decide what context follows subagents, tools, or nested runtime boundaries
5. persistence: plugins can serialize or checkpoint owned context state after execution

Each phase should be explicit in the runtime contract so a plugin can extend context without mutating arbitrary runtime internals.

This keeps context plugins powerful enough to support framework features while limiting when and how they can affect execution.

### Decision: Support Scope, Version, Policy, And Plugin Data

Borrow the operational lessons from `claudecode`:

- plugin source and installation metadata
- scope-aware enablement
- version-aware loading and caching
- policy-based blocking
- plugin-private data directories

For AWorld, scopes should be modeled in framework terms:
- global
- workspace
- session

These concepts are required before plugin behavior can be trusted across runtime and context boundaries.

## Risks / Trade-offs

- [Model too broad for first delivery] -> Split implementation into a core registry phase and capability-adapter phases.
- [Breaking current CLI plugin behavior] -> Provide a compatibility bridge where existing `agents/` and `skills/` plugins can be ingested into the new manifest-driven loader.
- [Context plugins become overpowered] -> Require explicit capability declarations and scoped activation before context mutators are applied.
- [HUD plugins become expensive to render] -> Make core produce a shared `HudContext` snapshot and forbid heavy I/O in provider render paths.
- [Capability conflicts across plugins] -> Add plugin dependency and conflict metadata plus deterministic load ordering.

## Migration Plan

1. Introduce the framework-level plugin manifest, source model, and loaded plugin model.
2. Add a capability registry and plugin lifecycle hooks for discover, validate, resolve, load, activate, deactivate.
3. Define plugin capability adapters for context, runtime, hooks, HUD, skills, and CLI.
4. Bridge existing CLI-installed plugins into the new system.
5. Move CLI plugin management to operate on the framework plugin registry rather than only filesystem conventions.

## Open Questions

- Whether the first manifest path should be a new dedicated file or a compatibility-friendly location that can coexist with current plugin directories.
- Whether runner and tool plugin activation should happen at process startup only or support reload during a live session.
