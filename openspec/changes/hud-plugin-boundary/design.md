## Context

The current HUD stack has three layers:

- the built-in HUD plugin in `aworld-cli/src/aworld_cli/builtin_plugins/aworld_hud/`
- host-side HUD capability loading in `aworld-cli/src/aworld_cli/plugin_runtime/hud.py`
- CLI rendering in `aworld-cli/src/aworld_cli/console.py`

That layering is directionally correct, but the current boundary is too loose. Several recent HUD changes required touching host code for behavior that users perceive as plugin behavior, such as which fields matter, how runtime state should be summarized, and how a built-in HUD should evolve over time.

The specific design pressure points are:

- `plugin_runtime/hud.py` is host-side capability support, but its name implies plugin-owned runtime code.
- `console.py` has become the place where many HUD-visible behaviors are corrected, even when the desired change is really about HUD content policy.
- hooks already exist as the main runtime extension mechanism, but HUD state still leans too heavily on host-curated runtime snapshots instead of hook-driven plugin state.
- as a result, a future third-party HUD plugin would still be too dependent on host changes for ordinary feature evolution.

This change does not attempt to turn HUD into a zero-host feature. The CLI still owns the toolbar surface and renderer. The goal is to minimize host responsibility so that HUD capability growth happens through plugins, with `aworld-hud` serving as a built-in plugin that obeys the same contract as any external HUD plugin.

There is also an important delivery constraint from the current working branch: although the implementation has not yet reached the desired boundary, the present HUD behavior is already acceptable from a user-experience perspective. The redesign must therefore preserve the current accepted behavior while improving ownership boundaries underneath it.

## Goals / Non-Goals

**Goals:**

- Keep bottom-toolbar rendering and terminal integration in `aworld-cli`.
- Move HUD content policy and runtime-specific field composition toward plugin-owned logic.
- Make hooks the preferred mechanism for HUD plugins to observe task lifecycle and update plugin-scoped HUD state.
- Keep built-in `aworld-hud` on the same contract as a third-party HUD plugin, aside from shipping location and default activation.
- Rename or relocate host-side HUD support modules so their ownership is obvious during review.
- Preserve the currently accepted HUD user-facing behavior while refactoring internals toward the desired plugin boundary.

**Non-Goals:**

- Eliminating all host HUD code from `aworld-cli`.
- Giving plugins raw `prompt_toolkit` control or direct terminal mutation rights.
- Redesigning the full hook system beyond what is required for HUD-oriented plugin state flow.
- Replacing the current working HUD behavior in this design change.
- Solving every existing naming issue under `plugin_runtime/` in one pass.
- Regressing the currently accepted HUD output in order to achieve a cleaner internal architecture.

## Decisions

### Decision: CLI owns a generic HUD surface, not `aworld-hud` behavior

The CLI host remains responsible for:

- mounting the bottom toolbar
- refreshing it
- truncating and styling it
- rendering plain-text HUD segments

The CLI host is not responsible for `aworld-hud` business logic such as which semantic fields must appear, which fields belong together, or how a built-in HUD summarizes runtime state.

Why:

- terminal integration is inherently host-specific
- plugin authors should not need to know `prompt_toolkit`
- the host surface should work for both built-in and third-party HUD plugins

Alternative considered:

- let plugins render toolbar markup directly
  Rejected because it couples plugin authors to host UI internals and weakens layout guarantees.

### Decision: Built-in HUD plugins must use the same contract as external plugins

`aworld-hud` remains built-in, but it must behave like a plugin consumer of host capabilities rather than like a privileged extension path. Host code must not branch on the plugin name or contain `aworld-hud`-specific field policy.

Why:

- built-in status does not justify a separate architectural contract
- the built-in plugin should prove the plugin surface is sufficient
- review becomes much easier when "built-in" only means "shipped with CLI"

Alternative considered:

- keep a special built-in HUD fast path
  Rejected because it preserves the same ambiguity that caused the current confusion.

### Decision: Hooks become the preferred source of HUD-oriented plugin state

HUD plugins should derive runtime-facing state through hook participation and plugin-scoped state, not by requiring host business logic for each new field. The runtime may still expose generic shared context, but plugin-specific synthesis should happen in plugin code.

The intended flow is:

`hook lifecycle -> plugin-scoped state update -> HUD provider render_lines(context, plugin_state) -> CLI renderer`

Why:

- hooks are already the framework's runtime extension mechanism
- plugin-scoped state allows multiple entrypoints in one plugin to cooperate cleanly
- HUD business logic becomes easier to keep inside the plugin boundary

Alternative considered:

- keep growing runtime-owned HUD snapshots with plugin-specific semantics
  Rejected because it makes host code the default home for plugin behavior.

### Decision: Runtime context remains generic and shared

The runtime still provides a shared HUD context because some values are inherently host-owned:

- current workspace
- branch
- session identity
- terminal-facing refresh cadence
- generic execution summary data that all HUD providers may consume

However, the runtime contract should stop short of being the primary place where `aworld-hud` business semantics are assembled. It should provide generic context plus plugin-scoped state bridges, not plugin-specific presentation policy.

Alternative considered:

- remove runtime HUD context and force all HUD state through plugin-owned persistence
  Rejected because basic session and execution context are legitimately host-owned.

### Decision: Host-side HUD support modules should use host/capability naming

`aworld-cli/src/aworld_cli/plugin_runtime/hud.py` is semantically misleading. It is host-side capability support, not plugin-owned runtime code. The implementation should move this responsibility under a host-oriented namespace such as:

- `aworld_cli/plugin_capabilities/hud.py`
- or a similar host-owned capability path

The exact target path is an implementation detail, but the design requirement is that the name must make ownership obvious.

Why:

- current naming confuses review and ownership
- the code supports plugin capability loading; it is not the plugin itself
- future contributors should be able to locate the host/plugin boundary from paths alone

Alternative considered:

- move the current file directly under `ui_extensions/`
  Rejected for now because the current file does more than UI rendering; it also loads and aggregates plugin HUD providers.

### Decision: Prefer `plugin_capabilities/*` as the host-side namespace

For the current codebase, the preferred naming direction is:

- host-side plugin capability support:
  - `aworld_cli/plugin_capabilities/hud.py`
  - `aworld_cli/plugin_capabilities/hooks.py`
  - `aworld_cli/plugin_capabilities/state.py`
- built-in plugin implementations:
  - `aworld_cli/builtin_plugins/aworld_hud/...`
- CLI presentation integration:
  - `aworld_cli/console.py`

This is preferred over `ui_extensions/*` because the current host-side HUD support is not only UI. It loads plugin entrypoints, validates capability payloads, and assembles plugin-visible data structures. Those responsibilities belong to plugin capability support, not purely to UI composition.

Why:

- `plugin_capabilities` clearly communicates host ownership plus plugin-facing purpose
- it leaves room for non-UI plugin capability helpers that still belong on the CLI side
- it reduces confusion between "plugin code" and "host support for plugins"

Alternative considered:

- use `ui_extensions/*` for all HUD-related host code
  Rejected because that path implies a primarily visual responsibility, while the current module also handles capability loading and aggregation.

### Decision: Migrate by compatibility alias first, then caller cleanup

The rename should happen in two phases:

1. introduce the new host-owned namespace with the real implementation
2. leave compatibility re-export shims behind existing import paths until callers and tests move over

Recommended transition shape:

- move implementation to `plugin_capabilities/*`
- leave `plugin_runtime/*` as compatibility imports for one migration cycle
- update internal callers to the new namespace
- remove compatibility paths only after import usage and documentation are clean

Why:

- keeps the refactor reviewable
- avoids mixing semantic cleanup with wider HUD behavior changes
- reduces regression risk in tests and plugin-loading code

## Directory Ownership Map

The intended long-term ownership map for HUD-related code is:

### Host presentation layer

- `aworld-cli/src/aworld_cli/console.py`

Responsibility:

- mount the bottom HUD surface
- apply prompt-toolkit style and refresh behavior
- render plain-text HUD lines and segments
- handle width reduction, ordering, and truncation at presentation time

Must not own:

- `aworld-hud` field policy
- plugin-specific content semantics
- plugin-name-based branches

### Host plugin-capability support layer

- `aworld-cli/src/aworld_cli/plugin_capabilities/hud.py`
- `aworld-cli/src/aworld_cli/plugin_capabilities/hooks.py`
- `aworld-cli/src/aworld_cli/plugin_capabilities/state.py`

Responsibility:

- load plugin entrypoints
- validate capability payloads
- provide capability-specific adapters and shared data structures
- bridge runtime state and plugin-scoped state into plugin entrypoints

Must not own:

- terminal rendering details that belong in `console.py`
- plugin-specific HUD summaries
- built-in-plugin privilege paths

### Built-in plugin layer

- `aworld-cli/src/aworld_cli/builtin_plugins/aworld_hud/...`

Responsibility:

- define HUD line composition
- define segment grouping and summary policy
- consume hooks, plugin state, and generic runtime context
- remain replaceable by an external HUD plugin using the same capability contract

Must not own:

- direct terminal writes
- prompt-toolkit markup
- ad hoc access to host internals outside the declared plugin contract

### Compatibility layer during migration

- `aworld-cli/src/aworld_cli/plugin_runtime/*.py`
- `aworld-cli/src/aworld_cli/plugin_framework/*.py`

Responsibility:

- temporarily re-export the host-owned implementation while imports migrate

Must not become:

- the permanent home of new HUD capability work
- the place where new behavior is introduced first

## Reviewer Checklist

Use this checklist when reviewing follow-up implementation work for this change:

- Does the diff add or preserve any `aworld-hud` plugin-name branch in host code?
- Does a HUD content change require touching `console.py` for reasons other than presentation?
- Is a new HUD field being added through plugin code, or is the host assembling plugin-specific semantics again?
- Does a hook or plugin-state addition remain generic enough to support more than one HUD plugin?
- Does the new path or module name make host ownership obvious from the filesystem layout?
- Can the built-in HUD plugin still be conceptually replaced by an external plugin using the same capability contract?
- Does the refactor preserve the current accepted manual HUD behavior rather than trading UX regressions for architectural purity?

## Acceptance Baseline

Follow-up implementation work under this change must validate against the currently accepted HUD behavior, using the user-approved manual baseline reflected by the referenced screenshots and recent CLI verification.

The acceptance intent is:

- the current branch behavior is an acceptable UX baseline even if the internals are not yet at the desired architecture
- future refactors must preserve that behavior while moving ownership from host-specific HUD logic toward plugin contracts, hooks, and plugin state
- architectural progress does not justify regressions in HUD output, lifecycle stability, or accepted visual behavior

For review purposes, the baseline is defined by these user-approved outcomes:

- the HUD remains functionally correct in the current CLI experience
- the delivered behavior continues to satisfy the user need even before the internal design is fully cleaned up
- refactors are successful only if they preserve the accepted behavior and improve the host/plugin boundary at the same time

## Risks / Trade-offs

- [Risk] Hook-driven HUD state may duplicate some runtime snapshot data.
  → Mitigation: keep runtime context limited to generic shared fields and document which fields are host-owned vs plugin-owned.

- [Risk] A stricter boundary may require small framework additions for plugin state write-back from hooks.
  → Mitigation: treat those additions as generic plugin-state features, not HUD-specific branches.

- [Risk] Renaming host-side modules can create churn for imports and tests.
  → Mitigation: perform renames behind compatibility imports first, then clean up callers in a follow-up commit.

- [Risk] Some HUD fields may still legitimately require host support.
  → Mitigation: allow generic host context additions when they benefit all HUD providers, but reject plugin-name-specific behavior in review.

## Migration Plan

1. Freeze the desired boundary in OpenSpec before further HUD behavior changes.
2. Rename or alias misleading host-side HUD capability modules to a host-owned namespace.
3. Audit current HUD-related logic and classify each piece as host-rendering, generic capability support, or plugin-owned content policy.
4. Add or extend generic plugin-state update paths so hooks can publish HUD-relevant state without special-casing `aworld-hud`.
5. Migrate built-in `aworld-hud` field composition toward hook-driven state plus generic context.
6. Add regression coverage proving that a synthetic third-party HUD plugin can use the same contract without requiring host business branches.
7. Remove compatibility aliases only after imports, tests, and OpenSpec references all point at the host-owned namespace.

Rollback strategy:

- keep compatibility import aliases during the migration
- avoid changing the currently working HUD rendering behavior until the new boundary is covered by tests

## Open Questions

- Should hook-driven plugin state be returned as explicit typed deltas, or should hooks write through a generic plugin-state helper?
- Should the built-in `aworld-hud` plugin keep using shared runtime HUD snapshots for some fields, or should it exclusively consume plugin-scoped state plus minimal host context?
- Should HUD provider rendering continue to accept only `context`, or should the contract explicitly include plugin-owned state as a first-class input?
