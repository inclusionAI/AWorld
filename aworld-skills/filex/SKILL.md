---
name: filex
description: Parse local or remote files into Markdown, download AFTS files, and inspect resumable PDF batch status with the FileX CLI inside an AWorld sandbox. Use for reading, extracting, transcribing, inspecting, summarizing, or answering questions about PDF, Word, PowerPoint, Excel, CSV, text, Markdown, image, audio, or video files.
---

# Use FileX

Use the bundled wrapper for FileX `parse`, `save`, and `status`. It validates workspace paths, keeps credentials out of command-line arguments, preserves FileX JSON fields, and returns `output_path` for synchronous parsing.

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

## Select a provider

Use `--provider` when the provider needs no credentials on the command line:

```bash
python3 /skills/filex/scripts/filex.py parse \
  --input /root/workspace/report.pdf \
  --provider liteparse
```

Available providers depend on the file type and image configuration. They include Paddle OCR, LiteParse, PyPDF+VLM, native Office/text/table providers, image VLM, and local Whisper. Let FileX select the default unless the task requires a specific capability.

For provider credentials or complex configuration, create a protected JSON file in the workspace and pass `--env-file`. Put `filex_parse_provider` in that file when selecting a provider. Do not combine `--provider` with `--env-file`.

## Parse remote AFTS files

Use the remote file id with an environment file:

```bash
python3 /skills/filex/scripts/filex.py parse \
  --file-id AFTS_FILE_ID \
  --file-type pdf \
  --env-file /root/workspace/filex-env.json
```

To download without parsing:

```bash
python3 /skills/filex/scripts/filex.py save \
  --file-id AFTS_FILE_ID \
  --output /root/workspace/source.pdf \
  --env-file /root/workspace/filex-env.json
```

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
- Upload host-only files into a remote sandbox workspace before parsing.
- Preserve FileX error messages, task ids, warnings, metrics, and partial-success information.
