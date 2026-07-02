# AWorld CLI Resume Design

## Date
2026-07-01

## Status
Implemented

## Goal

Add a first-class `aworld-cli resume` capability that can reopen a previous AWorld CLI session by session id, rebuild the effective conversation context that existed when the session ended, and continue chatting as if the process had not exited.

The core success criterion is continuity: after `aworld-cli resume <session-id>`, the next user turn should have access to the same relevant session history, memory summaries, agent identity, cwd, and runtime metadata that an uninterrupted multi-turn conversation would have had.

The target user experience is intentionally similar to:

```bash
codex resume 019f1d05-4529-7201-9733-4e6bdb5cc768
```

For AWorld CLI, the equivalent should be:

```bash
aworld-cli resume <session-id>
aworld-cli resume <session-id> "continue the previous task"
aworld-cli resume --last
```

## Scope

Included:

- local `aworld-cli` session discovery and resumption
- a top-level `resume` command
- interactive resume by explicit session id
- interactive resume of the latest session
- optional immediate prompt after resume
- cwd-scoped session selection by default
- explicit `--all` cross-cwd lookup
- explicit inclusion of direct-run sessions
- session metadata persistence needed for reliable resume
- in-chat `/restore <session-id>` and `/sessions` improvements that share the same underlying session store
- a shared restore core used by both top-level `resume` and in-chat `/restore`
- tests for session store, command parsing, cwd filtering, and resume handoff into runtime/executor creation

Excluded from this change:

- Codex-compatible storage format
- cloud task resume
- remote backend session replay guarantees beyond passing the selected session id to `RemoteAgentExecutor`
- archive/delete/unarchive/fork commands
- transcript viewer UI beyond enough CLI-visible transcript replay to make a resumed terminal look like the session at exit
- full migration of every historical `~/.aworld/cli_history.jsonl` record into rich session records
- changing AWorld framework memory semantics outside what is needed to select the correct `session_id`

## Problem

AWorld CLI already has several session-related pieces, but they do not form a reliable resume feature:

- `BaseAgentExecutor` creates and records session ids in `.aworld/workspaces/.session_history.json`, but the record only contains `session_id`, `created_at`, and `last_used_at`.
- `restore_session()` changes `self.session_id`, but it does not validate agent availability, cwd ownership, mode, source metadata, or transcript presence.
- direct run mode accepts `--session-id`, but interactive mode has no top-level way to start inside an existing session.
- `/restore` restores only the latest session and does not parse an explicit id.
- `/sessions` is currently a debug dump rather than a user-facing session picker/list.
- `JSONLHistory` stores command-style prompt history and token stats, but it is not a session index and should not become the sole source of truth for resume.

The missing product contract is: “A session is a durable resumable object with enough metadata to find it, validate it, select the right agent, and continue with the same `session_id`.”

## Product Decisions

### 1. Resume must rebuild effective context, not only reuse an id

The MVP resumes by reusing the selected `session_id` in the existing AWorld context/memory pipeline, but that is not sufficient by itself. Resume must prove that the next task can reconstruct the effective context from the previous session end.

Effective context means the agent can see the same relevant prior dialogue and summarized/session memory it would have seen if the user had kept the original process open and sent one more message.

This does not require mechanically replaying every old event into a new process. It does require a durable context-reconstruction path backed by session-scoped memory/history. If the current AWorld context/memory pipeline cannot fully reconstruct the session-end context from `session_id`, this change must add the missing durable session transcript or summary material needed to make resume behavior equivalent to uninterrupted multi-turn chat.

### 2. A session store becomes the source of truth for discovery, not context alone

Add a dedicated AWorld CLI session store for resumable session metadata. Existing history files may remain useful, but resume selection should not depend on scanning `~/.aworld/cli_history.jsonl`.

The store should live under the workspace by default:

```text
.aworld/sessions/index.json
```

The store owns:

- durable session metadata
- latest-session selection
- cwd filtering
- agent/source metadata needed to reopen the right runtime
- archived/deleted-ready fields, even if commands for those fields are out of scope

The session store is not the complete conversation context store. It should point to, or be updated alongside, the durable session history/memory artifacts that allow context reconstruction.

### 3. Existing session ids remain valid

Do not require UUID-style ids. AWorld currently creates ids like:

```text
session_YYYYMMDDHHMMSS_<8-hex>
```

The resume command must accept existing AWorld session ids. If future ids become UUIDs, the store should support them without requiring this change to redesign the command.

### 4. Default lookup is current workspace scoped

`aworld-cli resume` should default to sessions whose stored `cwd` matches the current working directory after path resolution.

`--all` disables cwd filtering and shows/accepts sessions from all known cwd values in the store.

### 5. Direct-run sessions are recorded but hidden by default

Direct runs using `aworld-cli run --task ...` should be recorded in the session store with `mode=direct`.

Default resume selection should target interactive sessions. `--include-non-interactive` includes direct-run sessions in `--last` selection and session listing.

Explicit `aworld-cli resume <session-id>` may resume a direct-run session even without `--include-non-interactive`, because an explicit id is a stronger user intent than the default filter.

### 6. Resume and restore share one core implementation

`aworld-cli resume <session-id>` and `/restore <session-id>` are different user entry points for the same underlying operation: recover a known session into a live executor/runtime so the next turn continues under that session id and its reconstructed context.

The two entry points must not maintain separate restore semantics. Their only differences are lifecycle concerns:

- `aworld-cli resume` starts from a cold process, bootstraps runtime, loads or selects the agent, creates an executor, then applies the shared restore operation.
- `/restore` runs inside an existing interactive process and applies the same shared restore operation to the current executor.
- `aworld-cli resume <session-id> <prompt>` applies the same restore operation first, then submits `<prompt>` as the next user turn in the restored session.

Any behavior that defines whether a session can be restored, how the executor is updated, how HUD/tool logging is reset, how the session store is touched, and how limited-context warnings are produced belongs in the shared restore core, not in either entry point.

## User-Facing CLI Contract

### Top-level command

Add:

```bash
aworld-cli resume [OPTIONS] [SESSION_ID] [PROMPT]
```

Arguments:

- `SESSION_ID`: optional session id or session name/alias when names are introduced later
- `PROMPT`: optional follow-up prompt to execute immediately after resume

Options:

- `--last`: resume the latest matching session without showing an interactive picker
- `--all`: disable cwd filtering
- `--include-non-interactive`: include direct-run sessions in picker and `--last`
- `--agent <name>`: override stored agent name when the original agent is unavailable or the user intentionally wants a different agent
- existing runtime setup options should remain available where they matter:
  - `--skill`
  - `--env-file`
  - `--remote-backend`
  - `--agent-dir`
  - `--agent-file`
  - `--skill-path`

Initial MVP behavior:

- if `SESSION_ID` is provided, resume that session directly
- if `--last` is provided and no `SESSION_ID` is provided, resume the latest matching session
- if neither `SESSION_ID` nor `--last` is provided, print a compact numbered list and ask the user to choose when running in a terminal
- if no terminal is available and no `SESSION_ID`/`--last` is provided, fail with a clear message

### Examples

```bash
aworld-cli resume session_20260701121000_ab12cd34
aworld-cli resume session_20260701121000_ab12cd34 "continue from the last result"
aworld-cli resume --last
aworld-cli resume --all --last
aworld-cli resume --include-non-interactive --last
aworld-cli resume --agent Aworld session_20260701121000_ab12cd34
```

### Interactive slash commands

Improve in-chat commands to use the same session store:

```text
/sessions
/sessions list
/sessions show <session-id>
/restore <session-id>
/latest
```

Rules:

- `/restore <session-id>` switches the current executor to the selected session after validation.
- `/restore` without an id behaves like `/latest`.
- `/latest` uses the same current-cwd and interactive-session default filters as `aworld-cli resume --last`.
- `/sessions` and `/sessions list` show a compact table, not executor debug attributes.
- `/sessions show <session-id>` shows stored metadata and recent prompt summary if available.

## Session Store Model

### Record shape

Use a dataclass-backed model with explicit JSON serialization.

Recommended fields:

```python
@dataclass
class CliSessionRecord:
    session_id: str
    created_at: str
    updated_at: str
    cwd: str
    agent_name: str
    mode: str  # "interactive" or "direct"
    source_type: str | None = None
    source_location: str | None = None
    title: str | None = None
    last_prompt: str | None = None
    last_task_id: str | None = None
    turn_count: int = 0
    archived: bool = False
    deleted_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

### Store API

Recommended API:

```python
class CliSessionStore:
    def __init__(self, root: Path | None = None): ...

    def upsert_session(self, record: CliSessionRecord) -> CliSessionRecord: ...

    def record_turn(
        self,
        *,
        session_id: str,
        cwd: str,
        agent_name: str,
        mode: str,
        prompt: str,
        task_id: str | None,
        source_type: str | None,
        source_location: str | None,
    ) -> CliSessionRecord: ...

    def get(self, session_id: str) -> CliSessionRecord | None: ...

    def list(
        self,
        *,
        cwd: str | None,
        include_all_cwds: bool = False,
        include_non_interactive: bool = False,
        include_archived: bool = False,
    ) -> list[CliSessionRecord]: ...

    def latest(
        self,
        *,
        cwd: str | None,
        include_all_cwds: bool = False,
        include_non_interactive: bool = False,
    ) -> CliSessionRecord | None: ...
```

## Shared Restore Core

Add a core module owned by AWorld CLI session management:

```text
aworld_cli.core.session_restore
```

Recommended API:

```python
@dataclass
class SessionRestoreResult:
    record: CliSessionRecord
    message: str
    warning: str | None = None


def resolve_session_record(
    *,
    session_store: CliSessionStore,
    session_id: str | None,
    cwd: str,
    use_latest: bool = False,
    include_all_cwds: bool = False,
    include_non_interactive: bool = False,
) -> CliSessionRecord | None: ...


def restore_session_to_executor(
    *,
    record: CliSessionRecord,
    executor_instance: Any,
    session_store: CliSessionStore,
    current_agent_name: str | None = None,
    require_same_agent: bool = True,
) -> SessionRestoreResult: ...
```

Responsibilities:

- validate that an executor exists
- reject agent mismatch when `require_same_agent=True`
- set `executor_instance.session_id = record.session_id`
- restart tool logging when the executor supports it
- reset HUD session state when the runtime supports it
- touch the session store record
- return any limited-context warning from `CliSessionStore.context_warning(record)`

Entry-point responsibilities:

- `resume` resolves agent/runtime options, creates the executor/runtime, then invokes the shared restore core before accepting the next user turn.
- `/restore` parses the slash command and delegates to the shared restore core.
- neither entry point should call legacy `BaseAgentExecutor.restore_session()` for explicit resume ids, because that legacy method creates a new session when the id is missing.

### Migration and compatibility

On first use, the session store should import lightweight records from the existing workspace `.aworld/workspaces/.session_history.json` when the new index does not exist.

Imported records should:

- preserve `session_id`, `created_at`, and `last_used_at`
- set `updated_at` from `last_used_at`
- set `cwd` to the current workspace path where the legacy file was found
- set `agent_name` to the current/default agent only when the old record does not know the agent
- set `mode` to `interactive`
- mark `metadata["imported_from"] = ".aworld/workspaces/.session_history.json"`

This import is best-effort. It should not block normal CLI startup if the legacy file is malformed.

## Runtime Integration

### Recording sessions

Every interactive and direct run should update the session store.

Interactive path:

- when an executor is created, ensure the session has a store record
- before or after each successful task start, record the prompt and task id
- update `updated_at` when a session is restored or used

Direct run path:

- when `_run_direct_mode()` creates an executor, ensure a record with `mode=direct`
- record the direct prompt and task id when available

The existing `JSONLHistory` writes should remain. The session store does not replace token/cost history.

### Context reconstruction

Resume must restore enough durable context that the next turn behaves like an uninterrupted follow-up turn.

Before executing any resumed prompt or entering the resumed interactive loop, the runtime must ensure:

- the selected `session_id` is passed into executor creation and task construction
- the selected agent id/name is the same one used to resolve session-scoped memory, unless explicitly overridden
- the AWorld context/memory layer can load prior session messages, summaries, or equivalent durable state for that `session_id`
- any CLI-owned transcript or summary artifact required for context reconstruction is available and well-formed
- if required context artifacts are missing, the user receives a warning that resume will continue with limited context rather than silently claiming full continuity

Implementation planning must verify the current AWorld memory path. If existing session-scoped memory already provides complete continuity, tests should lock that in. If it does not, this change must add a minimal durable session transcript/summary writer and loader before `resume` is considered complete.

### Resuming into runtime

Top-level `resume` should:

1. load and validate the target session record
2. resolve the agent name:
   - use `--agent` when provided
   - otherwise use `record.agent_name`
   - otherwise fall back to `Aworld` with a warning for imported legacy records
3. bootstrap runtime with the usual env/skill/plugin options
4. create `CliRuntime(..., session_id=record.session_id)`
5. create the executor for the selected agent
6. apply the shared restore core to the executor/runtime
7. start interactive mode using that restored session id
8. rebuild the effective session context for `record.session_id`
9. if a prompt argument exists, execute it once in the resumed session before returning to the interactive prompt or exiting according to the selected mode

MVP behavior for prompt-after-resume:

- `aworld-cli resume <id> "prompt"` means: first restore session `<id>`, then submit `"prompt"` as the next user turn inside that restored session, with the rebuilt prior context available to the agent.
- The prompt must not start a new unrelated session.
- The prompt must be recorded as a new turn for the restored session.
- After that turn completes, the CLI should enter interactive mode in the same restored session.
- A later implementation may add `--non-interactive` for resume, but it is not part of this MVP.

### Restore inside an existing chat

`/restore <session-id>` should:

- validate the record in the store through the shared restore core
- update `executor_instance.session_id` through the shared restore core
- restart tool logging for the restored session through the shared restore core
- reset HUD session state for the new session id through the shared restore core
- update session store `updated_at` through the shared restore core
- not rebuild the agent unless the current agent differs from the stored agent

If the stored agent differs from the current agent:

- default behavior: warn and require `/switch <agent>` or top-level `aworld-cli resume --agent ...`
- do not silently switch agents inside `/restore`

## Error Handling

### Missing session

For explicit session id:

```text
Session not found: <session-id>
Use `aworld-cli resume --all` to include sessions from other workspaces, or `/sessions list`.
```

Do not create a new session when an explicit resume id is missing. This differs from current `restore_session()` behavior and is important for user trust.

### No latest session

For `--last` with no match:

```text
No resumable sessions found for <cwd>.
Start a session with `aworld-cli interactive`, or use `--all` to search all workspaces.
```

### Agent unavailable

When a stored agent cannot be loaded:

```text
Session <id> was created with agent <agent>, but that agent is not available.
Pass `--agent <name>` to resume with a different agent.
```

### CWD mismatch

Explicit id with different cwd should succeed only with a warning:

```text
Session <id> belongs to <stored-cwd>; current cwd is <cwd>.
Continuing because the session id was explicit.
```

List and `--last` should keep cwd filtering unless `--all` is provided.

## Security and Privacy

The session index should not persist full assistant transcripts or tool outputs.

The CLI may persist a separate session transcript artifact for terminal replay because the
updated resume goal requires `aworld-cli resume <session-id>` to repaint the visible
conversation before showing the next prompt. This transcript should contain only the
CLI-visible user input, assistant final answer, agent name, task id, and timestamps needed
to reconstruct the terminal view. It should not live in the session index.

The same shared restore core also prepares bounded OpenAI-compatible `user`/`assistant`
messages by merging the dedicated transcript artifact with legacy session history/memory,
then removing exact duplicate events. The restored messages are consumed once into
`TaskInput.messages` by the next task under the restored session. The current
`task_content` remains the user's new prompt, while the LLM request receives prior turns as
separate chat messages instead of a transcript pasted into one user message. Terminal
replay and model-context restoration are separate consumers of the same session
reconstruction module.

Allowed metadata:

- session id
- timestamps
- cwd
- agent/source metadata
- last prompt preview
- task id
- token/cost summary references
- CLI-visible user/assistant transcript events in the dedicated transcript artifact

Prompt previews should be truncated for display and storage. The full prompt remains in existing history/memory systems, which already carry their own privacy expectations.

Do not store environment variables, API keys, raw tool outputs, or file contents in the session index or transcript artifact.

## Testing Strategy

### Unit tests

Add focused tests for:

- creating a new session store index
- upserting records
- listing by cwd
- `--all` behavior
- `--include-non-interactive` behavior
- latest-session selection by `updated_at`
- explicit id lookup bypassing default cwd/mode filters
- malformed legacy `.session_history.json` import does not crash
- explicit missing id does not create a new session

Recommended location:

```text
tests/core/test_cli_session_store.py
```

### Command parser tests

Add tests for:

- `aworld-cli resume <id>`
- `aworld-cli resume <id> <prompt>`
- `aworld-cli resume --last`
- `aworld-cli resume --all --last`
- runtime setup options are accepted

Recommended locations:

```text
tests/test_cli_help.py
tests/test_plugin_cli_entrypoint.py
tests/core/test_top_level_invocation.py
```

### Runtime integration tests

Add tests with fake runtime/executor boundaries for:

- shared restore core updates executor session id, restarts tool logging, resets HUD, touches the store, and returns context warnings
- top-level `resume` delegates executor restore behavior to the shared restore core rather than duplicating it
- resume command creates `CliRuntime(..., session_id=<id>)`
- prompt-after-resume calls the executor with the provided prompt under the restored session id
- resumed prompts see prior session context through the same context/memory path used by uninterrupted multi-turn chat
- missing context artifacts produce an explicit limited-context warning rather than silently claiming full continuity
- `aworld-cli resume <session-id>` replays the CLI-visible transcript before showing the next interactive prompt
- `/restore <session-id>` updates executor session id and store `updated_at`
- `/sessions list` renders user-facing records rather than debug attributes

Recommended locations:

```text
tests/test_slash_commands.py
tests/test_interactive_steering.py
tests/core/test_top_level_invocation.py
```

## Acceptance Criteria

1. `aworld-cli resume <session-id>` starts an interactive session using the selected session id, replays the CLI-visible transcript, and restores the effective context from the previous session end.
2. A resumed session's next turn has access to prior session dialogue/memory/summaries equivalent to an uninterrupted multi-turn conversation.
3. `aworld-cli resume <session-id> "prompt"` first resumes `<session-id>`, then executes `"prompt"` as the next user turn under that resumed session id and rebuilt context, then enters the interactive prompt.
4. prompt-after-resume records the prompt as a new turn on the restored session, not as a new unrelated session.
5. `aworld-cli resume --last` resumes the latest non-archived interactive session for the current cwd.
6. `aworld-cli resume --all --last` can resume a latest session from another cwd.
7. direct-run sessions are recorded with `mode=direct` but excluded from default `--last`.
8. `--include-non-interactive` includes direct-run sessions in latest/list selection.
9. explicit missing session ids fail clearly and do not create new sessions.
10. stored agent metadata is used to select the default agent for resume.
11. missing stored agents fail clearly unless `--agent` overrides the agent.
12. missing or incomplete context artifacts produce an explicit limited-context warning.
13. `/restore <session-id>` restores a specific known session in the current chat.
14. `/sessions list` displays resumable sessions with id, agent, mode, cwd indicator, updated time, and last prompt preview.
15. existing `--session-id` direct run behavior remains compatible.
16. existing `JSONLHistory` cost and prompt history behavior remains compatible.
17. `aworld-cli resume <session-id>` and `/restore <session-id>` use the same shared restore core for executor/runtime mutation.
18. focused tests cover the new session store, context reconstruction, top-level command parsing, and restore/resume runtime handoff.
19. future completed turns are recorded to a dedicated transcript artifact so a later resume can repaint the terminal without relying on lossy legacy history.

## Rejected Alternatives

### Use only `~/.aworld/cli_history.jsonl`

Rejected because `JSONLHistory` is optimized for prompt history and cost accounting, not session discovery. It lacks reliable agent/source metadata and would require expensive scans for common resume operations.

### Make `restore_session()` create missing sessions for resume

Rejected because `resume <id>` should be trustworthy. A typo should fail; it should not silently create a new session and make the user believe previous context was restored.

### Implement archive/delete/fork in the same change

Rejected for MVP scope. The session model should reserve fields for these lifecycle commands, but the first implementation should deliver reliable resume/list/restore before expanding lifecycle management.

### Blindly replay raw transcripts to rebuild context

Rejected as the primary MVP mechanism because AWorld already has session-scoped memory/context machinery. Blind transcript replay would introduce ordering, tool-output, and security risks.

This rejection does not remove the requirement to rebuild effective context. If the existing memory path is insufficient, the implementation should add a minimal durable session transcript or summary path designed for context reconstruction, not replay arbitrary raw terminal output.

## Implementation Decisions

The implemented MVP uses these choices:

1. use a numbered text prompt first
2. always enter interactive mode after the immediate prompt
3. use workspace-local index first and defer a global index until a later change
