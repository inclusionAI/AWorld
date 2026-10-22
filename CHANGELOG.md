# Changelog

## Unreleased

### Reviewer value and delivery convergence

- Let a claimable reviewer lease preempt a simultaneous generic long-horizon
  decision boundary, so the bounded review happens while its candidate and
  public obligation are still current.
- Expanded the guided DSV4 review window from 60 to 90 seconds, retaining a
  single provider attempt and a 45-second stream-idle bound so admitted reviews
  can return value instead of timing out at the previous hard edge.
- Strengthened task-derived counterexample validation to compare every emitted
  field with its exact authoritative source value and exercise grouping-key
  collisions, cardinality preservation, and negative cases.
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
