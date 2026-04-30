# AWorld CLI Memory

This document covers the `aworld-cli` memory features that are specific to CLI workspaces.

## Overview

`aworld-cli` memory has two related behaviors:

1. **Explicit workspace guidance**
   - You save stable instructions on purpose with `/remember` or by editing `/memory`.
   - These instructions are read back in future CLI sessions for the same workspace.
2. **Task-completion evaluation**
   - After a task finishes, the CLI records a session log entry and a promotion decision.
   - Only high-confidence workspace instructions are eligible for automatic promotion into durable memory.

## Commands

Use these commands inside `aworld-cli`:

```text
/memory
/memory view
/memory status
/remember --type workspace Use pnpm and keep tests fast in this workspace
```

Recommended usage:

- Use `/remember` for stable one-line preferences you want to persist immediately.
- Use `/memory` when you want to edit the workspace instruction file directly.

## Storage Layout

For a workspace at `/path/to/workspace`, CLI memory uses:

- Global instructions: `~/.aworld/AWORLD.md`
- Workspace instructions: `/path/to/workspace/.aworld/AWORLD.md`
- Durable records: `/path/to/workspace/.aworld/memory/durable.jsonl`
- Promotion metrics: `/path/to/workspace/.aworld/memory/metrics/promotion.jsonl`
- Session logs: `/path/to/workspace/.aworld/memory/sessions/<session_id>.jsonl`

## Expected Behavior

### Explicit durable memory

When you run:

```text
/remember --type workspace Always use pnpm for workspace package management and never run npm install here.
```

the CLI should:

- append a durable record to `durable.jsonl`
- append or update remembered guidance in `.aworld/AWORLD.md`

### Task-completed memory evaluation

When a normal task finishes with a non-empty final answer, the CLI should:

- append a promotion decision to `promotion.jsonl`
- append a task-completed session entry to `sessions/<session_id>.jsonl`

This happens even when the answer is **not** a durable instruction.

Example:

- Asking `你是谁` should usually produce:
  - `promotion: "session_log_only"`
  - `confidence: "low"`
  - `eligible_for_auto_promotion: false`
- It should **not** mutate `.aworld/AWORLD.md` or `durable.jsonl`.

## Auto-Promotion

Auto-promotion is controlled by:

```bash
export AWORLD_CLI_ENABLE_AUTO_PROMOTION=1
```

Accepted truthy values are:

- `1`
- `true`
- `yes`
- `on`

Default behavior:

- If `AWORLD_CLI_ENABLE_AUTO_PROMOTION` is unset, auto-promotion is **disabled**.
- This is intentional. It avoids polluting durable workspace memory with ordinary answers or weak heuristics.

Auto-promotion only happens when all of the following are true:

- auto-promotion is enabled
- the evaluated candidate is marked `eligible_for_auto_promotion: true`
- the candidate confidence is `high`
- the candidate memory type is a supported instruction memory type

Example of a high-confidence instruction candidate:

```text
Always use pnpm for workspace package management and never run npm install here.
```

Expected result when auto-promotion is enabled:

- `promotion.jsonl` records `promotion: "durable_memory"`
- `durable.jsonl` gets a new record
- `.aworld/AWORLD.md` is updated with remembered guidance

## Verification

### Verify explicit durable memory

1. Run:

   ```text
   /remember --type workspace Use pnpm and keep tests fast in this workspace
   ```

2. Check:

   ```bash
   more .aworld/AWORLD.md
   more .aworld/memory/durable.jsonl
   ```

### Verify task-completed session logging

1. Start the CLI from the target workspace.
2. Ask a simple question.
3. Check:

   ```bash
   more .aworld/memory/metrics/promotion.jsonl
   ls -la .aworld/memory/sessions
   more .aworld/memory/sessions/<session_id>.jsonl
   ```

Expected:

- both `metrics/` and `sessions/` exist under the current workspace
- the session log contains the correct `workspace_path`

### Verify auto-promotion

1. Enable it:

   ```bash
   export AWORLD_CLI_ENABLE_AUTO_PROMOTION=1
   ```

2. Start the CLI from the workspace.
3. Give a strong stable instruction, for example:

   ```text
   Always use pnpm for workspace package management and never run npm install here.
   ```

4. Check:

   ```bash
   more .aworld/AWORLD.md
   more .aworld/memory/durable.jsonl
   more .aworld/memory/metrics/promotion.jsonl
   ```

Expected:

- the instruction is present in `.aworld/AWORLD.md`
- `durable.jsonl` contains a new record
- `promotion.jsonl` shows `promotion: "durable_memory"`

## Notes

- If you are testing from source, prefer `python -m aworld_cli.main` to ensure you are running the checkout you expect.
- If memory output appears under another checkout or only under `~/.aworld`, first verify which `aworld_cli` package your Python environment is importing.
