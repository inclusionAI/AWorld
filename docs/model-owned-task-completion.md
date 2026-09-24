# Model-owned task completion

AWorld's default agent completes a task when the model emits a normal final
response without tool calls. Task correctness belongs to an independent
evaluator. Response wording, length, repetition, artifact checks, and inferred
goal progress do not override the model's completion decision.

The default CLI completion mode is `off`. Callers that deliberately need the
legacy contract API can select `observe` or `enforce`. WORKBENCH remains an
optional tool; its operations are not mandatory steps in the agent prompt.

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
