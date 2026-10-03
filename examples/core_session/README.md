# Agent + Context + Tool demo

Run from the repository root with Python 3.10 or later. The new kernel requires
only the standard library:

```sh
AWORLD_DISABLE_AUTO_DOTENV=1 python examples/aworld/demo.py
```

The configured `Agent` owns one model/tool loop. `Context` owns the canonical
history and prepares request views. `Tool` is an async function invoked directly.
Session / Run provide admission, cancellation, results and event observation.
No event bus or legacy Runner dispatches model or tool calls.

```python
from aworld.core.agent import Agent
from aworld.core.context import Context
from aworld.core.tool import Tool
from aworld.core.session import create_session

agent = Agent(model=model, tools=[tool], max_turns=20)
session = await create_session(agent=agent, context=Context())
run = await session.submit("Compute the result")
result = await run.result()
```

A model adapter implements `async complete(ModelRequest) -> AssistantMessage`.
`AssistantMessage` may contain text and `ToolCall` records. The loop confirms the
assistant message, awaits tools, writes paired results, then prepares the next
request. A response without tool calls ends the run. Tool errors return to the
model as error results; cancellation propagates. The first model boundary is text
and finalized responses, without provider streaming or multimodal support.

The deterministic local model in this example calls an actual `add` Tool. The
first task returns `5`, and the same context continues with `4` to return `9`.
Each task makes two model requests. Context retains ten confirmed entries.

To persist context through the original file storage backend, Pydantic 2 is the
only additional storage dependency:

```sh
AWORLD_DISABLE_AUTO_DOTENV=1 python examples/aworld/demo.py --context-dir /tmp/aworld-context-demo
```

Storage is an internal option of Context. The bridge reuses the original backend
without starting MemoryFactory, model clients, tokenizers, tracing or installers.
Persisting history does not recover live Session / Run ownership after restart.
The bridge requires JSON payloads; the default context supports deepcopy-able data.

The default Agent has six tools: read, write, bash, read_session,
search_sessions and session_query. Physical execution defaults to LocalSandbox:

```python
from aworld.core.agent import Agent, Skill, load_skills
from aworld.core.sandbox import LocalSandbox
from aworld.core.tool import ToolRegistry, default_tools
from aworld.core.session import InMemorySessionStore, create_session

store = InMemorySessionStore()
tools = default_tools(sandbox=LocalSandbox("/path/to/project"))
agent = Agent(model=model, tools=tools, skills=[Skill("notes", "Write design notes", "Verify files after writing.")])
session = await create_session(agent=agent, store=store, metadata={"project": "aworld"})
# Another session using this same store can query/read this session.
print(agent.tools.names)
# Pass this restricted registry to another Agent if it should only read files:
readonly = ToolRegistry(tools).select("read")
```

Sandbox carries cwd/files/processes; Tools expose operations; Agent sees tools
and instructions. The lightweight LocalSandbox imports no legacy sandbox stack.
A remote/container carrier can implement async read/write/bash and be passed to
`default_tools(sandbox=...)`. Local cwd is an execution location; absolute paths
and paths outside it are supported. It provides no OS access isolation.
Local Bash currently requires POSIX and /bin/bash; it skips shell profiles.
Read supports paginated UTF-8 text (2000 lines/50 KiB), write replaces complete
files and creates parents. Bash defaults to a 120-second timeout, kills its process
group on timeout/cancellation, and preserves structured failure output for the
model. Truncated output keeps a tail and points to a full temporary log.

Explicit `tools` replaces the defaults; `tools=[]` disables them. Registry names
must be unique. `select` and `extend` return separate registries; no global manager
is involved. Custom parameter validation uses Tool.validate_arguments.

Cross-session model tool call arguments:

```json
{"name": "session_query", "arguments": {"query": {"metadata": {"project": "aworld"}, "state": "idle", "text": "context"}, "limit": 20}}
```

The response contains matching session IDs, metadata, snippets and pagination.
Use `read_session` with session_id and offset/limit to read confirmed history.
`search_sessions(query="context")` is the text-search shorthand. Query filters
session_ids / metadata / state / text combine with AND; empty query lists visible
sessions. Closed sessions remain readable. These tools never execute agents.
Default scope is the invoking session's explicit store, not machine-wide history.
For restricted views use `session_tools(store, allowed_session_ids=[...])` from
aworld.core.tool and select the other desired default tools separately.
Search scans live in-memory history; it has no index or restart recovery.

Inline Skills add instructions and explicit Tool values. `load_skills(directory)`
loads SKILL.md metadata from explicit roots, then advertises name/description/path;
the model reads the body on demand through read. That file loader requires optional
PyYAML, never installs packages or runs scripts. The default core remains stdlib.

Run actual files, bash and cross-session queries with a deterministic local model:

```sh
AWORLD_DISABLE_AUTO_DOTENV=1 python examples/aworld/tools_demo.py
```

Subagents use ordinary tools:

```python
from aworld.core.tool.subagent import subagent_tools

parent = Agent(model=parent_model, tools=[*default_tools(),
    *subagent_tools({"worker": child_agent}, max_concurrent=4)])
session = await create_session(agent=parent)
try:
    result = await (await session.submit("Delegate the work")).result()
finally:
    await session.close()
```

The six child tools are spawn_subagent, parallel_subagents, check_subagent,
wait_subagent, cancel_subagent and list_subagents. Each child gets its own history.
Foreground tasks attach to the parent run; background=true returns a task handle
that survives parent runs. Waiting cancellation preserves background work;
explicit cancellation or Session.close stops it and waits for cleanup. Handles
are scoped by session. Physical sharing is configured on the child tools; it is
not inferred from a global current context. Agent.md discovery and shared recursive
budgets remain future work. No old Task/Swarm/Runner or event bus is required.

Run core checks without third-party dependencies:

```sh
AWORLD_DISABLE_AUTO_DOTENV=1 python -m unittest discover -s tests/core/session -p 'test_*.py'
```

Memory-backend regression checks additionally need pytest and Pydantic:

```sh
AWORLD_DISABLE_AUTO_DOTENV=1 python -m pytest -q tests/memory/test_minimal_memory_store.py tests/memory/test_filesystem_memory_store.py
```
