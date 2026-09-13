# Project Standards

These standards apply to all three repository branches and their isolated worktrees.

## Correctness and Reproducibility

- Pin the ParseBench data revision, scorer revision, AWorld wheel digest, runtime image identity, and all parser/model profiles.
- Treat the official scorer as the source of truth; do not reimplement metrics when an adapter can call the pinned implementation.
- Never average task rewards as a substitute for ParseBench's dimension-first, equal-weight overall aggregation.
- Preserve zero scores, execution failures, and not-scored outcomes as distinct states.
- Fail closed on unknown providers, missing layout output, incomplete dimensions, or silent model fallback.

## Security and Data Boundaries

- Never copy keys, tokens, signed URLs, or raw credentials from local YAML into source, manifests, test fixtures, logs, or artifacts.
- Use allowlisted logical secret/model references at API boundaries.
- Keep private ground truth inside verifier-only task package paths.
- Validate archive paths, sizes, checksums, duplicate IDs, and resource locations before extraction or execution.
- Do not commit downloaded ParseBench documents, model weights, full generated datasets, run logs, or reports.

## Code Quality

- Follow each repository's nearest `AGENTS.md` and existing language/style conventions.
- Keep framework logic benchmark-neutral where possible; ParseBench-specific semantics belong in AWorld's benchmark package and verifier.
- Functions and models must have single, explicit responsibilities and typed boundary contracts.
- Handle retryable infrastructure failures separately from deterministic parser/scorer failures.
- Avoid new dependencies unless the official scorer/runtime contract requires them and the lock/build manifests are updated.

## Testing

- Write behavior tests before implementation for converters, normalizers, reducers, CLI contracts, gateway boundaries, and harness behavior.
- Use small synthetic fixtures for fast tests and a pinned five-dimension smoke set for integration tests.
- Assert exact schemas, IDs, checksums, metric values, and failure states rather than truthiness.
- Run the closest practical end-to-end user path after unit and integration suites.
- Distinguish pre-existing failures from introduced regressions and record evidence in `.agent/progress.md`.

## Git and Review

- Each repository uses its own `codex/filex-parsebench-benchmark` branch and isolated worktree.
- Preserve dirty user worktrees and never stage unrelated or generated local files.
- Commits are small, reviewable, and separated by concern.
- Re-read `.agent/progress.md` before milestone starts, dispatches, merges, and review cycles.
- Run an architectural review after every milestone and a cross-repository review before completion.

## Release and Publication Gates

- Bundle AWorld, aworld-cli, and FileX wheels as one reviewed set and record every SHA-256 plus the originating AWorld source commit.
- Resolve FileX native/model dependencies under Python 3.12 from one shared Runtime constraints file, then run import and no-download model-initialization health checks during image construction.
- Keep the large PaddleX archive out of Git. A normal Runtime release may inject only the exact ignored archive selected by the reviewed manifest; Arca CI must receive an internal, non-secret HTTPS artifact URL and verify the same size, archive digest, member set, and member digests.
- Refuse formal leaderboard publication unless the result package is complete and carries the approved immutable Runtime identity and signed gateway acceptance evidence. Smoke and diagnostic reports must remain explicitly non-publishable.
- Treat the deployed image-transfer `reward_server` as an external trusted boundary: it must consume content-addressed source material, verify and echo the exact source-material SHA-256, and accept only the dedicated attempt-bound service capability. Missing support fails closed.
- Before publication, version the signed gateway acceptance receipt so it binds every selected task's actual immutable image digest and runtime type; a plan checksum alone is an idempotency association, not sufficient provenance evidence.
- A missing Docker daemon, internal artifact, protected model binding, or signed deployment identity is an external readiness gate, not permission to weaken provenance or silently fall back.
