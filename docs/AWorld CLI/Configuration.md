# Configuration

Configure AWorld CLI once, then reuse the same environment across workspaces.

## Interactive Setup

Run the built-in configuration flow:

```bash
aworld-cli --config
```

## Workspace `.env`

You can also place a `.env` file in the working directory and start `aworld-cli` from the same location.

Required variables:

```bash
LLM_MODEL_NAME="your_model_name"
LLM_PROVIDER="openai"
LLM_API_KEY="your_model_api_key"
LLM_BASE_URL="your_base_url"
```

Recommended notes:

- Use a strong coding-capable model for the main developer flow.
- Keep the `.env` file in the same workspace where you run `aworld-cli`.
- If you use `aworld-cli --config`, the CLI writes the equivalent configuration for you.

## Runtime behavior

The built-in `Aworld` agent keeps task completion model-owned. These settings
change runtime boundaries; they do not provide benchmark or evaluator authority.

| Variable | Default | Purpose |
| --- | --- | --- |
| `AWORLD_CONTROL_ROOT` | framework default | Relocates AWorld-owned state without changing the task working directory. |
| `AWORLD_CONTEXT_WINDOW_TOKENS` | model-aware | Declares the deployment context window. |
| `AWORLD_CONTEXT_LIMIT_TOKENS` | unset | Overrides the compiler context window when set. |
| `AWORLD_COMPLETION_MODE` | `off` | Selects `off`, `observe`, or `enforce` for an explicit completion contract. |
| `AWORLD_REQUIRED_ARTIFACTS_JSON` | unset | Supplies a JSON array of required artifact paths. |
| `AWORLD_VALIDATION_COMMANDS_JSON` | unset | Supplies explicit validation commands; commands are never inferred from model text. |
| `AWORLD_TOOL_SURFACE_PROFILE` | `general` | Selects `general` or process-local `one_shot` lifecycle policy. |
| `AWORLD_TOOL_SURFACE_MODE` | `observe` | Validates the live tool schema; `enforce` fails before model execution when required schemas are missing. |
| `AWORLD_NATIVE_FILESYSTEM_TOOLS` | `off` | Exposes fine-grained filesystem tools directly to the model when enabled. |
| `AWORLD_BUILTIN_SUBAGENTS` | `none` | Enables an exact optional collaborator set, `core`, or `all`. |
| `AWORLD_SUCCESS_FIRST` | enabled | Leaves ordinary execution strategy with the model and keeps protocol observation active. |
| `AWORLD_SUCCESS_FIRST_TERMINAL_GATE` | enabled | Keeps the bounded terminal checkpoint, review, and repair path. |
| `AWORLD_REQUIRE_STREAM_FINISH_REASON` | disabled | Requires an explicit provider `finish_reason`; supervised runtimes commonly enable it. |

`AWORLD_COMPLETION_MODE` alone does not create a contract. Use `observe` or
`enforce` together with `AWORLD_REQUIRED_ARTIFACTS_JSON` and/or
`AWORLD_VALIDATION_COMMANDS_JSON`. Natural-language deliverable detection remains
advisory, and the legacy `AWORLD_INFER_REQUIRED_ARTIFACTS` flag is ignored.

The generic context compiler defaults to capacity-triggered checkpoints. The
built-in CLI Agent overrides that library default with adaptive checkpoints for
long-running work. See [Context budget and offload](../Agents/Context%20Budget%20and%20Offload.md),
[execution completion and progress](../Agents/Runtime/Completion%20and%20Progress.md),
and [model-owned task completion](../Agents/Runtime/Model-Owned%20Completion.md) for the
behavioral contracts.
