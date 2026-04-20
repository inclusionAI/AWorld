## Why

AWorld currently has plugin-shaped behavior in `aworld-cli`, but it is still a directory-and-loader convention centered on CLI installation, `agents/`, and `skills/`. It does not yet provide a framework-level plugin system that can uniformly extend runtime, context, tools, hooks, swarms, CLI behavior, or the bottom status bar.

## What Changes

- Introduce a new framework-level `plugin-system` capability for AWorld.
- Define a unified plugin manifest, source model, lifecycle, and capability registry that is not owned by the CLI.
- Define plugin framework primitives around typed entrypoints, packaged assets, scoped state, and entrypoint-level policy.
- Add `context` as a first-class plugin extension surface alongside agents, swarms, tools, hooks, skills, runners, and CLI commands.
- Define command and hook entrypoint contracts strong enough to support Claude-style command plugins and Ralph-style session-control plugins.
- Add `hud` as a first-class plugin extension surface so plugins can extend the `aworld-cli` bottom status bar through composable line providers.
- Define V1 HUD around a core-owned `HudContext` snapshot and plugin-provided plain-text `HudLineProvider` contributions, with CLI retaining ordering, truncation, styling, and refresh control.
- Define explicit context-management phases so plugins can participate in context schema, bootstrap, enrichment, propagation, and persistence without reaching into runtime internals ad hoc.
- Evolve CLI plugin commands into one consumer of the framework plugin system rather than the system of record.
- Align skill, hook, and runtime plugin behavior with the unified plugin model.

## Capabilities

### New Capabilities
- `plugin-system`: Defines the framework-level plugin abstraction, manifest, lifecycle, source model, scope model, context extension model, HUD extension model, and capability registration contract.

### Modified Capabilities
- `agent-runtime`: Add plugin-aware runtime activation and plugin-contributed runtime/context/tool/HUD-state integration.
- `workflow-hooks`: Add plugin-provided hook registration and lifecycle behavior.
- `cli-experience`: Add framework-plugin management and plugin-aware CLI behavior.
- `skills-system`: Add plugin-provided skill sourcing through the unified plugin model.

## Impact

- Affects `aworld-cli` plugin installation and loading code, which currently centers on `agents/` and `skills/`.
- Introduces a framework-level plugin abstraction that runtime and context systems can consume directly.
- Establishes plugin primitives that let one framework support command, hook, HUD, context, skill, and agent extension cases.
- Expands the plugin surface to cover context, runtime, hooks, tools, agents, swarms, skills, CLI commands, and HUD status lines.
- Enables cross-entrypoint plugins that share packaged assets and scoped state, instead of treating each plugin surface as isolated files on disk.
- Establishes deterministic HUD composition rules needed to evolve the `aworld-cli` bottom status bar through plugins rather than hard-coded fields.
- Establishes explicit context-management phases so plugin-provided context behavior can be activated, propagated, and persisted predictably.
- Establishes a migration path away from ad hoc plugin directory assumptions toward manifest-driven capability loading.
