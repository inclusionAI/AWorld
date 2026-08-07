---
name: filex-pdf
description: Parse PDF files into Markdown with the FileX CLI inside an AWorld local or remote sandbox. Use when an aworld-cli task needs to read, extract, inspect, summarize, or answer questions about a PDF, including selected pages and long PDFs processed in page batches.
---

# Parse PDFs with FileX

Use the bundled wrapper to validate the input, invoke `filex parse`, locate the generated Markdown, and return a small JSON result that is safe for an agent to consume.

## Workflow

1. Confirm the PDF is visible inside the execution environment. For the FileX image this normally means an absolute path under `/root/workspace`.
2. Confirm `filex` is installed with `command -v filex`. If it is missing, report that the current sandbox image does not contain FileX. Do not fall back to AWorld's built-in PDF parser because it is unsupported.
3. Run the wrapper through the terminal tool:

```bash
python3 /skills/filex-pdf/scripts/parse_pdf.py \
  --input /root/workspace/input.pdf \
  --output /root/workspace/input.md
```

4. Parse stdout as JSON. Treat the run as successful only when `success` is `true` and `output_path` exists.
5. Read the generated Markdown with the filesystem text tool or in bounded terminal chunks. Use that content for the user's requested summary, extraction, or question answering.

## Options

- Parse selected one-based pages with `--pages 1,3-5`.
- Bound long-document work with `--page-batch-size 10` and optionally set the first consumable batch with `--first-batch-pages 10`.
- Select a configured FileX provider with `--provider liteparse` or `--provider paddle_ocr`. Omit it to use the image default.
- Bypass cached results with `--no-cache`, or refresh an existing entry with `--force-refresh`.
- Omit `--output` to keep FileX's generated Markdown path.

Example for selected pages:

```bash
python3 /skills/filex-pdf/scripts/parse_pdf.py \
  --input /root/workspace/report.pdf \
  --pages 1-5,20 \
  --page-batch-size 5 \
  --provider liteparse
```

## Guardrails

- Never pass credentials on the command line. Provider and service credentials belong in the image configuration or environment.
- Keep the source PDF unchanged. The wrapper only reads it and optionally copies the generated Markdown to `--output`.
- Do not claim OCR, table, formula, or layout fidelity without inspecting the generated Markdown.
- If a host-only path is unavailable in a remote sandbox, copy or upload the PDF into the sandbox workspace before parsing.
- Preserve FileX's error message and task ID when reporting failures.
