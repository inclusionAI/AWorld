# Memory

## What It Does

The memory commands manage workspace-specific instructions for AWorld CLI. They let you inspect the active instruction files, edit the canonical workspace memory file, and understand what the CLI will read on the next task.

`/memory reload` is informational only. Current CLI behavior reads workspace memory from disk on demand, so no manual reload or agent restart is required.

## Commands

Use these commands inside an interactive CLI session:

```text
/memory
/memory view
/memory status
/memory reload
/remember --type workspace Always use pnpm in this workspace.
```

Command behavior:

- `/memory` opens the canonical workspace `AWORLD.md` file in your editor.
- `/memory view` shows the effective instruction files plus explicit durable memory records.
- `/memory status` shows the current workspace path, active read files, durable-memory paths, and promotion metrics.
- `/memory reload` explains the current no-reload-needed behavior.
- `/remember` persists an explicit durable instruction immediately.

## Storage Layout

For a workspace at `/path/to/workspace`, the CLI uses:

- Global instructions: `~/.aworld/AWORLD.md`
- Workspace instructions: `/path/to/workspace/.aworld/AWORLD.md`
- Durable records: `/path/to/workspace/.aworld/memory/durable.jsonl`
- Promotion metrics: `/path/to/workspace/.aworld/memory/metrics/promotion.jsonl`
- Session logs: `/path/to/workspace/.aworld/memory/sessions/<session_id>.jsonl`

## Typical Workflow

1. Start `aworld-cli` from the target workspace.
2. Run `/memory status` to confirm which files are active.
3. Use `/memory` to edit long-lived workspace instructions.
4. Use `/remember --type workspace ...` for short stable preferences you want to persist immediately.
5. Use `/memory view` to confirm the effective result.

## Auto-Promotion

Task-completion evaluation records promotion decisions even when the answer is not a durable instruction. Auto-promotion stays off by default.

Enable it with:

```bash
export AWORLD_CLI_ENABLE_AUTO_PROMOTION=1
```

Auto-promotion only happens when the candidate is eligible, has high confidence, and maps to a supported instruction memory type. When it triggers, the CLI updates both durable records and the workspace `AWORLD.md`.

## Notes And Limits

- If `Auto-promotion enabled: no` appears in `/memory status`, that is expected unless you explicitly set `AWORLD_CLI_ENABLE_AUTO_PROMOTION`.
- `Active read files: none` is also expected when no global or workspace instruction files exist yet.
- If `/memory` opens the wrong editor, set `EDITOR` or `VISUAL` first.
