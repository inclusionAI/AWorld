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

The local source directory must be either a clean Git checkout at the pinned
revision or a real Hugging Face cache snapshot at that revision. For Git, all
five JSONL files and every referenced document must be tracked and unchanged;
staged, unstaged, untracked, or ignored source material is rejected. A plain
`.parsebench-revision` marker is intentionally not accepted because it is not
revision evidence.

For a Hugging Face cache snapshot, every required material must resolve into
the same repository cache's bounded `blobs/` directory. A 40-character blob
name is verified as a Git blob SHA-1 and a 64-character blob name is verified
as a raw SHA-256. Renaming a directory to the pinned revision, copying loose
files into it, or changing a blob without changing its content address fails
closed. Git LFS/Xet objects must already be materialized. Links escaping either
the checkout or the bounded blob directory are rejected.

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
│   ├── parsebench-task.json
│   └── input/document.<ext>
└── tests/
    ├── Dockerfile
    ├── test.sh
    └── ground_truth.json
```

Only the source document and a versioned public execution contract enter
`environment/`. `parsebench-task.json` contains exactly its schema version,
task ID, pinned dataset/scorer revisions, and source runtime path, size,
checksum, and optional page. It contains no dimensions, rule counts, rules,
tags, original category-bearing source path, or expected Markdown. The public
`dataset.jsonl` follows the same fairness boundary: it carries execution and
artifact transport metadata but no per-task scoring dimension or rule count.

Rule payloads, rule IDs, tags, expected Markdown, dimensions, and their JSONL
line provenance remain under verifier-owned `tests/`. `task.toml` selects
`verifier.environment_mode = "separate"` and gives the verifier its own
no-network build environment. The agent writes `document.md`, `layout.json`,
and `result.json` directly below `/logs/artifacts`. Harbor collects those
artifacts, stops the agent environment, starts the verifier from
`tests/Dockerfile`, and restores the files at the same `/logs/artifacts` paths
before `tests/test.sh` runs. The private `tests/` tree is never uploaded into
the agent environment.

The authored top-level environment deliberately has neither `image` nor
`docker_image`: `environment/Dockerfile` must be built so the per-task source
and public contract are copied into the image. In the mcpgateway + Arca path,
Runtime freezes that built per-task image and injects its immutable
`docker_image` into an execution copy of `task.toml`. A direct Harbor run must
likewise build the authored Dockerfile rather than selecting the common runtime
base as a prebuilt task image.

`manifest.json` records the checksum and size of the canonical catalog and
every task archive. Its provenance section also records checksums and row
counts for all five source JSONL files, the source/scorer revisions, source and
selected cardinalities, converter version, runtime image reference, and
deterministic smoke-selection parameters. It also records whether revision
evidence came from a clean Git checkout or a content-addressed Hugging Face
snapshot, plus the public task-contract version and runtime path.

The included `tests/test.sh` targets the future dedicated
`aworld-cli benchmark parsebench verify-task` command. This dataset foundation
does not add that command or use the generic LLM-judge evaluator path.
