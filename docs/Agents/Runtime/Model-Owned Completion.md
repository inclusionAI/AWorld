# Model-owned task completion

AWorld's default agent completes a task when the model emits a normal final
response without tool calls. Task correctness belongs to an independent
evaluator. Response wording, length, repetition, artifact checks, and inferred
goal progress do not override the model's completion decision.

The bundled agent uses success-first execution by default. Ordinary execution
is observed rather than steered by the convergence protocol, but a candidate
that reaches the terminal boundary enters one bounded, model-owned checkpoint,
review, and (when justified) repair path. This review does not manufacture an
external pass: it either returns the best current candidate, performs a bounded
repair supported by observed evidence, or fails open with accurate limitations
when review is unavailable. Set `AWORLD_SUCCESS_FIRST_TERMINAL_GATE=false` only
as an explicit rollback of this terminal safeguard.

The default CLI completion mode is `off`. Callers that deliberately need the
generic contract API can select `observe` or `enforce` and supply explicit
artifact paths or validation commands. Paths inferred from task wording are
published only as advisory deliverable hints; the legacy
`AWORLD_INFER_REQUIRED_ARTIFACTS` flag does not create a contract. Workbench is
a separately selected standalone Skill/CLI; it is not a core Tool or completion
requirement.

Agent and built-in swarm step limits are disabled by default. Explicit step
limits and generation watchdogs remain available to callers. Transport errors,
interrupted streams, invalid tool arguments, cancellation, and externally
supplied process deadlines still retain their existing failure semantics.

The generic `ContextCompilerRuntimeConfig` defaults to `budget_pressure`, with a
checkpoint threshold at 95% of the available input budget. The bundled CLI
Agent deliberately overrides that library default with `adaptive` so repeated
operations, low-information observations, and durable progress can trigger an
earlier checkpoint during long work. Both policies preserve the task, system
instructions, work state, recent evidence, and complete assistant/tool groups;
neither treats a checkpoint as proof of completion.

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
state without suppressing explicit project instructions or artifacts in the task
working directory. An `agent.md` discovered at runtime may not replace a collaborator
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
settings. The bundled `long-running-agent` skill is enabled by default but tells
short tasks to bypass milestones and checkpoints; Workbench is bundled but
disabled by default and remains available through explicit skill selection.
