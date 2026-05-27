# 2026-04-27-ralph-session-loop-plugin

Track the phase-1 design lineage that ultimately landed as the shared `/goal` session loop in AWorld.

Implementation note:

- the user-facing entrypoint is now `/goal`
- `/goal "..."` starts a session goal directly
- `/goal status`, `/goal pause`, and `/goal clear` control the persisted goal state
- continuation is driven from task lifecycle hooks, while the `stop` hook only blocks accidental exit from an active goal
