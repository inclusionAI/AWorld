## 1. Host Boundary Cleanup

- [ ] 1.1 Introduce a host-owned `plugin_capabilities` namespace for HUD-related capability support.
- [ ] 1.2 Add compatibility re-export shims from existing `plugin_runtime` HUD support imports to the new host-owned namespace.
- [ ] 1.3 Audit `aworld-cli` HUD-related code paths and classify each one as generic rendering, generic capability support, or plugin-specific content logic.
- [ ] 1.4 Remove or refactor any host-side `aworld-hud` business branches that cannot be justified as generic HUD surface behavior.

## 2. Hook-Driven HUD State

- [ ] 2.1 Define the generic plugin-state update path that HUD-oriented hooks will use.
- [ ] 2.2 Document which runtime context fields remain host-owned and which HUD semantics are expected to move into plugin-owned state.
- [ ] 2.3 Wire supported hook points so a HUD plugin can observe task lifecycle and maintain its own state without direct host customization.

## 3. Built-In Plugin Alignment

- [ ] 3.1 Update `aworld-hud` so its field composition and grouping logic live in plugin code rather than in host rendering code.
- [ ] 3.2 Keep `aworld-hud` on the same HUD capability contract as an external plugin, apart from shipping location and default enablement.
- [ ] 3.3 Verify that removing `aworld-hud` leaves the CLI HUD host surface intact rather than breaking host startup or rendering.

## 4. Namespace Migration

- [ ] 4.1 Move host callers and tests from `plugin_runtime/*` HUD support imports to `plugin_capabilities/*`.
- [ ] 4.2 Update documentation and OpenSpec references to use the host-owned namespace language.
- [ ] 4.3 Remove compatibility aliases only after callers and tests no longer depend on the old paths.

## 5. Validation

- [ ] 5.1 Add tests that prove host HUD rendering works with generic HUD providers and does not depend on `aworld-hud` by name.
- [ ] 5.2 Add tests that prove hook-driven plugin state can feed HUD providers through the generic plugin contract.
- [ ] 5.3 Run manual validation against the currently accepted HUD behavior baseline before declaring the boundary refactor successful.
- [ ] 5.4 Validate the OpenSpec change and update review notes based on feedback.
