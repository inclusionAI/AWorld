## 1. Functional Plugin Contract Gaps

- [x] 1.1 Implement a generic plugin-state write-back API for active plugin entrypoints.
- [x] 1.2 Add task lifecycle hook points for `task_started`, `task_progress`, `task_completed`, and `task_error`.
- [x] 1.3 Upgrade the HUD provider contract from `render_lines(context)` to `render_lines(context, plugin_state)` through the generic framework path.
- [x] 1.4 Define the explicit plugin-facing HUD helper boundary and remove reliance on private host helper imports from plugin code where applicable.

## 2. Hook-Driven HUD State

- [x] 2.1 Define the generic plugin-state update path that HUD-oriented hooks will use.
- [x] 2.2 Document which runtime context fields remain host-owned and which HUD semantics are expected to move into plugin-owned state.
- [x] 2.3 Wire supported hook points so a HUD plugin can observe task lifecycle and maintain its own state without direct host customization.

## 3. Built-In Plugin Alignment

- [x] 3.1 Update `aworld-hud` so its field composition and grouping logic live in plugin code rather than in host rendering code.
- [x] 3.2 Keep `aworld-hud` on the same HUD capability contract as an external plugin, apart from shipping location and default enablement.
- [x] 3.3 Verify that removing `aworld-hud` leaves the CLI HUD host surface intact rather than breaking host startup or rendering.

## 4. End-To-End Validation

- [x] 4.1 Add tests that prove host HUD rendering works with generic HUD providers and does not depend on `aworld-hud` by name.
- [x] 4.2 Add tests that prove hook-driven plugin state can feed HUD providers through the generic plugin contract.
- [x] 4.3 Add an end-to-end validation flow using a mock third-party HUD plugin that exercises install/discovery, hook-driven state updates, and final HUD rendering.
- [ ] 4.4 Run manual validation against the currently accepted HUD behavior baseline before declaring the boundary refactor successful.

## 5. Host Boundary Cleanup And Naming

- [x] 5.1 Audit `aworld-cli` HUD-related code paths and classify each one as generic rendering, generic capability support, or plugin-specific content logic.
- [x] 5.2 Remove or refactor any host-side `aworld-hud` business branches that cannot be justified as generic HUD surface behavior.
- [x] 5.3 Introduce a host-owned `plugin_capabilities` namespace for HUD-related capability support.
- [x] 5.4 Add compatibility re-export shims from existing `plugin_runtime` HUD support imports to the new host-owned namespace.
- [x] 5.5 Move host callers and tests from `plugin_runtime/*` HUD support imports to `plugin_capabilities/*`.
- [x] 5.6 Update documentation and OpenSpec references to use the host-owned namespace language.
- [ ] 5.7 Remove compatibility aliases only after callers and tests no longer depend on the old paths.

## 6. Final Validation

- [x] 6.1 Validate the OpenSpec change and update review notes based on feedback.
