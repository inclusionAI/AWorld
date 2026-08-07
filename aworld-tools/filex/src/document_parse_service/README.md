# document_parse_service

Typed document parsing pipeline for `filesystem_server`.

## Responsibilities

- Parse local `workspace_path` sources; the CLI downloads HTTP(S) URLs into the workspace first.
- Route by file type through `DocumentServiceFactory`.
- Produce Markdown as the single normalized output.
- Keep extracted assets and parsed Markdown in the mounted workspace.

## Key Files

- `service.py` - shared runtime used by MCP tools and CLI.
- `cli.py` - `filex` CLI entrypoint.
- `pdf_page_selection.py` - shared one-based PDF page-range validation and provider-neutral subset generation.
- `document_service_factory.py` - file-type routing.
- `document_parse_executor.py` - shared parsing execution and Markdown output.
- `document_parse_metrics.py` - versioned common metrics and file-type-specific metric schemas.
- `provider_registry.py` - canonical provider names, supported formats, capability metadata, versions, and request validation.
  Metrics are returned with the parse result and persisted beside the Markdown output as `<name>.metrics.json`.
  Common timing and model fields stay comparable across formats, while PDF, presentation, word-processing, spreadsheet, image, audio, and video details live under `type_metrics`.
- Extracted images use local workspace references by default in the public image.
- `pdf/` - PDF-specific services and providers:
  - `pdf_document_service.py` - PDF document pipeline.
  - `pdf_batch_checkpoint.py` - durable successful-page-batch checkpoints used by retry/resume.
  - `liteparse_pdf_service.py` - LiteParse PDF extraction and asset merge path.
  - `pypdf_vlm_provider.py` - pypdf text-layer plus VLM page provider.
  - `paddle_ocr_pdf_provider.py` - PaddleOCR-VL provider.
  - `pdf_*_extract_service.py` - PDF image, figure, and layout extraction helpers.

PDF parsing accepts `--pages 1,3-5` and preserves the requested original-page mapping in `type_metrics.pdf.requested_pages`.
`--first-batch-pages N` defines the first consumable batch size; `timings_ms.first_batch` records the first provider result when the provider streams page results, otherwise it records the completed non-streaming batch boundary.
`--page-batch-size N` makes FileX invoke the selected PDF provider sequentially for bounded page batches, merge their Markdown and assets in requested-page order, and report each completed batch in diagnostics plus `work.batch_count`.
`--batch-resume-id ID` persists each successful batch under the mounted document workspace; a retry with the same ID and page plan skips completed batches and reports `work.resumed_batch_count`.
`filex status --batch-resume-id ID --include-results --after-batch N` returns newly completed batch Markdown with a stable stream id, cursor, batch index, original page range, terminal marker, and pending-asset signal while later batches continue in the same parse task.
Incremental batch Markdown is text-consumable immediately; embedded assets remain pending until the final merged artifact publishes stable remote references.
The bundled `filex.yaml` enables 10-page PDF batches by default; callers can override the batch size explicitly for controlled evaluations.
- `pdf_document_service.py` and other root-level `pdf_*` files are compatibility wrappers for older imports.
- `ppt_document_service.py` - PPT/PPTX pipeline with explicit `python_pptx`
  (default) and `liteparse` providers, ordered slide/table extraction, and shared
  embedded-image publishing.
- `word_document_service.py` - DOC/DOCX text, table, and embedded-image pipeline.
- `tabular_document_service.py` / `text_document_service.py` - simple Markdown conversion pipelines.
- `media_document_service.py` - audio/video Markdown pipeline.
- `media_transcription/` - pluggable media backends:
  - `local` for local `faster-whisper`.
  - `openai_compatible` for OpenAI-compatible Chat Completions endpoints.
  - `file_rate_limiter.py` for per-endpoint, model, and credential RPM slots shared by CLI processes in one container.

Image parsing through `openai_compatible` retries transient HTTP failures and honors numeric `Retry-After` values before applying exponential backoff.
Its standard Metrics report model call count, retry count, per-file peak concurrency, proactive rate-limit and retry wait time, and recognized character count.

## Provider selection baseline

The caller selects exactly one provider for each parse request.
An alternate provider in this table means that the caller may start a new explicit parse; FileX does not silently cascade through providers.

| Format or scenario | Default provider | Alternate provider | Selection and fallback boundary |
|---|---|---|---|
| Native or simple PDF | `liteparse` for scenario-aware routing | `paddle_ocr` | Upgrade only pages with weak text coverage, handwriting, formulas, or complex visual layout. |
| Scanned or visually complex PDF | `paddle_ocr` | `liteparse` | Apply page batching and model-call budgets before parsing; fall back when rate limits or page-level timeouts are reached. |
| PPT/PPTX | `python_pptx` | `liteparse` | Retry with LiteParse only when native text, table, or slide-order coverage is insufficient. |
| DOCX | `python_docx` | None | Return an explicit error for unsupported legacy DOC or unreadable packages. |
| XLSX | `openpyxl` | None | XLS must use `xlrd`; CSV must use `pandas`. |
| XLS | `xlrd` | None | Do not guess or silently switch to an XLSX provider. |
| CSV | `pandas` | None | Report encoding and delimiter failures explicitly. |
| Images | `image_vlm` | None | Auto-detect document, single-object, multi-object, chart, and general scenes. Multi-object images are cropped and parsed in batches; report authentication, rate-limit, and terminal model failures explicitly. |

Image requests keep a single public provider, `image_vlm`.
Its internal `media_parse_options.mode` defaults to `auto`.
Use `whole_image` to skip scene detection or `multi_object` to force crop-first parsing.
Optional `intent` and `target_fields` values guide the per-object result without exposing internal model choices.
Set `image_extraction_profile=pill_search`, or request imprint-related target fields, to produce Drugs.com-ready visual evidence instead of a free-form image description.
The pill profile persists every object crop under the parse task directory and writes a sibling `<source>.evidence.json` file.
Each evidence record keeps the stable object ID, crop reference, normalized imprint candidates, color, Drugs.com-compatible shape, visible dose form, query readiness, and the reason for manual review.
FileX does not guess medication names or call external drug databases; a domain workflow must resolve the evidence against Drugs.com and MedlinePlus.
Routing metrics include `image_scene_type`, `image_selected_pipeline`, object and batch counts, query-ready and review-required counts, evidence schema identity, and any fallback reason.

The ASAP Gateway currently keeps `paddle_ocr` as its static PDF default until scenario-aware routing is deployed.
Provider metrics must keep `provider`, `provider_version`, model identity, model call count, timings, cache state, and error type so selection conclusions remain replayable.
