# Self-Evolve Toy Example

This example shows the shape of a phase-1 self-evolve request without enabling unattended online application.

Use a toy eval dataset:

```bash
aworld-cli optimize \
  --target skill:login \
  --dataset examples/aworld_quick_start/self_evolve/toy_eval.jsonl \
  --apply proposal
```

Use a trajectory log when the framework should infer the target from task evidence:

```bash
aworld-cli optimize \
  --task "improve login retry guidance" \
  --from-trajectory ./trajectory.log \
  --apply proposal
```

Both commands submit the same generic framework request path. The CLI does not own target inference, optimizer selection, evaluation gates, durable artifacts, or agent opt-in.
