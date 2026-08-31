# Local AWorld Docker Sandbox / Terminal Bench Evidence

This directory contains a two-task local validation of AWorld's attach-only
`DockerSandbox` implementation. The Terminal Bench dataset was read from:

`/Users/wuman/Documents/workspace/mcpgateway/mcp_gateway/src/yolo_scheduler/datasets/terminal-bench-2-1-executable-v2.zip`

No code was changed in mcpgateway or lingguang-bench-runtime-dsh.

## Results

| Task | TaskResponse | Verifier reward | Trajectory |
|---|---:|---:|---:|
| `prove-plus-comm` | success | 1 | 9 items |
| `cancel-async-tasks` | success | 0 | 13 items |

The hard negative is intentional evidence: AWorld's agent completed and reported
success, while the benchmark verifier rejected the queued-task cancellation edge
case. This keeps framework completion, trajectory capture, and external reward as
three independently observable states.

Each `runs/<task>/` directory contains:

- `raw_trajectory.json`: canonical trajectory bound to `TaskResponse`;
- `task_response.json`: complete serialized response;
- `logs/trajectory.log`: current two-line legacy logger representation;
- `verifier.json`: assertion-level reward evidence.

The source dataset, extracted task fixtures, verbose runtime logs, and initial
connectivity smoke output remain local and are intentionally excluded from this
evidence bundle. Reproduction uses the dataset path recorded above and the two
task names in the results table.
