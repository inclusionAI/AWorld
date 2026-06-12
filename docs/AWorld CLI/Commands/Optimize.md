# Optimize

## What It Does

`aworld-cli optimize` is the single phase-1 CLI entrypoint for manual self-evolve runs. It is a thin command surface: parsing happens in the CLI, while scheduling, target inference, evaluation, optimizer selection, durable artifacts, and agent opt-in semantics remain in `aworld.self_evolve`.

Use it to submit an explicit harness optimization request from a dataset, trajectory, session, or batch config.

## Commands

```bash
aworld-cli optimize --target skill:demo --dataset eval.jsonl
aworld-cli optimize --target prompt:system --dataset eval.jsonl
aworld-cli optimize --target tool:browser --dataset eval.jsonl
```

Trajectory and task-directed usage:

```bash
aworld-cli optimize \
  --task "fix browser login" \
  --from-trajectory trajectory.log

aworld-cli optimize \
  --target skill:login \
  --from-session session-123

aworld-cli optimize \
  --target tool:browser \
  --batch-config batch.yaml \
  --iterations 3 \
  --apply auto_verified
```

Supported options:

- `--agent`: agent name or id for request context.
- `--task`: task text used by framework target inference when `--target` is omitted.
- `--target`: explicit generic target reference such as `skill:demo`, `prompt:system`, or `tool:browser`.
- `--dataset`: JSONL eval dataset path.
- `--from-session`: session id/source for framework dataset construction.
- `--from-trajectory`: trajectory log path.
- `--batch-config`: batch config path.
- `--iterations`: requested optimization iteration count.
- `--apply`: `proposal` or `auto_verified`.

`--apply write` and `--apply branch` are intentionally unsupported in phase 1. Proposal artifacts are written by the framework store; direct file writes and branch management are outside the CLI contract.

## Output

When the framework accepts a request, the command prints the report path and the best candidate id when one is available:

```text
Optimize run submitted.
Report: .aworld/self_evolve/<run_id>/report.json
Best candidate: cand-1
```

## Boundaries

The command is not an interactive slash command and there are no target-specific subcommands. All target forms use the same generic command path.

The CLI does not infer targets itself. `--task` without `--target` passes `infer_target=True` into the framework so credit assignment and target selection remain framework-owned.

The CLI also does not own self-evolve scheduler behavior, evaluator behavior, optimizer behavior, durable job formats, or agent opt-in configuration. Configure opt-in with `AgentConfig.self_evolve_config`, then use this command as a manual/debug entrypoint for the same framework APIs.

See [Self Evolve](../../Agents/Self%20Evolve.md) for the framework safety model and configuration details.
