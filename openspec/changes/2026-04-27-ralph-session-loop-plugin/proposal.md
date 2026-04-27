## Why

AWorld already has two relevant but different pieces of Ralph-adjacent capability:

- `aworld-cli` has a plugin framework with `commands`, `stop` hooks, plugin state, and HUD surfaces that can control an interactive CLI session.
- `aworld/runners/ralph_runner.py` is a core runner-oriented loop executor designed around `Task -> exec_tasks -> stop detector -> workspace artifacts`.

The desired phase-1 capability is not a core task runner. It is an interactive Ralph loop that stays inside the current CLI session and continues execution when the operator attempts to exit. That shape matches the plugin host surface much more closely than the existing `RalphRunner`.

The repository also has two external Ralph references with different strategies:

- `claude_plugin/plugins/ralph-wiggum` implements an in-session loop using a stop hook and session-local state.
- `amp` implements a fresh-process orchestration loop through `ralph.sh`, `prd.json`, and append-only progress artifacts.

For AWorld, phase 1 should optimize for the smallest clean integration boundary:

- keep the implementation independent from `aworld/core`
- reuse the existing plugin and CLI session lifecycle
- avoid coupling interactive session control to `RalphRunner`
- leave fresh-run orchestration to a later phase

## What Changes

- Introduce a standalone AWorld plugin that provides a Ralph-style in-session loop for the interactive CLI.
- Define the phase-1 plugin shape around:
  - prompt commands
  - `stop` hook continuation
  - plugin-scoped persisted state
  - optional HUD status lines
- Freeze the phase-1 boundary so the plugin does not depend on `RalphRunner`.
- Freeze the phase-1 verify model so verification requirements are declared and injected into the task prompt, but are not executed by the stop hook itself.
- Reserve phase 2 for fresh-run orchestration, where reuse of shared Ralph concepts or selected `RalphRunner` concepts can be evaluated separately.

## Capabilities

### New Capabilities

- `ralph-session-loop-plugin`: Adds a standalone plugin-hosted Ralph interaction model for the AWorld interactive CLI.

### Modified Capabilities

- `plugins`: Extends the plugin contract with a concrete design target for a self-looping interactive workflow built from existing `commands`, `hooks`, `state`, and `hud` surfaces.

## Impact

- Affects plugin manifests and plugin entrypoint usage under the AWorld CLI plugin framework.
- Affects the interactive CLI experience by adding Ralph-specific slash commands and a stop-hook-controlled continuation path.
- Does not require `aworld/core` changes for phase 1.
- Does not require `RalphRunner` changes for phase 1.
- Creates an explicit future seam for phase-2 fresh-run orchestration without prematurely hard-binding that work to the current runner implementation.
