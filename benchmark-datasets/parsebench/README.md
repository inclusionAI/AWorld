# Public ParseBench Dataset project

This converter packages the complete public `llamaindex/ParseBench` release as a
standard executable Dataset accepted by an unmodified lingguang-bench-client and
mcpgateway. The Dataset does not select an Agent, skill, model, or parsing library.
AWorld/FileX may participate as one candidate through a separate run configuration.

The source revision is `2805a1d940f95a203e0ae4b88be9934f7765b3fc`: 2,078 unique
input tasks and 169,011 rules across table, chart, text content, text formatting,
and layout. Ground truth is available only in the isolated verifier context.

## Build the complete package

```bash
PYTHONPATH=benchmark-datasets/parsebench/tools \
python -m parsebench_dataset.project \
  --source /path/to/verified/ParseBench/snapshot \
  --output /path/to/parsebench-project

PYTHONPATH=/path/to/lingguang-bench-client/src \
python benchmark-datasets/parsebench/package_with_lingguang.py \
  --project /path/to/parsebench-project \
  --output /path/to/parsebench-public.zip
```

Omitting `--smoke-per-dimension` produces the full release. The source loader
checks all material sizes/hashes and the actual Git/Hugging Face revision.
`bench.toml` contains an optional local client run profile. Its Agent/model/service
settings are not embedded in the uploaded Dataset; configure them before running
an evaluation. Packaging and image builds do not require a registered Agent service.

## Public task and verifier images

Both Dockerfiles start from a fixed digest of the public official
`python:3.12-slim-bookworm` image. The Agent environment includes standard Poppler
PDF tools and the task input. It installs no Agent SDK, FileX, OCR model, or private
wheel bundle.

The verifier builds its own scorer from the fixed public upstream GitHub archive
at `34b73455032797754f6ed62e14c27a8b5423d11e`, checks the archive hash and every
scorer source file, and installs public scoring dependencies with exact versions
and hashes from the official lockfile. Build-time downloads use public endpoints;
verification runs without network access or model credentials. Source provenance
retains some historical `AWORLD_*` manifest/schema names for compatibility; these
are data attestations and introduce no AWorld package or runtime dependency.

`--base-image` can override the authoring base for local experiments; noncanonical
or mutable overrides are marked non-publishable. The historical `--runtime-image`
flag and scope `runtime_image` field remain aliases for this Dataset base image.
They no longer refer to the separately deployed Agent Runtime.

## Candidate output contract

Every candidate writes two files:

- `/logs/artifacts/document.md`: the parsed UTF-8 Markdown, preserved verbatim.
- `/logs/artifacts/layout.json`: the official ParseOutput `layout_pages` format,
  with one-based page numbers, pixel dimensions, ordered items, and Canonical17
  labeled pixel `x,y,w,h` boxes. The task instructions include an example.

No FileX result envelope, provider name, API key, VLM call count, or model identity
is required. The verifier supplies neutral inference metadata and calls the fixed
upstream scoring implementation. It preserves the official five-dimensional
metrics and distinguishes incorrect predictions from invalid submissions and
scoring failures. Legacy FileX conversion is available only as an explicit mode
in the source tooling; its adapter module is not shipped in the public package.

## Release checks

The full selection pin covers task instructions, public contracts, ground truth,
both Dockerfiles, the embedded verifier, and scorer installation resources.
Recompute it when any of those bytes change, then run the full release check:

```bash
PARSEBENCH_SOURCE=/path/to/verified/ParseBench/snapshot \
PARSEBENCH_RUNTIME_SOURCE=/path/to/reference-runtime-source \
PYTHONPATH=benchmark-datasets/parsebench/tools \
python -m pytest benchmark-datasets/parsebench/tests
```

`PARSEBENCH_RUNTIME_SOURCE` only enables a compatibility test against the previously
vendored official scorer manifest; neither authoring nor image building requires
that repository. The container tests additionally cover actual public image builds,
five-dimensional offline scoring, wrong/empty predictions, malformed artifacts,
and source tampering.
