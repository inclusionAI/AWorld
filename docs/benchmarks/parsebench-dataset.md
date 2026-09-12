# ParseBench executable dataset authoring

AWorld's ParseBench authoring module converts an already-present, pinned
Hugging Face-style checkout into one deterministic
`yolo-dataset-package/v2` ZIP. It does not download the dataset, call FileX,
or call a model.

The supported release is fixed to:

- Dataset revision: `2805a1d940f95a203e0ae4b88be9934f7765b3fc`
- Official scorer revision: `34b73455032797754f6ed62e14c27a8b5423d11e`
- Source files: `chart.jsonl`, `layout.jsonl`, `table.jsonl`,
  `text_content.jsonl`, and `text_formatting.jsonl`
- Expected source cardinality: 169,011 rules and 2,078 unique source-file
  parse executions

Rules are grouped by the normalized source path and one-indexed `page` value.
Most upstream files are already single-page excerpts and therefore use an
all-document execution key; an explicitly paged source is kept as a separate
parse execution for each page.

## Prepare a package

The local source directory must be either a Git checkout at the pinned
revision, a Hugging Face cache snapshot whose directory name is that revision,
or an exported snapshot containing a `.parsebench-revision` file with exactly
that revision. Git LFS/Xet objects must already be materialized.
Standard snapshot links into the same Hugging Face repository cache's `blobs/`
directory are accepted; links escaping either the checkout or that bounded
blob directory are rejected.

For a deliberately small local integration package:

```bash
python -m aworld.benchmarks.parsebench.dataset \
  --source /path/to/ParseBench \
  --output /path/to/parsebench-smoke.zip \
  --smoke-per-dimension 1 \
  --smoke-seed parsebench-smoke-v1 \
  --runtime-image registry.example/aworld-filex-parsebench@sha256:<digest>
```

Omit `--smoke-per-dimension` to author the full 2,078-task package. The
converter validates the complete pinned checkout before selecting a smoke
subset, so a partial or silently revised corpus cannot be presented as the
official release. A production package should always pass an immutable runtime
image digest; the default `aworld-filex-parsebench:local` name exists only for
local contract development.

## Privacy boundary

Each generated task archive has this shape:

```text
<task-id>/
├── task.toml
├── instruction.md
├── environment/
│   ├── Dockerfile
│   └── input/document.<ext>
└── tests/
    ├── test.sh
    └── ground_truth.json
```

Only the source document and generic parse instruction enter `environment/`.
Rule payloads, rule IDs, tags, expected Markdown, and their JSONL line
provenance remain under verifier-owned `tests/`. The public `dataset.jsonl`
contains task identity, source checksums, dimensions, and output artifact
contracts, but no scoring ground truth.

`manifest.json` records the checksum and size of the canonical catalog and
every task archive. Its provenance section also records checksums and row
counts for all five source JSONL files, the source/scorer revisions, source and
selected cardinalities, converter version, runtime image reference, and
deterministic smoke-selection parameters.

The included `tests/test.sh` targets the future dedicated
`aworld-cli benchmark parsebench verify-task` command. This dataset foundation
does not add that command or use the generic LLM-judge evaluator path.
