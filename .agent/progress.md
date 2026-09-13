# Project Progress

## Current Status

**Phase:** Complete; ready for handoff.
**Outcome:** The ParseBench dataset, FileX execution, aworld-cli lifecycle, generic mcpgateway orchestration, and Runtime/Arca packaging capabilities are implemented on three independent branches. Local acceptance intentionally used fixtures and a few representative tasks rather than the full 2,078-task campaign.

## Frozen Implementation Revisions

| Repository | Branch | Implementation commit | Worktree |
| --- | --- | --- | --- |
| AWorld | `codex/filex-parsebench-benchmark` | `3b1bfec7937a0fe2f0da2fc817b15e118b43a5c7` | `/private/tmp/aworld-filex-parsebench` |
| mcpgateway | `codex/filex-parsebench-benchmark` | `4c8cfcaf32a8994c0a714150b1aef30be69b498a` | `/private/tmp/mcpgateway-filex-parsebench` |
| lingguang-bench-runtime | `codex/filex-parsebench-benchmark` | `b4ec530d4399e92d618cfa8350ea71a45d22c8c9` | `/private/tmp/runtime-filex-parsebench` |

The final AWorld documentation commit is intentionally allowed to follow the implementation commit. Runtime wheel provenance remains bound to the implementation commit above.

## Delivered Capabilities

### AWorld, FileX, and aworld-cli

- Pinned ParseBench dataset revision `2805a1d940f95a203e0ae4b88be9934f7765b3fc` and scorer revision `34b73455032797754f6ed62e14c27a8b5423d11e`.
- Validated the eight-field upstream schema and content-bound revision evidence.
- Aggregated 169,011 rules into 2,078 deterministic parse executions while preserving private rules, dimensions, source identity, checksums, and task provenance.
- Added fixture, smoke, explicit task/dimension selection, and full package modes; smoke remains the default.
- Added `aworld-cli benchmark parsebench` lifecycle commands: `prepare`, `submit`, `status`, `report`, `run-task`, and `verify-task`.
- Added durable idempotency/resume state, exact dataset-acceptance binding, paginated gateway collection, and fail-closed publication provenance.
- Added FileX result schema v2 with provider/version/model identity, immutable input/output digests, Markdown, Document IR/layout sidecar, and official verifier artifacts.
- Enabled chart recognition for ParseBench and mapped the complete PP-DocLayoutV3 output-label set, including chart, formula, image, header/footer, footnote, and vertical-text variants.
- Selected protected logical VLM profile `default__gemini-3.1-pro-preview`, resolving to `gemini-3.1-pro-preview`; model credentials remain environment-only.

### mcpgateway

- Added durable executable-dataset import and batch lifecycle APIs with request idempotency, pagination, terminal filtering, and restart-safe persistence.
- Bound an immutable accepted dataset checksum to every plan and result, authenticated completion/acceptance, and retained detailed reward vectors and artifact metadata.
- Added protected model-profile validation and generic Harbor/Arca `agent_only` planning without embedding ParseBench scoring semantics into gateway code.
- Preserved zero, failed, and not-scored states as distinct outcomes for the AWorld official reducer.

### lingguang-bench-runtime and Arca

- Bundled AWorld, aworld-cli, and FileX wheels built from AWorld implementation commit `3b1bfec7937a0fe2f0da2fc817b15e118b43a5c7`.
- Recorded wheel SHA-256 values and a Python 3.12 resolver constraint set for MCP, FastMCP, PaddleOCR/PaddleX/PaddlePaddle, OpenCV, NumPy, Pillow, and aiohttp.
  The final wheel hashes are `f88a1c3823d59ec439d1c82ca75f28ffe32e2303b7431e1e569d370e0319d2db` (AWorld), `e2696363f7540560be1f29f21e85f9102dc0b7680ce6c3a3871caf8998013689` (aworld-cli), and `0068691d0d840653bb5538ad45722e5868a3bdeeb2326e577a16a53659e53020` (FileX).
- Installed both `aworld-cli` and `filex` wrappers in normal and Arca images and extended the Harbor AWorld adapter to verify both surfaces.
- Added required native OpenCV/Paddle libraries and a build-time import plus no-download PaddleOCR-VL initialization health check with chart recognition enabled.
- Added a reviewed PP-DocLayoutV3 model manifest with exact archive/member sizes and hashes. The 126 MiB model archive remains ignored by Git.
- Normal releases materialize and inject only that exact verified archive into the immutable source ZIP. Clean ACI builds require an internal non-secret HTTPS artifact URL and fail closed if it is absent or does not match the manifest.

## Verification Evidence

### AWorld and FileX

- `215 passed` in the focused ParseBench adapter, converter, scorer, lifecycle, gateway-client, provenance, and publication suite, plus `70 passed` in the CLI bootstrap, slash-command, and input-hook suite.
- `13 passed` in the focused PaddleOCR provider suite, including chart-recognition Markdown/IR preservation.
- FileX document parsing suite covered all `182` tests: `180 passed` with an isolated workspace/cache and the two environment-sensitive path tests passed separately without the workspace override.
- Ruff checks passed for the changed ParseBench/FileX/CLI files.
- A final three-wheel clean Python 3.12 resolver installed 240 packages and `uv pip check` reported all packages compatible.

### mcpgateway

- `385 passed` in the final changed executable-dataset, internal image
  capability, Harbor/Arca, AWorld harness, publication-CAS, and worker-boundary
  suite.
- An additional focused security/configuration selection passed `123` tests;
  the earlier extended integration and Dataset API selections passed `32`
  (`10 skipped`) and `28` tests respectively.
- Critical Ruff rules (`E9`, `F63`, `F7`, `F82`), curated full Ruff checks,
  Python compilation, `git diff --check`, and strict OpenSpec validation passed.
  Repository-wide test collection still has six pre-existing missing/stale
  Harbor test-module imports, outside this feature diff.

### Runtime

- `651 passed, 3 skipped` in the complete Runtime suite under Python 3.12.9.
- Focused release, model-asset, wheel-bundle, Dockerfile, ACI, source-overlay, and runtime-health tests passed.
- Ruff checks passed for all introduced/changed Runtime implementation files (the vendored legacy runner retains pre-existing repository-wide lint findings outside the changed wrapper tuple).
- The final resolved environment imported native Paddle/OpenCV dependencies and initialized local PP-DocLayoutV3 plus PaddleOCR-VL with online model lookup forcibly rejected.

### Representative Task Smoke

- Real FileX/model text task `pb-98cc80e6f06af114a7ebce79e35e78df` completed end to end and the pinned official scorer produced text `0.8905934663942072`, formatting `0`, and reward `0.4452967331971036`.
- Chart task `pb-047a098c222f39cdcdf801e6db424f17` completed through the AWorld adapter and FileX subprocess using the fixed local PP-DocLayoutV3 model plus a deterministic localhost OpenAI-compatible VLM stub. Result schema v2 recorded PaddleOCR `3.7.0`, PP-DocLayoutV3, input/output digests, and chart-table Markdown. The pinned official scorer returned `scored`; its chart score was expectedly `0.0` because the local VLM response was a deterministic placeholder, so this is execution-contract evidence rather than a quality claim.

## External Readiness Gates

- Docker/Arca image execution could not be run because the host Docker daemon was unavailable (`/var/run/docker.sock`). Static Dockerfile/ACI contracts, full Runtime tests, clean wheel resolution, native imports, and offline model initialization passed.
- A second live external-VLM PDF attempt was not performed because sending the benchmark PDF to the configured external endpoint was not explicitly authorized for that destination. No workaround or data transfer was attempted.
- Production ACI invocation must provision `paddlexModelArchiveUrl` with the internally hosted bytes matching the pinned PP-DocLayoutV3 manifest. The empty default deliberately fails closed.
- Cross-deployment image transfer requires caller and receiver to share independently injected `DATASET_IMAGE_S2S_CAPABILITY_SECRET` and `DATASET_IMAGE_S2S_CAPABILITY_ISSUER` values. The secret must never reuse `JWT_SECRET`; a missing or mismatched trust binding fails closed.
- The deployed `reward_server` is outside these repositories. Before rollout it must accept only the attempt-bound internal capability, fetch the content-addressed `oss://` source, verify its exact SHA-256, and echo that digest in READY results; absent or mismatched source attestation is rejected.
- Formal leaderboard publication remains disabled until a complete campaign carries an approved immutable Runtime identity and a signed gateway receipt binding every selected task's actual image digest and runtime type. The current plan checksum is sufficient for durable association, not final publication provenance. Smoke reports remain explicitly non-publishable.

## Decisions and Scope

- ParseBench uses deterministic parser execution and the pinned official scorer, not an LLM judge.
- One parse execution represents one normalized document/page and carries all associated rules; detailed artifacts drive global reduction.
- Official aggregation computes each of the five dimensions first and then takes their equal-weight mean. Task reward averaging is forbidden.
- The local acceptance scope proves the integrated path with fixtures and representative tasks. It does not claim a publishable full benchmark score, deploy services, or alter parsing quality beyond adapter correctness.

## Final Review

Milestone reviews closed ground-truth isolation, source/scorer pinning,
official-failure zero padding, dataset acceptance binding, result authenticity,
runtime dependency drift, local model provenance, chart routing, internal Run
visibility, request-bound S2S capability, worker-side provenance, and
publication TOCTOU issues. The final frozen three-repository review found no
unresolved critical or important issue; only the explicitly external release
gates above remain.
