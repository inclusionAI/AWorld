# Project Progress

## Final Status

**Phase:** minimal-boundary implementation complete; real-VLM and Docker local acceptance complete.

The final data flow is:

`ParseBench checkout -> independent Dataset generator -> standard lingguang Dataset project -> unmodified lingguang-bench-client package -> unmodified mcpgateway upload/publish/dispatch -> Arca Runtime AWorld harness -> generic aworld-cli + FileX skill -> Dataset verifier -> Gateway result`.

## Repository State

| Repository | Branch | State |
| --- | --- | --- |
| AWorld | `codex/filex-parsebench-benchmark` | Generic AWorld/FileX changes plus independent `benchmark-datasets/parsebench` tooling |
| lingguang-bench-runtime | `codex/filex-parsebench-benchmark` | Generic AWorld/FileX Runtime harness and immutable dependencies |
| lingguang-bench-client | `codex/filex-parsebench-benchmark` | Net source diff is exactly zero against latest-master base `c3cdc378` |
| mcpgateway | `codex/filex-parsebench-benchmark` | Net source diff is exactly zero against integration base `b9f54ffe` |

The client and Gateway branches contain explicit revert history for auditability, but their final trees are byte-for-byte identical to their bases.

## Delivered Capabilities

### Independent ParseBench Dataset project

- `benchmark-datasets/parsebench` owns the pinned ParseBench schema, source validation, deterministic execution grouping, task instructions, private ground truth, verifier, scorer bridge, and project generator.
- The generator produces ordinary `bench.toml`, `dataset/dataset.yaml`, `dataset/samples.jsonl`, and `tasks/<task-id>` inputs accepted by stock lingguang-bench-client.
- `package_with_lingguang.py` imports only the stock client's `BenchConfig.load` and `build_gateway_package` to emit the manual-upload ZIP. It does not implement or patch the client package protocol.
- Smoke selection is explicit; omitting it creates the full pinned Dataset.

### AWorld/FileX

- AWorld CLI remains benchmark-neutral and supports generic skill loading plus optional ATIF trajectory output.
- FileX remains an independent CLI and reusable skill. Its generic `--artifacts-dir` mode atomically writes Markdown, raw Document IR, and hash-bound result evidence.
- No ParseBench lifecycle, upload, scheduling, or scorer command exists in AWorld CLI.

### Runtime/Arca

- Runtime owns the execution adapter: generic `aworld-cli run --skill filex`, skill path, model environment, workspace/artifact roots, and trajectory output.
- The immutable image supplies AWorld/FileX and the pinned scorer executable required by Dataset-owned verifier code; the harness contains no ParseBench task detection or scoring branch.

### Unmodified platform systems

- Stock lingguang-bench-client validates the generated project and builds its executable ZIP.
- Stock mcpgateway validates/uploads/publishes the ZIP, dispatches Arca work, and stores artifacts/rewards.

## Verification Evidence

- Independent generator test: `1 passed`; Ruff passed for the complete Dataset tooling directory.
- Generated five-task real-data smoke project validated successfully using unmodified lingguang-bench-client.
- The same stock client produced a 3,045,005-byte `yolo-dataset-package/v2` ZIP with SHA-256 `sha256:2350f5f6747c258380832fa45d3875989e9de0a6817686102c272eb094e1b8c1`.
- Unmodified mcpgateway production parser accepted all five tasks.
- Real Gemini/PaddleOCR-VL calls plus the pinned scorer covered all five dimensions. The diagnostic equal-weight smoke score is `0.3010371995915193`, with positive text-content (`0.86351933129093`), text-formatting (`0.14166666666666666`), and layout (`0.5`) scores and no missing/unscored/execution-failed/official-failure result.
- A real `aworld-cli run --skill filex` text task made 15 VLM calls with zero retries/timeouts and received positive official scores, proving the CLI-to-skill-to-model-to-scorer path.
- DockerSandbox: three real Docker integration tests passed; a copied task source hash matched inside the container and an artifact was recovered.
- AWorld focused tests: `31 passed`; Runtime focused tests: `54 passed`; Docker integration: `3 passed`.
- Sanitized evidence: `benchmark-datasets/parsebench/evidence/local-vlm-smoke-20260913/acceptance.json`.
- Client and Gateway net diffs were verified with `git diff --quiet <base>..HEAD` returning zero.

## External Gates

- The smoke package is non-publishable by design because it is an incomplete selection and uses a mutable local Runtime image tag.
- No live Gateway upload or Arca run was attempted; the user explicitly separated those concerns from local VLM and Docker validation.
- A full ParseBench package requires a local checkout of the pinned Hugging Face revision and the real immutable Runtime image digest. A complete 2,078-task run was deliberately not performed.

## Review Conclusion

The two platform systems identified by the user are now reused without source modifications. ParseBench-specific behavior is isolated in the Dataset project; Runtime owns only execution adaptation; AWorld/FileX remains generic.
