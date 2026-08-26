# Changelog

## Unreleased

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
