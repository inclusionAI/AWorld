# ParseBench Dataset Project Generator

This directory is independent from AWorld CLI, lingguang-bench-client, and
mcpgateway implementation code. It converts the pinned ParseBench checkout into
a normal executable Dataset project understood by an unmodified
lingguang-bench-client.

Generate a small local project:

```bash
PYTHONPATH=benchmark-datasets/parsebench/tools \
python -m parsebench_dataset.project \
  --source /path/to/ParseBench \
  --output /tmp/parsebench-smoke \
  --runtime-service YOUR_RUNTIME_SERVICE \
  --runtime-image registry.example/aworld-filex@sha256:YOUR_DIGEST \
  --smoke-per-dimension 1
```

Validate and package it with the stock client from its latest master branch:

```bash
cd /Users/wuman/Documents/workspace/lingguang-bench-client
.venv/bin/lingbench --config /tmp/parsebench-smoke/bench.toml validate
.venv/bin/python \
  /path/to/aworld/benchmark-datasets/parsebench/package_with_lingguang.py \
  --project /tmp/parsebench-smoke \
  --output /tmp/parsebench-smoke.zip
```

The helper only calls the stock client's `BenchConfig.load` and
`build_gateway_package`; it contains no package implementation of its own. The
resulting ZIP can be uploaded manually through mcpgateway. Alternatively run
the stock client's `publish` command to package and upload in one step. Do not
include `.env` or Gateway tokens in the Dataset directory. Omit
`--smoke-per-dimension` only when intentionally building the complete pinned
Dataset.

Ownership remains strict:

- this Dataset project owns ParseBench conversion, private ground truth,
  verifier, and scorer invocation;
- Runtime owns the generic AWorld/FileX execution adapter and immutable binary
  dependencies;
- AWorld/FileX owns only generic CLI and skill behavior;
- stock mcpgateway owns upload, publication, scheduling, and result storage.

The Runtime injects the credential-free FileX model identity used by this
project: `ai_cloud_Kimi_k26_pgc` maps to HTTP model `kimi_k26_pc`, while the
PaddleOCR-VL provider uses `aisearch_paaldocr_vl_16`. Credentials must remain
Runtime Secrets (`GATEWAY_VLLM_API_KEY` and
`FILEX_PADDLE_OCR_VL_REC_API_KEY`); never place them in the generated Dataset
project or ZIP.

For a complete local package before a Runtime image has been released, omit
`--smoke-per-dimension` and use `--runtime-image aworld-filex-parsebench:local
--allow-mutable-local-image`. The result includes all 2,078 tasks and 169,011
rules, but explicitly retains `publishable=false`. The local image reference is
a build target, not evidence of an available or approved Runtime image.

Full releases require a verified Runtime digest and an updated
`PINNED_PARSEBENCH_RUNTIME_IMAGE` release contract. A digest-shaped string alone
does not establish approval. Changes to embedded verifier modules also require
recomputing `PINNED_FULL_SELECTION_MANIFEST_SHA256` against the full source.
Run the full source and Runtime bundle checks before packaging:

```bash
PARSEBENCH_SOURCE=/path/to/ParseBench-snapshot \
PARSEBENCH_RUNTIME_SOURCE=/path/to/lingguang-bench-runtime \
PYTHONPATH=benchmark-datasets/parsebench/tools \
python -m pytest benchmark-datasets/parsebench/tests
```
