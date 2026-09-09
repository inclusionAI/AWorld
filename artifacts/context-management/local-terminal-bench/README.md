# Local AWorld Docker Sandbox / Terminal Bench Evidence

This directory contains a historical two-task smoke validation of AWorld's
attach-only `DockerSandbox` implementation. The dataset path is intentionally
not recorded because it is machine-specific; reproduction passes any compatible
Terminal Bench package to the generic driver with `--dataset`.

No code was changed in mcpgateway or lingguang-bench-runtime-dsh.

## Results

| Task | TaskResponse | Verifier reward | Trajectory |
|---|---:|---:|---:|
| `prove-plus-comm` | success | 1 | 9 items |
| `cancel-async-tasks` | success | 0 | 13 items |

The reward-0 smoke keeps framework completion, trajectory capture, and external
reward as three independently observable states. The reward-1 smoke is a single
model/run connectivity case, not a paired “both-one” positive.

Each `runs/<task>/` directory contains:

- `raw_trajectory.json`: canonical trajectory bound to `TaskResponse`;
- `task_response.json`: complete serialized response;
- `logs/trajectory.log`: current two-line legacy logger representation;
- `verifier.json`: assertion-level reward evidence.

The source dataset, extracted task fixtures, verbose runtime logs, and initial
connectivity smoke output remain local and are intentionally excluded from this
evidence bundle. New causal runs use
`examples/sandbox/terminal_bench_context_eval.py`; task names, variants and
repetitions are CLI/manifest inputs, never hard-coded optimization cases.
