## 1. Core Plugin Model

- [x] 1.1 Define the framework-level plugin manifest schema and loaded-plugin model.
- [ ] 1.2 Define plugin source, scope, version, policy, dependency, and conflict semantics.
- [ ] 1.3 Define plugin lifecycle phases for discover, validate, resolve, load, activate, deactivate, and unload.

## 2. Capability Registry

- [ ] 2.1 Add a plugin capability registry that can register agents, swarms, tools, MCP servers, runners, hooks, contexts, HUD providers, skills, and CLI commands.
- [x] 2.2 Define plugin-private configuration and persistent data directory behavior.
- [x] 2.3 Add a compatibility bridge for current directory-based plugins that only expose `agents/` and `skills/`.
- [x] 2.4 Define typed plugin entrypoint descriptors for commands, hooks, contexts, HUD providers, skills, and agent-like surfaces.
- [x] 2.5 Define plugin-scoped resource handles for packaged assets and `global` / `workspace` / `session` state.

## 3. Context And Runtime Integration

- [x] 3.1 Define plugin-contributed context schema, bootstrap, enrichment, propagation, persistence, and retrieval adapter hooks.
- [ ] 3.2 Define runtime activation behavior for plugin-contributed runtime, context, tool, and HUD-state surfaces.
- [x] 3.3 Define plugin-contributed hook loading, typed control results, and ordering rules.

## 4. HUD Integration

- [x] 4.1 Define a core `HudContext` collector that aggregates bottom-toolbar state for plugins.
- [x] 4.2 Define composable plain-text `HudLineProvider` plugin APIs, section ordering, and conflict rules.
- [ ] 4.3 Define CLI-owned rendering, width truncation, refresh behavior, and provider performance constraints for plugin-provided HUD lines.

## 5. CLI Integration

- [ ] 5.1 Update CLI plugin management to operate on the framework plugin system.
- [x] 5.2 Define enable, disable, list, install, remove, and reload flows against framework plugins.
- [x] 5.3 Preserve a migration path for existing installed plugins under `~/.aworld/plugins`.
- [x] 5.4 Define plugin command metadata, packaged-resource resolution, and entrypoint-level tool permission behavior.

## 6. Validation

- [x] 6.1 Add tests for manifest validation, capability registration, and conflict handling.
- [x] 6.2 Add tests for plugin-provided context lifecycle integration, HUD context collection, and runtime activation.
- [ ] 6.3 Add CLI-level tests for framework-plugin management flows, composable HUD rendering, ordering, and truncation.
- [x] 6.4 Add tests for command entrypoint metadata, hook control actions, and shared plugin state across command and hook surfaces.
