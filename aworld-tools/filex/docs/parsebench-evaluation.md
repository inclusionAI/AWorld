# FileX ParseBench Evaluation

## Scope

This report records the latest validated FileX component baseline against the
official ParseBench data and scorer. It evaluates parsing quality, not agent
planning, LLM quality, or downstream task completion.

- Cases: 2,553
- Data revision: `2805a1d940f95a203e0ae4b88be9934f7765b3fc`
- Scorer revision: `34b73455032797754f6ed62e14c27a8b5423d11e`
- Project: [ParseBench repository](https://github.com/run-llama/ParseBench) and
  [official leaderboard](https://www.parsebench.ai/)
- Runtime: FileX native parser; no agent or LLM in the execution path
- Scheduling: one active parse at a time, bounded ten-case evaluation batches
- Aggregation: official score mean within each dimension, then an equal-weight
  mean across the five dimensions

System/control-plane errors were tracked separately from parser scores. A
`not_scored` case was not converted to zero. The final baseline contains 2,534
numeric scores, 19 `not_scored` formatting cases, and no unresolved execution
failures.

## Results

| Dimension | Cases | Numeric | Not scored | FileX | Official PaddleOCR-VL-1.6 reference | Difference |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Tables | 503 | 503 | 0 | 67.6422% | 67.77% | -0.1278 pp |
| Charts | 568 | 568 | 0 | 56.1396% | 54.24% | +1.8996 pp |
| Content faithfulness | 506 | 506 | 0 | 82.8802% | 82.71% | +0.1702 pp |
| Semantic formatting | 476 | 457 | 19 | 48.4013% | 54.64% | -6.2387 pp |
| Visual grounding / layout | 500 | 500 | 0 | 5.2995% | 77.80% | -72.5005 pp |
| Equal-weight overall | 2,553 | 2,534 | 19 | **52.0726%** | **67.43%** | **-15.3574 pp** |

The official reference values are the ParseBench published
PaddleOCR-VL-1.6 Full Pipeline results. FileX is close to the reference on
tables and content, exceeds it on this chart run, trails on formatting, and
does not yet satisfy the benchmark's grounding contract.

## Interpretation

### What is working

- Text-layer extraction and conservative text repair preserve content well.
- Table parsing is effectively at the upstream reference level.
- The chart pipeline now emits structured, scorer-compatible content instead
  of treating charts as plain OCR text.

## Operational observations outside ParseBench scoring

Separate service validation confirmed persistent model loading, bounded
concurrency, batch checkpoints, and direct source URL ingestion. These behaviors
improve long-document execution and transfer efficiency, but ParseBench quality
scores do not measure service reliability or throughput.

### Main gaps

1. **Visual grounding**: FileX does not yet emit reliable element bounding
   boxes in the coordinate system required by ParseBench. A correct fix needs
   page geometry, element identity, coordinate normalization, and confidence;
   guessing boxes would inflate neither correctness nor usefulness.
2. **Semantic formatting**: headings, lists, emphasis, superscript/subscript,
   and reading-order boundaries still lose information in difficult pages.
3. **Not-scored formatting cases**: these require separate contract diagnosis;
   they are excluded from the mean and must not be presented as parser zeros.

## Recommended optimization path

1. Extend Document IR v2 with page-relative and pixel-space bounding boxes,
   source image dimensions, rotation, and stable element IDs.
2. Carry Paddle layout detections through normalization instead of rebuilding
   geometry from Markdown.
3. Add a formatting state machine that reconciles OCR spans with the PDF text
   layer and preserves nested lists, heading levels, emphasis, and scripts.
4. Create regression sets per formatting rule and grounding object class before
   another full-suite run.
5. Re-run only the affected dimensions with pinned data/scorer revisions, then
   publish a release-level score from one immutable FileX revision.

## Reproducibility boundary

This is the newest trusted result for each component, not a single campaign
executed from one immutable FileX commit. It should be used as an engineering
baseline and optimization guide. A release claim should pin the FileX image
digest, parser configuration, hardware profile, data revision, scorer revision,
and all five campaign manifests.
