# Ralph Session Loop Plugin

Built-in interactive Ralph loop for AWorld CLI.

## Scope

This plugin implements the phase-1 Ralph model:

- loop inside the current CLI session
- continue on `exit` through the plugin `stop` hook
- keep loop state in plugin session state
- show lightweight HUD status

It does **not** implement:

- fresh-process orchestration
- `--model` overrides
- `--work-dir` overrides
- stop-hook-executed verification commands

## Commands

Start a loop with an iteration cap:

```text
/ralph-loop "Build a REST API" --max-iterations 5
```

Start a loop with declarative verification:

```text
/ralph-loop "Create a CLI tool" --verify "pytest tests/cli -q" --completion-promise "COMPLETE"
```

Cancel the active loop:

```text
/cancel-ralph
```

## Manual Smoke

1. Start `aworld-cli` interactive mode.
2. Run:

```text
/ralph-loop "Build a REST API" --verify "pytest tests/cli -q" --completion-promise "COMPLETE" --max-iterations 3
```

3. Let the agent answer once.
4. Type:

```text
exit
```

Expected:

- the session does not exit
- the stop hook prints a Ralph iteration message
- the follow-up prompt contains `Task:`, `Verification requirements:`, and `Completion rule:`
- the HUD shows `Ralph: active`

5. To end the loop manually, run:

```text
/cancel-ralph
```

6. Type `exit` again.

Expected:

- the session exits normally

## Completion Behavior

If the active loop has `--completion-promise "COMPLETE"`, the loop stops only when the most recent final answer contains:

```text
<promise>COMPLETE</promise>
```

The match is exact and case-sensitive.
