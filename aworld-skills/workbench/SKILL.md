---
name: workbench
description: Explicitly manage, validate, and publish workspace artifact candidates with the standalone Workbench CLI. Use only when Runtime selected the workbench capability for this run.
metadata:
  default_enabled: false
execution_assets:
  - scripts/workbench.py
  - scripts/workbench_runtime/__init__.py
  - scripts/workbench_runtime/_process.py
  - scripts/workbench_runtime/_process_child.py
  - scripts/workbench_runtime/session.py
  - scripts/workbench_runtime/store.py
  - scripts/workbench_runtime/store_inputs.py
  - scripts/workbench_runtime/store_io.py
  - scripts/workbench_runtime/validation.py
---

# Use Workbench

Workbench is an optional candidate-management helper. It is disabled unless the
Runtime explicitly selects the `workbench` capability. Its checks and receipts
are agent self-check evidence only: they are not the benchmark verifier, do not
compute reward, and must not be described as caller or canonical acceptance.

The adapter supplies `WORKBENCH_*` values as runtime-provided configuration.
They are ordinary environment variables that the agent shell can override, not
a security boundary. Changing them cannot grant access beyond the terminal's
existing operating-system permissions and cannot produce canonical verifier or
reward evidence.

Always call the mounted CLI at:

```bash
"$AWORLD_PYTHON_EXECUTABLE" /skills/workbench/scripts/workbench.py --help
```

Runtime provides the initial workspace, state directory, and run scope through
environment variables; the CLI exposes no command-line shortcuts for replacing
them. Workbench never infers outputs, checks, policy, or scope from task text,
dataset names, or benchmark identity.

## Initialize an explicit self-check contract

Create a JSON contract inside the current task workspace. Every contract must declare
`authority: "agent_self_check"`, at least one output, at least one executable
check, and an explicit selection policy. Use paths relative to
`$WORKBENCH_WORKSPACE_ROOT`; output and input paths must remain inside that
configured directory.

```json
{
  "schema_version": "workbench.contract/v1",
  "authority": "agent_self_check",
  "outputs": [
    {
      "id": "report",
      "path": "report.json",
      "checks": [
        {"id": "report-json", "kind": "json", "path": "report.json", "root_type": "object"}
      ]
    }
  ],
  "inputs": [],
  "checks": [],
  "policy": {
    "mandatory_checks": ["report-json"],
    "hard_constraints": []
  }
}
```

Initialize exactly once for the configured scope:

```bash
"$AWORLD_PYTHON_EXECUTABLE" /skills/workbench/scripts/workbench.py init \
  --contract workbench-contract.json
```

Re-initializing with a different contract fails closed. To compare candidates
by a measured metric, declare a policy objective such as
`{"metric":"check-id.metric-name","direction":"maximize"}`. Do not invent
metrics; objective values come only from executed checks.

## Save, validate, and publish a candidate

Create a bounded candidate manifest inside the current task workspace. Keys are
the exact declared output paths and values are the current draft files to
snapshot:

```json
{
  "files": {
    "report.json": "drafts/report.json"
  },
  "note": "candidate after schema repair"
}
```

Then run:

```bash
"$AWORLD_PYTHON_EXECUTABLE" /skills/workbench/scripts/workbench.py save-candidate \
  --manifest candidate.json

"$AWORLD_PYTHON_EXECUTABLE" /skills/workbench/scripts/workbench.py validate-candidate \
  --candidate-id CANDIDATE_ID

"$AWORLD_PYTHON_EXECUTABLE" /skills/workbench/scripts/workbench.py promote-candidate \
  --candidate-id CANDIDATE_ID \
  --receipt-id RECEIPT_ID
```

When the explicit policy has no objective, an already published eligible
candidate remains selected by default. To deliberately replace it with a later
validated candidate, make that decision explicit and give a reason:

```bash
"$AWORLD_PYTHON_EXECUTABLE" /skills/workbench/scripts/workbench.py promote-candidate \
  --candidate-id CANDIDATE_ID \
  --receipt-id RECEIPT_ID \
  --supersede \
  --reason "the later candidate fixes the requested output"
```

`--supersede` cannot bypass checks, input protection, receipt binding, or hard
constraints. It only resolves selection when no objective exists.

## Inspect bounded state

`inspect` is compact by default. Request one section when more detail is useful:

```bash
"$AWORLD_PYTHON_EXECUTABLE" /skills/workbench/scripts/workbench.py inspect
"$AWORLD_PYTHON_EXECUTABLE" /skills/workbench/scripts/workbench.py inspect --section contract
"$AWORLD_PYTHON_EXECUTABLE" /skills/workbench/scripts/workbench.py inspect --section candidates
"$AWORLD_PYTHON_EXECUTABLE" /skills/workbench/scripts/workbench.py inspect --section readback
"$AWORLD_PYTHON_EXECUTABLE" /skills/workbench/scripts/workbench.py readback
```

Use returned candidate and receipt IDs exactly. A successful Workbench check is
still only local self-check evidence; the external benchmark verifier remains
authoritative for reward.
