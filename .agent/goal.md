# Project Goal

## Problem Statement

FileX can parse documents and has a historical ParseBench score report, but the repository does not contain a reproducible path that prepares the pinned ParseBench dataset, invokes FileX through AWorld CLI, executes the workload through mcpgateway and Arca, and reduces detailed metrics into the official leaderboard score.

## Desired Outcome

Deliver a version-pinned, deterministic ParseBench integration spanning AWorld/FileX, mcpgateway, and lingguang-bench-runtime.
The user can prepare smoke or full executable datasets, run them locally or through mcpgateway plus Arca, resume interrupted campaigns, and generate official per-dimension and overall reports.

## Acceptance Criteria

- [x] A pinned converter turns ParseBench data revision `2805a1d940f95a203e0ae4b88be9934f7765b3fc` into a valid `yolo-dataset-package/v2` package.
- [x] The converter aggregates 169,011 rules into 2,078 unique parse executions while preserving rule IDs, dimensions, source provenance, private ground truth, and checksums.
- [x] A small deterministic fixture and a five-dimension smoke package can be generated without exposing ground truth to the runtime under test.
- [x] `aworld-cli` exposes a dedicated ParseBench workflow with local and mcpgateway backends rather than reusing the LLM-judge task evaluator path.
- [x] FileX emits the Markdown and layout sidecar required by the pinned official scorer, with explicit provider/model identity and no silent fallback.
- [x] mcpgateway can validate, publish, plan, dispatch, collect, and expose detailed ParseBench artifacts through Harbor with Arca `agent_only` execution.
- [x] lingguang-bench-runtime bundles the matching AWorld/FileX build and maps protected LLM/VLM profile references without embedding credentials.
- [x] Final aggregation matches the pinned official ParseBench per-dimension and equal-weight overall logic.
- [x] Unit, integration, package-validation, mocked gateway, and a deliberately small representative local task subset pass; unavailable live-model and live-Arca gates are documented with their exact blockers.
- [x] Every modified repository uses an independent `codex/filex-parsebench-benchmark` branch and isolated worktree.

## Non-Goals

- Improving FileX parsing quality or attempting to close the existing visual-grounding score gap beyond the adapter work needed for correct scoring.
- Replacing the official ParseBench scorer with an LLM judge.
- Publishing results to the public ParseBench leaderboard.
- Deploying production mcpgateway or Arca services.
- Running the complete 2,078-page ParseBench campaign as part of local acceptance.
- Committing downloaded benchmark documents, model weights, credentials, local logs, or generated full-run artifacts.

## Constraints

- Preserve the user's current AWorld, mcpgateway, and Runtime checkouts; implementation must use isolated worktrees from explicit commits.
- mcpgateway changes are limited to `mcp_gateway/src/yolo_scheduler/` and its tests/docs.
- ParseBench scorer revision is pinned to the FileX-validated `34b73455032797754f6ed62e14c27a8b5423d11e` unless a compatibility issue forces a documented change.
- The primary FileX leaderboard path is deterministic program execution; agent/skill behavior may be tested separately but cannot affect the parser score.
- Use the protected logical VLM profile `default__gemini-3.1-pro-preview`, resolving to concrete model `gemini-3.1-pro-preview`; secrets must be injected by protected runtime bindings.

## Tech Stack

- Python 3.12 for FileX and ParseBench tooling.
- AWorld and aworld-cli evaluation/command infrastructure.
- mcpgateway `yolo_scheduler`, Harbor, executable dataset package v2, and Arca.
- lingguang-bench-runtime AWorld Harbor harness.
- pytest, ruff, pyright, and pinned official ParseBench tests/scorer.
