---
name: filex
description: Parse workspace files, HTTP(S) file URLs, or supported source URLs such as YouTube into Markdown, inspect source routing, and inspect resumable PDF batch status with the FileX CLI inside an AWorld sandbox. Use for reading, extracting, transcribing, inspecting, summarizing, or answering questions about PDF, Word, PowerPoint, Excel, CSV, text, Markdown, image, audio, or video files.
metadata:
  default_enabled: true
  match_keywords:
    - pdf
    - docx
    - pptx
    - xlsx
    - csv
    - document
    - spreadsheet
    - powerpoint
    - extract text
    - read this file
    - parse this file
    - transcribe
    - transcript
    - .doc
    - .ppt
    - .xls
    - .txt
    - .md
    - .png
    - .jpg
    - .jpeg
    - .webp
    - .gif
    - .bmp
    - .mp3
    - .wav
    - .m4a
    - .aac
    - .flac
    - .ogg
    - .opus
    - .mp4
    - .mov
    - .mkv
    - .webm
    - .avi
    - .m4v
    - .mpeg
    - .mpg
    - youtube.com/
    - youtu.be/
    - 文档
    - 表格
    - 提取文字
    - 转录
    - 字幕
---

# Use FileX

Choose this skill when the task needs content from an existing document or media
source. FileX does not need to run for unrelated tasks or to create new documents
or media.

Use the bundled wrapper for FileX `inspect`, `parse`, and `status`. It validates workspace paths, resolves supported URL sources, keeps credentials out of command-line arguments, preserves FileX JSON fields, and returns `output_path` for synchronous parsing.

## Use the AWorld runtime contract

Invoke FileX through the bundled AWorld wrapper from any working directory:

```bash
python3 /skills/filex/scripts/filex.py --help
```

Do not change into `/app/mcp_servers/filesystem_server`, call `./bin/filex`, or
use `/root/fs_workspace`. Those paths belong to a different MCP filesystem-server
image. In a managed AWorld sandbox, `FILEX_WORKSPACE_ROOT` is commonly
`/workspace`; standalone AWorld images may use `$HOME/workspace`. Use the
runtime-provided value and the task's supplied path instead of deriving a path
from the current directory. `/skills/filex` is read-only and is never an output
location.

The wrapper accepts local `--input` files under `FILEX_WORKSPACE_ROOT` and
HTTP(S) `--url` sources. Keep the skill focused on those ordinary AWorld source
flows rather than deployment-specific object-store workflows.

## Follow the parsing workflow

1. Identify the source and confirm that a local file already resides under
   `FILEX_WORKSPACE_ROOT`; otherwise use a trusted HTTP(S) URL or ask the caller
   to stage it. Do not make an unnecessary base64 copy of a mounted file.
2. Let FileX infer the file type when the suffix and content are reliable. Use
   `--file-type` only to resolve ambiguity.
3. Let the configured provider handle ordinary files. Select an explicit
   provider only when the task needs a known capability such as scanned-page
   OCR, real layout geometry, or text-layer-first VLM fallback.
4. Keep a simple provider choice in `--provider`. Put provider-private settings
   and structured values in a protected workspace JSON file and use
   `--env-file`; never combine those two options.
5. Run the parse synchronously in the foreground with enough terminal-tool
   time. For evaluation or durable handoff, request an artifact bundle and an
   explicit layout format.
6. Validate the JSON result, then read `output_path` and inspect the generated
   Markdown or artifact bundle. Logs are diagnostic evidence, not the result
   contract.
7. On failure, preserve the wrapper's JSON error, provider/model identity, task
   id, and any checkpoint. Retry only after the first process has ended and the
   failure is understood.

## Allow time for document parsing

Run FileX parsing synchronously in the foreground. When using the terminal
`run_code` tool, set its `timeout` explicitly, for example:

```json
{
  "code": "python3 /skills/filex/scripts/filex.py parse --input /workspace/input/report.pdf --sync-mode sync --artifacts-dir /logs/artifacts",
  "timeout": 900
}
```

Use `timeout: 1800` for a larger document or extensive chart recognition when
the remaining task budget permits it. These are terminal tool arguments, not
FileX CLI flags. The framework caps the command at its configured maximum and
the remaining task deadline, reserving time to finish the task.

OCR and remote VLM chart recognition can take more than 120 seconds, even for
one page. Wait for the tool result; elapsed time alone does not mean parsing
failed. Do not wrap the command in `nohup` or `&`, start duplicate parses, or
kill and restart a parser simply because it is slow. If a tool call actually
times out, preserve its error and establish that the prior command has ended
before retrying within the remaining task budget.

The standalone CLI's `--sync-mode async` does not create a persistent worker:
its background coroutine belongs to the CLI process and cannot be relied on
after that process exits. Do not use it to produce task artifacts;
`--artifacts-dir` requires synchronous parsing. A separately deployed
`filex-server` offers asynchronous HTTP jobs through its own submission and
job-status API; the local CLI `status` command is not that API.

## Parse a local file

Confirm the file is under the sandbox workspace. In the Harbor runtime this is
normally `/workspace`, then run:

```bash
python3 /skills/filex/scripts/filex.py parse \
  --input /workspace/input.docx \
  --output /workspace/input.md
```

FileX supports:

- Documents: PDF, TXT, Markdown, DOC/DOCX, PPT/PPTX.
- Tables: CSV, XLS/XLSX.
- Images: PNG, JPG/JPEG, WebP, GIF, BMP.
- Audio: MP3, WAV, M4A, AAC, FLAC, OGG, OPUS.
- Video: MP4, MOV, MKV, WebM, AVI, M4V, MPEG, MPG.

Omit `--output` to keep FileX's generated Markdown path. Use `--file-type` only when extension or content detection is insufficient. Parse stdout as JSON and continue only when `success` is `true`.

When a task requires durable, machine-verifiable output, use `--artifacts-dir`. The
directory must be under `FILEX_ARTIFACTS_ROOT` (normally `/logs/artifacts`):

```bash
python3 /skills/filex/scripts/filex.py parse \
  --input /workspace/input/report.pdf \
  --no-cache \
  --artifacts-dir /logs/artifacts
```

For any workflow whose result is accepted only with machine-verifiable artifacts,
treat the following as one canonical command shape. Substitute the actual source,
artifact directory, and registered provider without dropping any option:

```bash
python3 /skills/filex/scripts/filex.py parse \
  --input "$SOURCE_PATH" \
  --provider "$FILEX_PROVIDER" \
  --no-cache \
  --layout-format parse-output \
  --artifacts-dir "$ARTIFACTS_DIR"
```

Invoke that wrapper directly as the terminal tool command. Do not append a shell
pipeline such as `| tail` or `| tee`, combine it with `2>&1`, redirect its JSON
stdout, add `|| true`, or wrap it in a command whose status can replace the
wrapper's exit status. The terminal tool already captures bounded stdout and
stderr. Success requires both a zero process status and JSON `success: true`.

If a retry is justified, preserve the source/page selection, `--artifacts-dir`,
`--layout-format parse-output`, `--no-cache`, and the same `--provider` or
`--env-file`. A deliberate provider fallback must name another registered FileX
provider, retain all artifact-contract options, and be reported as a provider
change; never turn an omitted option into an accidental fallback. Wait for each
attempt to end before starting the next one.

If every FileX attempt fails, preserve the last nonzero status and JSON error and
fail the artifact-producing task. Do not use Python, shell, another parser, or
manual editing to synthesize `document.md`, `layout.json`, or `result.json` after
FileX failed. Such files do not prove FileX execution.

When `--layout-format document-ir` is selected, the bundle contains
`document.md`, FileX Document IR as `layout.json`, and a version 1 `result.json`
with source/output hashes and the unmodified FileX response.

When the task requests `document.md` and a public ParseOutput `layout.json`
containing `layout_pages`, export that format explicitly:

```bash
python3 /skills/filex/scripts/filex.py parse \
  --input /workspace/input/report.pdf \
  --no-cache \
  --layout-format parse-output \
  --artifacts-dir /logs/artifacts
```

This preserves the generated Markdown exactly, exports actual element boxes as
pixel `x`, `y`, `w`, `h` coordinates with Canonical17 labels, and preserves source
page numbers and reading order. Tables retain their Markdown/HTML content. The
original Document IR is separately preserved byte for byte as `document-ir.json`.
A version 2 `result.json` records the layout format, source hash, all three output
hashes, the unmodified FileX response, and a `filex_provenance` receipt. The
receipt identifies the effective provider and binds the source, original Document
IR, exported Markdown/layout, and FileX response by SHA-256. Treat the export as
FileX-produced only when the receipt says `producer: filex`, `status: succeeded`,
and its hashes match the current files. The task's requested `document.md` and
`layout.json` are the submission artifacts; the other files retain provenance.

Keep the exported `layout.json` structure. Each `items[].bbox` is a box object;
each optional `items[].layout_segments[]` entry is itself a box object with
`x`, `y`, `w`, `h`, `label`, and optional `confidence` directly on the entry:

```json
{
  "type": "text",
  "md": "Section heading",
  "bbox": {"x": 10, "y": 20, "w": 180, "h": 24, "label": "section-header"},
  "layout_segments": [
    {"x": 10, "y": 20, "w": 180, "h": 24, "label": "section-header"}
  ]
}
```

Do not wrap a segment in another `bbox` object. When a valid item has only its
single `bbox`, `layout_segments` may be omitted. Before finishing, check every
page and every item/segment, not just whether JSON loads: page dimensions must
be positive, coordinates must be finite numbers, `x`/`y` must be nonnegative,
`w`/`h` must be positive, and boxes must stay within their page. Preserve the
actual source geometry and Canonical17 labels. If you use another parser or
edit the artifacts, repeat these checks; a well-formed JSON file alone does
not establish that the layout follows the required schema.

ParseOutput export requires a provider that emits real layout geometry. For
PNG, JPG/JPEG, WebP, GIF, and BMP inputs with `--artifacts-dir` and
`--layout-format parse-output`, the wrapper selects `paddle_ocr` when no
`--provider` or `--env-file` was supplied. This also applies when the format comes
from `FILEX_LAYOUT_FORMAT`. Explicit provider/configuration choices and the
legacy `document-ir` behavior stay unchanged. For PDF, do not rely on a
deployment's provider default when real geometry is required; explicitly choose
a layout-capable provider such as `paddle_ocr`.

Use the task's source path, selected pages, artifact paths, and output format.
For a selected PDF page, include `--pages` with the original one-based page
number. Read only the task's supplied material; evaluation and private reference
data stay with the task's verifier. If the parser cannot supply real page
dimensions, boxes, or a supported label, export reports a clear error. Actual
empty page/item lists are preserved; text-layer spans without complete boxes
remain in the original Document IR.

A managed artifact-producing runtime may set `FILEX_LAYOUT_FORMAT=parse-output`;
the standalone wrapper falls back to `document-ir` when the variable is absent.
An explicit `--layout-format` takes precedence, so specify it when the artifact
contract must be deterministic. The wheel supplies the format converter.
`FILEX_PYTHON` selects the Python environment containing that FileX wheel when it
differs from the wrapper's Python; by default the wrapper uses its own Python.
These controls select an output representation and do not select a model or run
evaluation.

## Select a provider

For PDF input, choose only providers registered in this AWorld FileX build:

| Provider | Aliases | Use it for |
| --- | --- | --- |
| `liteparse` | none | Native-text or general PDF parsing, with optional OCR configured through an environment file |
| `paddle_ocr` | `paddleocr`, `paddle` | Scans, layout, tables, and chart-aware OCR; requires the Paddle models to be present |
| `pypdf_vlm` | `pypdf+vlm`, `vlm_pdf` | Text-layer-first parsing with per-page VLM fallback |

`unlimited_ocr` and `page_images` are not providers in this release. Likewise,
the wrapper does not accept another distribution's `--parse-provider`,
`--unlimited-ocr-*`, `--page-images-*`, or provider-specific direct flags.
Use `python3 /skills/filex/scripts/filex.py parse --help` to inspect the wrapper
syntax. Provider availability is governed by this AWorld build's registry, not
by flags shown in another distribution's help.

Use `--provider` for a simple provider choice that needs no request-specific
configuration:

```bash
python3 /skills/filex/scripts/filex.py parse \
  --input /workspace/report.pdf \
  --provider liteparse
```

Available providers depend on the file type and installed dependencies. Native
Office/text/table providers, image VLM, and local Whisper are selected for their
matching formats. Do not assume the PDF default is Paddle: the bundled FileX
configuration may select LiteParse. Choose `paddle_ocr` explicitly when real
layout geometry, scanned-page OCR, or chart-aware parsing is required.

For provider-private or low-frequency settings, create a protected JSON object
inside the workspace and pass `--env-file`. Put `filex_parse_provider` in that
file when selecting a provider. Do not combine `--provider` with `--env-file`.
For example, `/workspace/filex-options.json` may contain:

```json
{
  "filex_parse_provider": "pypdf_vlm",
  "pdf_vlm_max_pages": 20,
  "pdf_vlm_max_concurrency": 2,
  "pdf_vlm_max_retries": 3,
  "pdf_vlm_timeout_seconds": 600,
  "pdf_render_dpi": 150,
  "pdf_jpeg_quality": 85
}
```

Then run:

```bash
chmod 600 /workspace/filex-options.json
python3 /skills/filex/scripts/filex.py parse \
  --input /workspace/report.pdf \
  --env-file /workspace/filex-options.json
```

Other supported families include `liteparse_*` settings and `paddle_ocr_*`
settings. Keep exact JSON scalar types: booleans must be JSON booleans and
numeric limits must be numbers. Passwords, long prompts, `extra_body`, and any
other sensitive or structured values belong in the protected JSON file, never
inline in a command.

The AWorld runtime supplies the selected FileX VLM independently of the Agent
model through `GATEWAY_VLLM_*`. When a managed runtime supplies that profile, do
not put a `gateway_vllm` object in `--env-file`: it would override the selected
VLM, weaken reproducibility, and may use mismatched credentials. Never copy the
Agent model credentials into the task. A standalone operator that needs a
different VLM must configure that runtime outside the agent's parsing command.

If PaddleOCR reports `No available model hosting platforms detected`, it was
trying to acquire a missing local model from a model-weight download host.
This does not show that the Agent model or configured VLM inference API is
unreachable. Preserve the error and check whether the required layout model
is installed and visible at `FILEX_PADDLE_OCR_LAYOUT_DETECTION_MODEL_DIR`.
Operators must supply missing model assets in the image or cache; bypassing a
host connectivity check does not install those assets. Do not claim successful
parsing or invent replacement geometry when the parser failed.

## Parse a URL

Pass an HTTP(S) URL directly. FileX downloads it into a bounded workspace cache before parsing:

```bash
python3 /skills/filex/scripts/filex.py parse \
  --url https://example.com/report.pdf \
  --file-type pdf
```

The default maximum download is 512 MiB and the default download timeout is 120 seconds.
Operators may adjust `FILEX_MAX_DOWNLOAD_BYTES` and
`FILEX_DOWNLOAD_TIMEOUT_SECONDS` on the container.

Do not base64-encode a large host file into a terminal command. Harbor mounts
task files into its workspace; use that path when present. In a standalone
runtime, mount or stage the file first. Otherwise use an authorized URL. This
avoids shell quoting damage and command-length limits.

## Inspect and parse YouTube

Inspect metadata, chapters, subtitle tracks, publisher transcript candidates, and
the recommended route without downloading media:

```bash
python3 /skills/filex/scripts/filex.py inspect \
  --url "https://www.youtube.com/watch?v=VIDEO_ID"
```

Parse the best available text track into timestamped Markdown:

```bash
python3 /skills/filex/scripts/filex.py parse \
  --url "https://www.youtube.com/watch?v=VIDEO_ID" \
  --mode transcript \
  --language en
```

FileX prefers human subtitles, then automatic captions. If neither exists, do
not download media unless the user explicitly confirms an applicable rights
basis. Only then use `--allow-media-download --rights-basis user-owned` (or
`licensed`, `service-permitted`, `applicable-law`) to acquire audio for local
Whisper. Never infer permission, use browser cookies, or bypass access controls.

The current YouTube source provider records publisher transcript candidates but
does not fetch arbitrary external HTML. It does not yet perform scene detection,
keyframe OCR, or full audiovisual understanding; preserve these limitations in
the response.

## PDF page and batch controls

For PDF only, use `--pages 1,3-5`, `--page-batch-size 10`, `--first-batch-pages 10`,
or `--batch-resume-id stable-id`. Keep parsing synchronous. After a failed
command has ended, repeating the same parse with its stable resume id can reuse
completed batches.

Read resumable progress with:

```bash
python3 /skills/filex/scripts/filex.py status \
  --batch-resume-id stable-id \
  --include-results \
  --after-batch 0
```

`status` reads saved PDF batch checkpoints. It does not launch or keep a worker
alive, and a successful batch checkpoint does not replace the parse command's
final artifact export. A completed synchronous parse must still return its
output paths successfully.

Use `--no-cache` to bypass cache or `--force-refresh` to refresh an existing result.

## Consume results

After synchronous parsing succeeds, read `output_path` with the filesystem text
tool or bounded terminal chunks. Preserve any task and batch identifiers for
diagnostics and resuming completed batches. Inspect the generated Markdown
before claiming OCR, table, formula, layout, transcription, or
image-understanding fidelity.

Treat stdout as the result contract. Success requires JSON `success: true` and,
for synchronous parsing, a readable `output_path`. Do not guess a result path
such as `/root/fs_workspace/document_parse/<task_id>/...`. Use only the paths
returned by the wrapper.

For a lightweight format smoke test, parse one small file through the same
wrapper and check both the JSON and `output_path`. Do not rely on provider log
lines: the wrapper's bounded JSON response is the observable contract.

## Troubleshoot the AWorld deployment

- `Path must be inside the FileX workspace`: use the existing task path under
  `FILEX_WORKSPACE_ROOT` (`/workspace` in Harbor), not `/root/fs_workspace`, the
  host path, or a path under `/skills`.
- `FileX executable not found`: confirm `command -v filex`; report a missing or
  stale FileX-enabled runtime image instead of installing an ad-hoc local copy.
- A dependency appears missing on the host: reproduce through the sandbox
  wrapper. A host virtual environment does not establish what is installed in
  the AWorld runtime.
- JSON reports success but no inline Markdown: use the wrapper's `output_path`.
  The content is intentionally kept in a file rather than copied into stdout.
- Office conversion fails: preserve the FileX JSON error and task id. Do not
  infer success from an intermediate conversion phase.
- A VLM request returns 401/403: preserve the requested provider and model
  identity. This is a FileX VLM profile/credential problem, not evidence that
  the independently configured AWorld Agent model is unavailable.

## Guardrails

- Confirm `command -v filex` before use. Report a missing FileX-enabled image instead of falling back to AWorld's unsupported built-in PDF parser.
- Keep source files unchanged and keep every local input, output, and environment file inside the workspace.
- Never place credentials directly in a command, prompt, log, skill file, or generated Markdown.
- Prefer a workspace path for private files; use `--url` only for a trusted HTTP(S) source.
- Do not use MCP filesystem-server paths or repository-local smoke scripts unless they are actually mounted in this runtime.
- Preserve FileX error messages, task ids, warnings, metrics, and partial-success information.
