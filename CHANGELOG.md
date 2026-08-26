# Changelog

## Unreleased

### FileX runtime synchronization

- Synchronized the latest FileX document and media runtime into AWorld while
  preserving the existing YouTube transcript provider and all-in-one
  `aworld-cli` image behavior.
- Added the asynchronous HTTP service, direct URL/object-reference ingestion,
  bounded queuing, persistent PaddleOCR-VL worker lifecycle, resumable PDF
  batches, Document IR v3 grounding/formatting repair, and video evidence
  artifacts.
- Preserved Paddle's detector geometry and confidence before VLM block merging,
  normalized contextual page labels, and removed HTML/Markdown presentation
  tokens from grounding attribution text. On a fixed 20-case Layout sample,
  the official score improved from 62.31% to 76.71% with zero regressions.
- Completed the clean 500-case Visual Grounding evaluation at 71.6970% and
  updated the five-dimension equal-weight overall to 65.3520%. The report also
  distinguishes that leaderboard aggregation from the 65.4367% case-weighted
  mean across 2,534 numeric results.
- Added public deployment, hardware, configuration, operations, and security
  documentation plus a pinned ParseBench evaluation report.
- Removed environment-specific deployment history, internal hosts, repository
  locations, and local filesystem details from the imported source and docs.
- Validation covers the FileX unit/integration test suite, AWorld skill wrapper
  tests, secret/local-information scans, and container build-contract checks.

Compatibility: the default `aworld-tools/filex/Dockerfile` remains the AWorld
all-in-one image. GPU service deployments use `Dockerfile.gpu-service`.
