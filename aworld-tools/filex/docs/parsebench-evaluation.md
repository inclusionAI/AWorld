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
`not_scored` case was not converted to zero. All five dimensions reached a
trusted terminal state with no unresolved execution failures. Nineteen
formatting cases remain `not_scored` and are excluded from score means.

## Results

| Dimension | Cases | Numeric | Not scored | FileX | Official PaddleOCR-VL-1.6 reference | Difference |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Tables | 503 | 503 | 0 | 67.6422% | 67.77% | -0.1278 pp |
| Charts | 568 | 568 | 0 | 56.1396% | 54.24% | +1.8996 pp |
| Content faithfulness | 506 | 506 | 0 | 82.8802% | 82.71% | +0.1702 pp |
| Semantic formatting | 476 | 457 | 19 | 48.4013% | 54.64% | -6.2387 pp |
| Visual grounding / layout | 500 | 500 | 0 | 71.6970% | 77.80% | -6.1030 pp |
| Equal-weight overall | 2,553 | 2,534 | 19 | **65.3520%** | **67.43%** | -2.0780 pp |

The official reference values are the ParseBench published PaddleOCR-VL-1.6
Full Pipeline results. FileX is close to the reference on tables and content,
exceeds it on this chart run, and trails on formatting and visual grounding.
The overall value is an equal-weight mean of the five dimension means, matching
the published leaderboard aggregation. A case-weighted mean over the 2,534
numeric results is 65.4367%; it answers a different question and should not be
compared directly with the 67.43% leaderboard overall.

The previously reported 5.2995% layout value is retired: it combined 458
legacy hard-coded contract zeros with 42 newly scorable Document IR cases. It
measured a migration state, not the current parser. On the same fixed 20 cases,
Document IR v3 improved the official mean from 62.3079% to 76.7097% (+14.4018
percentage points): 11 cases improved, 9 were unchanged, and none regressed.
The two worst cases moved from 0% to 100% and from 25% to 75%.

The clean 500-case layout result also corrects an evaluation-adapter issue in
64 PDFs containing both `layout` annotations and legacy `order` rows. The
upstream JSONL loader otherwise constructs a parse test case instead of a
layout-detection test case. Only `layout` rows are passed into the official
layout evaluator; reading order remains scored by that evaluator from each
annotation's `ro_index`. Those 64 cases all produced numeric scores, averaging
79.6376%, and the complete 500-case layout mean is 71.6970%.

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

1. **Visual grounding**: Document IR v3 now preserves Paddle detector boxes,
   confidence, page geometry, and parser text/order metadata. Remaining errors
   are concentrated in detector misses, class ambiguity, and content
   attribution for image-only controls.
2. **Semantic formatting**: headings, lists, emphasis, superscript/subscript,
   and reading-order boundaries still lose information in difficult pages.
3. **Not-scored formatting cases**: these require separate contract diagnosis;
   they are excluded from the mean and must not be presented as parser zeros.

## Recommended optimization path

1. Keep the fixed 20-case A/B as the release regression set and rerun the full
   500-case layout suite after detector or mapping changes.
2. Tune detector thresholds and label aliases against a separate development
   split, especially small pictures and page furniture, without changing the
   pinned official scorer.
3. Add a formatting state machine that reconciles OCR spans with the PDF text
   layer and preserves nested lists, heading levels, emphasis, and scripts.
4. Create regression sets per formatting rule and grounding object class.
5. Publish a release-level score only from one immutable FileX revision and
   complete campaign manifest.

## Reproducibility boundary

This is the newest trusted result for each component, not a single campaign
executed from one immutable FileX commit. The 500 layout cases use one repaired
FileX revision, while the five-dimension overall combines the newest trusted
campaign per dimension. It should be used as an engineering baseline and
optimization guide. A release claim should pin the FileX image digest, parser
configuration, hardware profile, data revision, scorer revision, and all five
campaign manifests.
