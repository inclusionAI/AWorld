---
name: filex
description: Parse workspace files, HTTP(S) file URLs, or supported source URLs such as YouTube into Markdown, inspect source routing, and inspect resumable PDF batch status with the FileX CLI inside an AWorld sandbox. Use for reading, extracting, transcribing, inspecting, summarizing, or answering questions about PDF, Word, PowerPoint, Excel, CSV, text, Markdown, image, audio, or video files.
default_enabled: true
metadata:
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

## Parse a local file

Confirm the file is under the sandbox workspace, normally `/root/workspace`, then run:

```bash
python3 /skills/filex/scripts/filex.py parse \
  --input /root/workspace/input.docx \
  --output /root/workspace/input.md
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

The default `--layout-format document-ir` writes `document.md`, FileX Document IR
as `layout.json`, and a version 1 `result.json` containing source/output hashes
and the unmodified FileX response.

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
hashes, and the unmodified FileX response. The task's requested `document.md` and
`layout.json` are the submission artifacts; the other files retain provenance.

ParseOutput export requires a provider that emits real layout geometry. For
PNG, JPG/JPEG, WebP, GIF, and BMP inputs with `--artifacts-dir` and
`--layout-format parse-output`, the wrapper selects `paddle_ocr` when no
`--provider` or `--env-file` was supplied. This also applies when the format comes
from `FILEX_LAYOUT_FORMAT`. PDF already defaults to Paddle in FileX. Explicit
provider/configuration choices and the legacy `document-ir` behavior stay
unchanged; choose a layout-capable provider when overriding this export path.

Use the task's source path, selected pages, artifact paths, and output format.
For a selected PDF page, include `--pages` with the original one-based page
number. Read only the task's supplied material; evaluation and private reference
data stay with the task's verifier. If the parser cannot supply real page
dimensions, boxes, or a supported label, export reports a clear error. Actual
empty page/item lists are preserved; text-layer spans without complete boxes
remain in the original Document IR.

Operators can set `FILEX_LAYOUT_FORMAT=parse-output` as the default; an explicit
`--layout-format` takes precedence. The wheel supplies the format converter.
`FILEX_PYTHON` selects the Python environment containing that FileX wheel when it
differs from the wrapper's Python; by default the wrapper uses its own Python.
These controls select an output representation and do not select a model or run
evaluation.

## Select a provider

Use `--provider` when the provider needs no credentials on the command line:

```bash
python3 /skills/filex/scripts/filex.py parse \
  --input /root/workspace/report.pdf \
  --provider liteparse
```

Available providers depend on the file type and image configuration. They include Paddle OCR, LiteParse, PyPDF+VLM, native Office/text/table providers, image VLM, and local Whisper. Let FileX select the default unless the task requires a specific capability.

For provider credentials or complex configuration, create a protected JSON file in the workspace and pass `--env-file`. Put `filex_parse_provider` in that file when selecting a provider. Do not combine `--provider` with `--env-file`.

## Parse a URL

Pass an HTTP(S) URL directly. FileX downloads it into a bounded workspace cache before parsing:

```bash
python3 /skills/filex/scripts/filex.py parse \
  --url https://example.com/report.pdf \
  --file-type pdf
```

The default maximum download is 512 MiB and the default timeout is 120 seconds.
Operators may adjust `FILEX_MAX_DOWNLOAD_BYTES` and
`FILEX_DOWNLOAD_TIMEOUT_SECONDS` on the container.

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

For PDF only, use `--pages 1,3-5`, `--page-batch-size 10`, `--first-batch-pages 10`, or `--batch-resume-id stable-id`. Add `--sync-mode async` for background parsing.

Read resumable progress with:

```bash
python3 /skills/filex/scripts/filex.py status \
  --batch-resume-id stable-id \
  --include-results \
  --after-batch 0
```

Use `--no-cache` to bypass cache or `--force-refresh` to refresh an existing result.

## Consume results

For synchronous parsing, read `output_path` with the filesystem text tool or bounded terminal chunks. For asynchronous parsing, preserve the returned task/batch identifiers and poll `status`. Inspect the generated Markdown before claiming OCR, table, formula, layout, transcription, or image-understanding fidelity.

## Guardrails

- Confirm `command -v filex` before use. Report a missing FileX-enabled image instead of falling back to AWorld's unsupported built-in PDF parser.
- Keep source files unchanged and keep every local input, output, and environment file inside the workspace.
- Never place credentials directly in a command, prompt, log, skill file, or generated Markdown.
- Prefer a workspace path for private files; use `--url` only for a trusted HTTP(S) source.
- Preserve FileX error messages, task ids, warnings, metrics, and partial-success information.
