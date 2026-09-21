# Local VLM and Docker acceptance evidence

This evidence records a bounded, non-publishable ParseBench smoke run. It proves
the integration contract; it is not a full leaderboard run and makes no Arca
claim.

## Result

- The real model was `gemini-3.1-pro-preview`, selected from
  `config/biz_config/default/asap_evaluator.yaml` without copying credentials
  into commands or evidence.
- A five-dimension smoke reduction completed with five numeric official scorer
  results, no missing result, no unscored result, and no execution or official
  scorer failure. Its diagnostic equal-weight score is
  `0.3010371995915193`.
- Positive evidence is present for text content (`0.86351933129093`), text
  formatting (`0.14166666666666666`), and layout (`0.5`). Chart and table are
  valid numeric zeroes for the selected examples, not transport or scorer
  failures.
- The strongest end-to-end run used `aworld-cli run --skill filex`, executed
  the generic FileX skill wrapper, made 15 real VLM calls with zero retries and
  timeouts, wrote the v1 artifact bundle, and received the two positive text
  scores above from the pinned official scorer.
- A second CLI-driven chart run made six real VLM calls and was officially
  scored. Its zero is retained as a quality result, not used as the sole proof
  that the integration works.
- The generated five-task ZIP was accepted by the unmodified
  `lingguang-bench-client` validator and the unmodified `mcpgateway` executable
  dataset parser.
- Docker sandbox validation is independent: three real Docker integration tests
  passed, and a task source copied into an `alpine:3.20` container had the same
  SHA-256 before and after AWorld `DockerSandbox` execution while an artifact
  was recovered successfully.

The machine-readable record is in `acceptance.json`. Raw local artifacts remain
under `/private/tmp/parsebench-vlm-acceptance.GbJ7lQ`; their hashes are recorded
so accidental substitution is detectable. No API key, model endpoint, parsed
document content, or private ground truth is committed here.

## Acceptance gates

The result is accepted only when all of these are true:

1. FileX reports provider `paddle_ocr`, provider contract
   `paddleocr-vl-1.6`, cache status `bypass`, zero failed work units, and zero
   FileX errors.
2. Model identity is non-empty, VLM `call_count` is positive, and retry and
   timeout counts are zero.
3. `document.md`, `layout.json`, and `result.json` satisfy the generic FileX
   skill artifact contract and retain source/output hashes.
4. The pinned ParseBench scorer revision returns `scored`, not
   `execution_failed`, `not_scored`, or `missing`.
5. At least one CLI-driven task receives a strictly positive official score.
6. Docker execution is checked separately and does not stand in for model
   quality.

## Scope

This smoke package is deliberately marked non-publishable because it contains a
small deterministic selection and uses a mutable local Runtime image tag. A
full leaderboard claim still requires the official-full package and all tasks.
Arca execution is intentionally outside this local acceptance.
