## 1. Change Skeleton

- [x] 1.1 Create a standalone Ralph plugin fixture or built-in plugin directory with manifest, commands, stop hook, and HUD provider.
- [x] 1.2 Keep the phase-1 implementation fully inside the CLI/plugin host layer and do not add a dependency on `RalphRunner`.

## 2. State And Command Flow

- [x] 2.1 Define the plugin state schema for active loop state, iteration data, completion promise, and declarative verification commands.
- [x] 2.2 Implement `/ralph-loop` argument handling for prompt text, repeatable `--verify`, optional `--completion-promise`, and optional `--max-iterations`, while keeping `--model` and `--work-dir` out of scope for phase 1.
- [x] 2.3 Implement `/cancel-ralph` state clearing.
- [x] 2.4 Normalize stored loop state into the effective follow-up prompt contract used for continuation.

## 3. Stop Hook

- [x] 3.1 Implement stop-hook continuation logic using plugin state as the single source of truth.
- [x] 3.2 Enforce the phase-1 stop-condition set only: inactive loop, max iterations reached, or exact completion-promise match.
- [x] 3.3 Ensure loop continuation increments iteration and preserves operator-visible diagnostics needed by the HUD.
- [x] 3.4 Ensure invalid or corrupted state fails predictably without leaving the CLI in an ambiguous continuation state.

## 4. HUD

- [x] 4.1 Add a HUD provider that exposes loop-active state, iteration count, and completion-promise summary.
- [x] 4.2 Keep HUD observational only and verify that no control logic depends on HUD rendering.

## 5. Validation

- [x] 5.1 Add tests for plugin discovery and command registration.
- [x] 5.2 Add tests for `/ralph-loop` state initialization and `/cancel-ralph` cleanup.
- [x] 5.3 Add stop-hook tests for `block_and_continue`, completion-promise success, and max-iteration termination.
- [x] 5.4 Add tests proving `--verify` values are preserved structurally and injected into the effective follow-up prompt.
- [x] 5.5 Add HUD tests for active and inactive loop rendering.
- [x] 5.6 Use only simple high-signal acceptance cases for phase 1, centered on unbounded loops, explicit iteration caps, and declarative verification; defer model/work-dir override cases.

## 6. Future Boundary

- [x] 6.1 Document in code and tests that phase 1 is a standalone session-loop plugin and not a `RalphRunner` wrapper.
- [x] 6.2 Leave a narrow future seam for phase-2 fresh-run orchestration without committing that work to `RalphRunner` integration.
