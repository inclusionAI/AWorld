# 2026-04-27-ralph-session-loop-plugin

Track the phase-1 design for introducing Ralph session-loop commands in AWorld.

Implementation note:

- the user-facing Ralph commands remain `/ralph-loop` and `/cancel-ralph`
- the shared persisted contract now lives in the built-in `goal-session` plugin
- continuation is driven from task lifecycle hooks, while the `stop` hook only blocks accidental exit from an active goal
