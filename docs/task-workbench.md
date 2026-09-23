# Local task workbench

The bundled `Aworld` agent exposes native `WORKBENCH` actions in both general and
one-shot profiles. The local executor binds the workspace and task identity;
model arguments cannot choose a store root, scope, mandatory validation policy,
or validation result. Custom remote sandboxes do not inherit local authority.
A tool error remains a repairable observation, rather than terminating the task.

Before the first model operation, public literal delivery clauses are compiled
with source quotes/spans. Ambiguous, conditional, negated and explanatory
language is reported as unresolved/excluded, not silently turned into a hard
requirement. Trusted caller contracts take precedence. Ordinary informational
requests do not require output files. Goal continuation uses one durable workspace
identity; new tasks use separate scopes. There is no aggregate task time/token
budget in this mechanism.

The default root system prompt directs the agent to inspect requirements,
preserve source data, produce a runnable baseline, probe real installed APIs
following compatibility errors, and validate actual final bytes. It names no
benchmark tasks, expected answers, hidden test paths or task-specific constants.

## Artifact lifecycle

`protect_inputs` captures source groups without forbidding legitimate in-place
edits. Only explicitly immutable inputs are prohibited from changing. Repeated
protection retains the first source bytes. `working_copy` returns isolated files;
`restore_inputs` refuses to overwrite changed existing files. Database recovery
runs on an isolated copy and preserves the original database and sidecar bytes.

`save_candidate` copies candidate bytes into a durable snapshot.
`validate_candidate` executes mandatory checks plus retained additional
self-checks, returning content-bound receipts and measured metrics. The model
cannot supply pass flags or metric results. `promote_candidate` uses the bound
policy and receipt, preserves accepted history, and journals multi-file
publication. A single file is replaced atomically. `readback` checks current
published bytes; the completion resolver executes mandatory and retained
semantic checks again. An earlier receipt never covers later edits.

Caller-provided structured contracts can be installed in
`context.context_info['task_workspace_contract']` before preparation. Outputs,
inputs and reusable checks are described in `task-workspace-validation.md`.
The optional selection policy declares hard constraints and a measured metric:

```json
{
  "outputs": [{"path": "result.csv", "checks": [
    {"id": "table", "kind": "csv", "required_columns": ["id", "value"]}
  ]}],
  "inputs": [{"path": "source.csv", "immutable": false}],
  "policy": {
    "objective": {"metric": "table.size_bytes", "direction": "minimize"},
    "hard_constraints": [{"artifact": "result.csv", "max_bytes": 1000000}]
  }
}
```

The policy is task input supplied by the caller, not a universal preference for
smaller files. With no objective, a passing candidate establishes a retained
baseline; no fabricated quality score is invented. Additional checks may tighten
acceptance, but cannot replace a public check with an easier definition. An
incorrect agent-authored checker can be revised through `revise_checks` with a
recorded reason; old receipts become stale and the incumbent is revalidated.
This prevents an erroneous self-check from permanently blocking completion.

API probes run the explicitly selected task interpreter and report installed
module location/version, actual signatures, explicit minimal invocation results,
and real exceptions. Command probes are diagnostic; exit zero alone does not
establish semantic correctness. Semantic command checks require calibrated
reports and a rejected negative control. Legacy trusted validation commands keep
their existing compatibility semantics.

## Runtime handoff

`AWORLD_TASK_WORKSPACE_ROOT` places durable state outside the ordinary workspace.
Runtime assigns a separate per-run directory under `/logs/agent`. After control
process reap and broker seal, the isolated installed interpreter runs publication
recovery before freeze and verifier handoff. Recovery errors prevent handoff.
Delivery, candidate and provenance summaries are captured with sealed evidence;
they are diagnostics, not benchmark reward or authentication against task root.

The framework does not infer arbitrary task semantics. Unrecognized public
constraints need explicit caller checks or agent-proposed independent checks.
Passing these mechanisms does not establish an 80% benchmark reward; deployment
qualification and the unchanged external verifier remain separate measurements.
