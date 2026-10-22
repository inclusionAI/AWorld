# Changelog

## Unreleased

### Complete public delivery contracts

- Recognize explicit destination, typed-output, and bounded Markdown output
  list forms without promoting inputs, examples, negated paths, or component
  side effects into delivery obligations.
- Keep multi-file delivery debt active until every named output has an
  inspectable current candidate, while preserving single-file, answer-only,
  and in-place task behavior.
- Defer the bounded reviewer lease for partial multi-file candidates so review
  cost is spent only when the public contract can be closed.

### Reviewer value and delivery convergence

- Made one authenticated GUIDE reviewer lease a bounded control turn, while
  preserving manual OFF/OBSERVE compatibility and the short-task fast path.
- Kept an admitted reviewer lease claimable across the model call that emits
  it, recovered its schema from the Agent's authenticated Tool surface when a
  sticky catalog omitted the dormant Tool, and constrained reviewer-bound
  validation to one non-parallel carrier Tool call.
- Prioritized semantic answer and completeness obligations over residual shape
  checks, including obligations already covered by solver-authored self-checks.
- Added kind-aware, candidate-wide counterexample guidance for finite sets,
  behavioral boundaries, objectives, and independently recomputed metrics.
- Added advisory 16/40-observation candidate checkpoints for long tasks with a
  named public output, without revoking ordinary Tool access or converting
  public-contract guidance into completion authority.
- Expanded public objective extraction while excluding input-only format
  descriptions; reviewer-only additions do not increase completion debt.

### FileX runtime synchronization

- Synchronized the latest FileX document and media runtime into AWorld while
  preserving the existing YouTube transcript provider and all-in-one
  `aworld-cli` image behavior.
- Added the asynchronous HTTP service, direct URL/object-reference ingestion,
  bounded queuing, persistent PaddleOCR-VL worker lifecycle, resumable PDF
  batches, Document IR v2 formatting repair, and video evidence artifacts.
- Added public deployment, hardware, configuration, operations, and security
  documentation plus a pinned ParseBench evaluation report.
- Removed environment-specific deployment history, internal hosts, repository
  locations, and local filesystem details from the imported source and docs.
- Validation covers the FileX unit/integration test suite, AWorld skill wrapper
  tests, secret/local-information scans, and container build-contract checks.

Compatibility: the default `aworld-tools/filex/Dockerfile` remains the AWorld
all-in-one image. GPU service deployments use `Dockerfile.gpu-service`.
