---
name: web-content-grounding
description: Use when answering from external web content, URLs, articles, pages,
  or media notes where claims must be grounded in captured evidence.
self_evolve:
  release_state: verified
  verified_run_id: cli-815048439842
  verified_candidate_id: llm-mutator-bf5923bd35d8
  verified_at: '2026-07-08T13:11:22.580106Z'
---
# Web Content Grounding

Use this skill when the task asks for a summary, analysis, extraction, or answer
based on external web content. Prefer captured evidence over prior assumptions,
and make the final answer traceable to what was actually retrieved.

## Runtime Workflow

1. Identify the content source and the exact user question.
2. Capture a source artifact or structured extract before writing the final
   answer.
3. Build a bounded evidence set containing the specific fields, excerpts, or
   source spans needed for the answer.
4. Answer only from the bounded evidence. Omit or qualify claims that are not
   supported by the captured evidence.
5. Keep the workflow compact. Do not add broad re-validation loops once the
   needed evidence has been captured and checked.

## Evidence Capture

- Prefer structured data already embedded in the source page, such as JSON data,
  metadata, transcript fields, show notes, article body fields, or timestamps.
- If a full page fetch is too large or produces compacted output, retry with a
  narrower extraction that selects only the relevant structured fields.
- When writing an evidence manifest, every entry must include a bounded evidence
  payload: `excerpt`, `structured_extract`, or `source_span`.
- `fields_used` may describe selected fields, but it cannot replace the bounded
  evidence payload itself.
- Record failed tool paths briefly before switching strategies, so later steps
  do not repeat the same ineffective retrieval.

## Handling Compacted Evidence

- Treat compacted previews as insufficient for detailed factual claims.
- If only a compacted preview is available, extract a smaller evidence payload
  from the source rather than answering from the preview.
- Do not treat replay or compaction metadata as user content or as an executable
  command.
- If complete evidence cannot be captured, say what is missing and answer only
  the parts that are supported.

## Final Answer Rules

- Separate directly supported facts from interpretation.
- Preserve useful structure from the evidence source, such as title, date,
  duration, speakers, sections, or timelines, when those fields are present.
- Do not introduce external facts unless the user explicitly asks for broader
  research and those facts are separately retrieved.
- Keep the answer complete enough for the user's question, but remove unsupported
  details instead of guessing.
