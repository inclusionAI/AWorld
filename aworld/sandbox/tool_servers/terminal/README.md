# Built-in terminal server

`run_code` executes a shell command in the terminal server's workspace. Its
timeout defaults to 300 seconds and is clamped to 3600 seconds. Callers can set
an explicit `cwd` (relative paths resolve from the workspace) and bounded
per-command `env` overrides. Each call starts a new shell process; filesystem
and background-process state may survive, while shell-local `cd`, `export`,
aliases, functions, and activation state do not.

When `AWORLD_TASK_DEADLINE_EPOCH_SECONDS` is present, the effective command
timeout is also clamped to the trial time remaining after
`AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS`. Standalone CLI use does not set a
trial deadline and therefore retains the normal per-command timeout behavior.

## Bounded output capture

Stdout and stderr are drained concurrently in fixed-size chunks. The server only
retains a head-and-tail excerpt, so a verbose command cannot make the server hold
its complete output in memory.

The combined retained stdout/stderr budget defaults to 1 MiB. Configure it with
`AWORLD_TERMINAL_CAPTURE_MAX_BYTES`; `TERMINAL_CAPTURE_MAX_BYTES` is accepted as
an alias. Values are clamped between 2 KiB and a non-overridable 16 MiB framework
hard limit. The total budget is split between stdout and stderr.

When output crosses the limit, the returned excerpt contains its head, tail, and
an explicit omitted-byte marker. The result metadata also reports total and
omitted bytes, the applied capture limit, and whether capture completed. Excess
bytes continue to be drained without retention so the child cannot deadlock on a
full pipe.

When output crosses the inline limit, the server retains a finite checksummed
artifact (64 MiB per stream by default, with a non-overridable 512 MiB hard
maximum). `metadata.output_policy.stdout` and `.stderr` report the artifact
reference, checksum, byte counts, and whether the artifact is complete.
`read_output_artifact` retrieves bounded text or base64 chunks. Configure these
limits with `AWORLD_TERMINAL_ARTIFACT_MAX_BYTES` and
`AWORLD_TERMINAL_ARTIFACT_READ_MAX_BYTES`.

If a background child inherits stdout or stderr after its launching shell exits,
`run_code` returns after a short flush window and leaves constant-memory,
drain-only readers attached. The result marks this as incomplete detached output.
On timeout or cancellation, the server terminates the shell's process group,
closes any surviving pipes, and reaps the shell.

The bounded excerpt exists once in the response `message`, which defaults to a
compact `{stdout, stderr}` object. `metadata.output_data` is `null`; only a
truncated stream keeps its separate retrieval artifact.
