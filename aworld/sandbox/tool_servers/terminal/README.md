# Built-in terminal server

`run_code` executes a shell command in the terminal server's current workspace.
Its timeout defaults to 300 seconds and is clamped to 3600 seconds.

## Bounded output capture

Stdout and stderr are drained concurrently in fixed-size chunks. The server only
retains a head-and-tail excerpt, so a verbose command cannot make the server hold
its complete output in memory or fill a temporary output file.

The combined retained stdout/stderr budget defaults to 1 MiB. Configure it with
`AWORLD_TERMINAL_CAPTURE_MAX_BYTES`; `TERMINAL_CAPTURE_MAX_BYTES` is accepted as
an alias. Values are clamped between 2 KiB and a non-overridable 16 MiB framework
hard limit. The total budget is split between stdout and stderr.

When output crosses the limit, the returned excerpt contains its head, tail, and
an explicit omitted-byte marker. The result metadata also reports total and
omitted bytes, the applied capture limit, and whether capture completed. Excess
bytes continue to be drained without retention so the child cannot deadlock on a
full pipe.

No implicit output spool is written to disk. A command can still explicitly
redirect its own output to a file when the caller needs a complete record.

If a background child inherits stdout or stderr after its launching shell exits,
`run_code` returns after a short flush window and leaves constant-memory,
drain-only readers attached. The result marks this as incomplete detached output.
On timeout or cancellation, the server terminates the shell's process group,
closes any surviving pipes, and reaps the shell.

The bounded output exists once in the response `message`. `metadata.output_data`
is `null`, and terminal responses are not duplicated into a workspace artifact.
