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

- **AWorld/FileX:** the independent `benchmark-datasets/parsebench` project owns pinned source validation, deterministic task grouping, package generation, result provenance, official verification, and dimension-first reduction. `document_parse_service` owns generic parsing and maps the complete PP-DocLayoutV3 label set into Markdown and Document IR without silent provider fallback.
- **CLI:** `aworld-cli run --skill filex` is the only integration surface. AWorld CLI contains no ParseBench lifecycle, upload, or scorer implementation.
- **mcpgateway/client:** existing executable-Dataset validation, ZIP upload, publication, dispatch, and result capabilities are reused without source changes. ParseBench-specific semantics remain in the Dataset package and verifier.
- **Runtime:** the Runtime locks the reviewed AWorld/FileX wheels, exposes both `aworld-cli` and `filex`, installs the pinned scorer plus layout dependency, injects the protected VLM environment into the stdio tool boundary, and performs a fail-closed no-download PaddleOCR-VL initialization health check.
- **Model binding:** source and task metadata use the protected logical VLM profile `default__gemini-3.1-pro-preview` (concrete model `gemini-3.1-pro-preview`). Credentials and endpoint authorization stay in protected runtime configuration; none are copied into source or task artifacts.
- **Publication:** local smoke can be scored diagnostically, but formal publication requires the official-full package and an approved immutable Runtime. Missing evidence produces an explicit non-publishable state.
