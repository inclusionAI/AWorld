# Model-owned task completion

AWorld's default agent completes a task when the model emits a normal final
response without tool calls. Task correctness belongs to an independent
evaluator. Response wording, length, repetition, artifact checks, and inferred
goal progress do not override the model's completion decision.

The default CLI completion mode is `off`. Callers that deliberately need the
generic contract API can select `observe` or `enforce` and supply explicit
artifact paths or validation commands. Workbench is a separately selected
standalone Skill/CLI; it is not a core Tool or completion requirement.

Agent and built-in swarm step limits are disabled by default. Explicit step
limits and generation watchdogs remain available to callers. Transport errors,
interrupted streams, invalid tool arguments, cancellation, and externally
supplied process deadlines still retain their existing failure semantics.

Context checkpoints default to capacity pressure at 95% of the available input
budget. Progress counters remain diagnostic. A capacity checkpoint preserves
the task, system instructions, work state, recent evidence, and complete tool
exchanges. Subsequent turns can accumulate new history until capacity pressure
returns. The previous strategy-changing `adaptive` policy is opt-in.

An external runner should save the final answer and artifacts, record any
execution failure separately, and invoke its verifier after execution ends.

## Harness boundary

Treat AWorld like any other packaged agent harness. The AWorld wheel owns task
semantics: planning, tool-use strategy, collaborator selection, checkpointing,
review, repair, and the decision to return a final answer. The outer harness may
provide model transport, credentials, a sandbox and capability set, the task
deadline, process lifecycle controls, and artifact collection. It must not
silently select or disable AWorld semantic policy for an ordinary run.

When a caller supplies `AWORLD_CONTROL_ROOT`, framework-global configuration,
skill state, installed skills, plugins, memory, and the global `AWORLD.md` layer
resolve under that root. This isolates a packaged run from ambient home-directory
state without suppressing the task workspace's explicit project instructions or
artifacts. An `agent.md` discovered at runtime may not replace a collaborator
already registered by the wheel's team.

Hidden evaluation remains outside the agent loop. A post-run verifier may score
the saved answer and artifacts, but its reward, rubric-private state, and hidden
checks are not an AWorld completion contract. Explicit single-feature ablations
are experiments and should be labeled as harness overrides rather than treated
as production defaults.

## Default automation agent

The CLI loads `Aworld` as its sole default built-in agent. Its prompt guides
planning, execution, observation, recovery, and completion of general automation
tasks. The model chooses the workflow and useful checks. Durable scheduling is
available only when the configured tool surface supports it.

Collaborators live in `aworld-cli/src/aworld_cli/builtin_agents/smllc/optional_agents`,
outside the default agent discovery directory. None is created by default: the
root model owns planning, execution, review, repair, and completion. This does not
disable the long-running execution protocol's review/repair phase. It only means
the root performs that phase without optional delegated advice. `core` explicitly
exposes `developer`, `evaluator`, and the fresh-context, read-only `verifier`.
Each selected collaborator inherits the active model, generation budget, and
sandbox. Evaluator and verifier receive only a read-only filesystem tool
allowlist; developer retains the writable implementation surface. None receives
hidden reward or grader data. Their child contexts exclude the solver transcript,
profiles, and mutable key-value scratch state; they cannot extend the parent
deadline, and only each answer plus content-free usage/request evidence is merged
back.

Unset or `AWORLD_BUILTIN_SUBAGENTS=none` disables every collaborator. `all` and the
legacy `auto` value enable every available collaborator, while a comma-separated
list selects exact names. The available names are `developer`, `evaluator`,
`verifier`, `diffusion`, `avatar`, `audio`, and `image`. Skill activation remains
independently controlled by `default_enabled`, `--skill`, and persisted user
settings. Workbench is bundled but disabled by default and remains available
through explicit skill selection.
