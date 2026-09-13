# Subagent Workflow

## Before Starting

1. Read `.agent/goal.md`, `.agent/plans.md`, `.agent/standards.md`, and `.agent/progress.md` from the AWorld orchestration worktree.
2. Read the nearest `AGENTS.md` in the repository being changed.
3. Confirm the assigned repository, worktree, task scope, and expected tests.
4. Inspect existing code and tests before designing changes.

## Implementation Workflow

1. State the boundary contract and edge cases.
2. Add a focused failing test.
3. Implement the smallest coherent behavior.
4. Refactor while tests remain green.
5. Run scoped unit tests, then relevant lint/type checks, then the closest integration path.
6. Review the diff for leaked secrets, downloaded data, generated artifacts, unrelated changes, and benchmark-specific logic in generic layers.
7. Commit one logical concern with a message that explains why.

## Cross-Repository Rules

- Never modify the user's original dirty checkout.
- Never modify `mcpgateway/mcp_gateway/src/mcp_gateway`.
- Coordinate shared contract changes through explicit versioned fields and fixtures; do not rely on synchronized private implementation details.
- If a task reveals a required change in another repository, report the contract and stop at the repository boundary unless that change is part of the assignment.
- Do not add raw model endpoints or credentials; use logical profile references.

## Report Format

- Summary of implemented behavior.
- Exact files changed.
- Tests and quality gates run, including counts/results.
- Assumptions and contract decisions.
- Remaining risks or external blockers.
- Commit SHA.

## Final Implementation Map

- **AWorld/FileX:** `aworld.benchmarks.parsebench` owns pinned source validation, deterministic task grouping, package generation, local/gateway lifecycle handling, result provenance, official verification, and dimension-first reduction. `document_parse_service` owns parsing and maps the complete PP-DocLayoutV3 label set, including chart/formula/footnote variants, into Markdown and Document IR without silent provider fallback.
- **CLI:** `aworld-cli benchmark parsebench` provides `prepare`, `submit`, `status`, `report`, `run-task`, and `verify-task`. Smoke scope is the default; full scope is explicit. Campaign state is durable and idempotent, and acceptance checks bind the exact submitted dataset checksum.
- **mcpgateway:** generic executable-dataset APIs persist imports/batches, validate protected logical model profiles, create immutable Harbor/Arca `agent_only` plans, expose paginated terminal status/results, retain reward vectors and artifacts, and authenticate result acceptance. Dataset-image transfer uses a dedicated S2S trust domain, content-addressed source material, provider-scope partitioning, and worker-side privileged-task provenance checks. ParseBench scoring semantics remain outside the gateway.
- **Runtime/Arca:** the Runtime locks three AWorld wheels to source commit `3b1bfec7937a0fe2f0da2fc817b15e118b43a5c7`, installs them under Python 3.12 with one native dependency constraints file, exposes both `aworld-cli` and `filex`, installs a manifest-verified PP-DocLayoutV3 detector, and performs a fail-closed no-download PaddleOCR-VL initialization health check.
- **Model binding:** source and task metadata use the protected logical VLM profile `default__gemini-3.1-pro-preview` (concrete model `gemini-3.1-pro-preview`). Credentials and endpoint authorization stay in protected runtime configuration; none are copied into source or task artifacts.
- **Publication:** local smoke can be scored and reported, but formal publication requires a complete campaign, an approved immutable Runtime, deployed source-digest attestation, and a signed gateway receipt binding each task image digest/runtime type. Missing evidence produces an explicit non-publishable state.
