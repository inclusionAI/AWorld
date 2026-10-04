---
name: workbench
description: Optionally manage, validate, compare, and publish workspace artifact candidates with the standalone Workbench CLI when checkpoints or multiple candidates would materially improve the task.
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

Workbench is an AWorld-owned candidate-management capability that is bundled but
disabled by default. Use it only when it was explicitly selected and multiple
candidates, rollback, checkpoint preservation, or measured local comparison
provide real value. Direct and low-risk tasks should usually proceed without
Workbench ceremony.

Its checks and receipts are agent self-check evidence only. They do not inspect
hidden checks, compute task reward, or represent caller/canonical acceptance.
Final external evaluation is outside Workbench.

`WORKBENCH_*` values are optional ordinary environment variables, not a
security boundary. Changing them cannot grant access beyond the terminal's
existing operating-system permissions and cannot produce canonical verifier or
reward evidence. When they are absent, the CLI uses the real current directory
as the workspace, puts state in AWorld's external control/state directory, and
derives a bounded scope from AWorld task/session identity or a workspace
fingerprint. Explicit values remain supported.

Choose the configured AWorld interpreter when present; otherwise `python3`
launches the CLI and the CLI uses `sys.executable`:

```bash
WORKBENCH_PYTHON="${AWORLD_PYTHON_EXECUTABLE:-python3}"
"$WORKBENCH_PYTHON" /skills/workbench/scripts/workbench.py --help
```

The CLI exposes no command-line shortcuts for replacing workspace, state, or
scope. Workbench never infers outputs, checks, policy, or scope from task text,
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
"$WORKBENCH_PYTHON" /skills/workbench/scripts/workbench.py init \
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
"$WORKBENCH_PYTHON" /skills/workbench/scripts/workbench.py save-candidate \
  --manifest candidate.json

"$WORKBENCH_PYTHON" /skills/workbench/scripts/workbench.py validate-candidate \
  --candidate-id CANDIDATE_ID

"$WORKBENCH_PYTHON" /skills/workbench/scripts/workbench.py promote-candidate \
  --candidate-id CANDIDATE_ID \
  --receipt-id RECEIPT_ID
```

When the explicit policy has no objective, an already published eligible
candidate remains selected by default. To deliberately replace it with a later
validated candidate, make that decision explicit and give a reason:

```bash
"$WORKBENCH_PYTHON" /skills/workbench/scripts/workbench.py promote-candidate \
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
"$WORKBENCH_PYTHON" /skills/workbench/scripts/workbench.py inspect
"$WORKBENCH_PYTHON" /skills/workbench/scripts/workbench.py inspect --section contract
"$WORKBENCH_PYTHON" /skills/workbench/scripts/workbench.py inspect --section candidates
"$WORKBENCH_PYTHON" /skills/workbench/scripts/workbench.py inspect --section readback
"$WORKBENCH_PYTHON" /skills/workbench/scripts/workbench.py readback
```

Use returned candidate and receipt IDs exactly. A successful Workbench check is
still only local self-check evidence; it makes no statement about hidden reward
or canonical acceptance.
