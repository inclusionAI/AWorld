# CLI Hook Acceptance Example Design

**Goal:** Add a standalone, manually runnable example package under `examples/` that demonstrates AWorld hook capabilities through `aworld-cli` using the default AWorld agent path.

**Scope:**
- Create a dedicated example directory for hook acceptance demos.
- Provide a README with exact manual commands and example queries.
- Include one demo agent and hook scripts/config needed to exercise the flows.
- Keep the package self-contained so a user can `cd` into the example directory and run it directly.

**Non-Goals:**
- No automated verification or pytest coverage in the example package.
- No new hook points or protocol changes.
- No attempt to mirror every Claude Hooks event; only the examples that map cleanly to current AWorld capabilities.

## User-Facing Outcome

The example should let a user run `aworld-cli` manually and observe these behaviors:

1. `user_input_received` blocks a dangerous prompt before agent execution.
2. `user_input_received` rewrites a prompt before it reaches the agent.
3. `before_tool_call` blocks a dangerous command that the agent attempts to run.
4. `after_tool_call` appends visible audit context after a safe tool call completes.

The entrypoint remains the normal `aworld-cli` flow, with the default agent being an AWorld agent defined inside the example directory.

## Directory Design

Create a new directory:

- `examples/cli_hook_acceptance/`

Planned contents:

- `examples/cli_hook_acceptance/README.md`
- `examples/cli_hook_acceptance/agent.py`
- `examples/cli_hook_acceptance/.aworld/hooks.yaml`
- `examples/cli_hook_acceptance/hooks/block_user_input.sh`
- `examples/cli_hook_acceptance/hooks/rewrite_user_input.sh`
- `examples/cli_hook_acceptance/hooks/block_rm_rf.sh`
- `examples/cli_hook_acceptance/hooks/audit_tool_output.sh`

The example should not depend on unrelated repo examples. Paths inside `hooks.yaml` should resolve relative to the example directory so users can copy or run it as-is.

## Agent Design

The example agent should be intentionally narrow and deterministic enough for manual demos:

- Use an AWorld agent file that can respond to ordinary prompts.
- Bias the agent toward using a shell or terminal-style tool for file cleanup requests so the `before_tool_call` and `after_tool_call` demos are observable.
- Keep the agent minimal; the point is hook behavior, not agent sophistication.

The README should avoid promising perfect determinism from arbitrary natural language. Instead, it should provide exact demo queries that are known to trigger the intended flow.

## Hook Configuration Design

Use one shared `.aworld/hooks.yaml` with four enabled hooks:

- `user_input_received` deny hook:
  - Detects destructive requests such as `rm -rf`.
  - Returns a deny decision with a clear user-facing reason.
- `user_input_received` rewrite hook:
  - Rewrites a broad cleanup request into a safer, scoped request.
  - Leaves other prompts unchanged.
- `before_tool_call` deny hook:
  - Detects dangerous tool commands such as `rm -rf`.
  - Blocks execution with a clear reason.
- `after_tool_call` audit hook:
  - Adds audit-oriented context after safe tool execution.
  - Demonstrates post-tool observation without changing the core hook protocol.

The deny and rewrite hooks must be scoped carefully enough that the README can tell the user which query exercises which behavior.

## README Design

The README should be written as a short acceptance walkthrough:

1. Preconditions
   - repository root
   - `aworld-cli` available
   - any required environment variables already configured by the user
2. Setup
   - `cd examples/cli_hook_acceptance`
   - launch command using the local agent file
3. Demo scenarios
   - scenario name
   - exact query to type
   - expected visible outcome
4. Notes
   - tool-selection behavior is guided by the example agent
   - hook output is demonstrative, not a security boundary for arbitrary custom agents

Each scenario should be copy-paste ready and describe what the user should see in one or two lines.

## Implementation Notes

- Prefer shell scripts for the demo hooks, matching current hook examples elsewhere in the repo.
- Use ASCII-only content for scripts and README commands.
- Reuse the current hook protocol fields:
  - `permission_decision`
  - `permission_decision_reason`
  - `updated_input`
  - `system_message`
  - `updated_output`
- Do not add new framework code unless the example exposes a real capability gap.

## Validation

Manual validation after implementation:

- From `examples/cli_hook_acceptance`, run the documented `aworld-cli` command.
- Enter each documented query and confirm the observed behavior matches the README.
- Confirm the example works without requiring users to edit paths inside the example directory.

Secondary validation:

- Existing hook regression tests should still pass if any framework code changes are required to support the example.
