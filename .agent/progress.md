# Project Progress

## Final Status

**Phase:** implementation complete; local bounded acceptance complete.

The final architecture is:

`ParseBench checkout -> lingguang-bench-client -> standalone yolo-dataset-package/v2 ZIP -> mcpgateway upload/publish/batch -> Arca Runtime AWorld harness -> aworld-cli run + FileX skill -> generic FileX artifacts -> Dataset-owned verifier + official scorer -> generic Gateway result`.

No ParseBench-specific command, dataset loader, gateway client, verifier, or scorer remains in AWorld CLI. Local validation intentionally used fixtures and a four-task package rather than a full 2,078-execution campaign.

## Branches and Revisions

| Repository | Branch | Implementation commit | Worktree |
| --- | --- | --- | --- |
| AWorld | `codex/filex-parsebench-benchmark` | `6296fe98` | `/private/tmp/aworld-filex-parsebench` |
| lingguang-bench-client | `codex/filex-parsebench-benchmark` | `e212a2b0` | `/private/tmp/lingguang-bench-client-filex-parsebench` |
| mcpgateway | `codex/filex-parsebench-benchmark` | `4c8cfcaf` | `/private/tmp/mcpgateway-filex-parsebench` |
| lingguang-bench-runtime | `codex/filex-parsebench-benchmark` | `be8330e` | `/private/tmp/runtime-filex-parsebench` |

## Delivered Boundaries

### lingguang-bench-client: Dataset owner

- Pins ParseBench dataset revision `2805a1d940f95a203e0ae4b88be9934f7765b3fc` and scorer revision `34b73455032797754f6ed62e14c27a8b5423d11e`.
- Validates source material, groups rules into deterministic executions, supports bounded smoke selection, and emits a deterministic standalone `yolo-dataset-package/v2` ZIP.
- Provides `lingbench-parsebench` for package creation and generic `lingbench upload-package` for uploading and publishing the exact ZIP bytes.
- Embeds private ground truth and a self-contained verifier in each task archive; verifier execution has no AWorld import or AWorld benchmark command.
- Consumes `filex.skill.parse-result/v1`, validates source/output hashes, normalizes raw FileX Document IR, and invokes the pinned official scorer.

### AWorld and FileX: generic agent/skill capability

- Retains reusable FileX parsing, chart recognition, provider/model evidence, independent `filex` CLI, and the first-party FileX skill.
- Adds generic `aworld-cli run --trajectory-output ... --trajectory-format atif`; it contains no ParseBench semantics.
- FileX skill supports `--artifacts-dir` and atomically commits `document.md`, raw `layout.json` Document IR, and `result.json` with source/output hashes and the original FileX response.
- The artifact schema is generic (`filex.skill.parse-result/v1`); benchmark normalization remains in the Dataset verifier.

### mcpgateway: Dataset lifecycle and scheduling

- Validates/imports executable Dataset ZIPs, uploads/publishes them, binds immutable package identity to plans/results, and dispatches generic Harbor/Arca batches.
- Supports protected model profiles, idempotent lifecycle operations, artifact projection, and distinct zero/not-scored/failure states.
- Contains no ParseBench scorer or FileX output-normalization implementation.

### Runtime/Arca: execution adapter

- The AWorld Harbor harness runs generic `aworld-cli run --skill filex`, injects the FileX skill path, model proxy environment, ATIF trajectory output, workspace root, and `/logs/artifacts` root.
- Runtime images bundle pinned AWorld, aworld-cli, and FileX wheels plus the FileX skill and required Paddle/OpenCV assets.
- The harness does not detect ParseBench tasks and does not import Dataset/verifier code.

## Local Verification

- AWorld FileX wrapper: `7 passed`; generic CLI/skill selection: `83 passed` with one unrelated pre-existing serve-dispatch expectation failure when that neighboring suite is included.
- lingguang-bench-client complete suite: all `197` tests passed outside the filesystem sandbox so its localhost protocol fixtures could bind; Ruff passed for `src` and `tests`.
- Runtime focused wheel/Dockerfile/source-overlay/FileX health suite passed (`240` tests in the main selection); Ruff passed for changed files.
- mcpgateway focused Dataset/Harbor/Arca selection: `435 passed`; one stale disabled-environment API test expects a custom body for a path value now rejected earlier by the enum, and one separate legacy Harbor import test cannot collect against the installed Harbor version.
- Cross-repository package smoke: a four-task fixture ZIP was produced by lingguang-bench-client and accepted by mcpgateway's production parser with all four tasks plus `agent=aworld` and `required_skill=filex` preserved.
- Generic artifact smoke: FileX skill output was hash-validated and normalized by the Dataset verifier into `filex/paddle_ocr@1.6` scorer input.
- `git diff --check` passed in all four repositories.

## External Gates

- A live upload/publish was not attempted because no target mcpgateway endpoint/credential was supplied; the client upload protocol is covered by localhost tests.
- A live Arca container run was not attempted because Docker/Arca and the internal immutable model asset were not available locally. Static image contracts, wheel health, harness routing, and bounded artifact flow are covered.
- A full ParseBench campaign was deliberately not run. Production publication still requires an immutable Runtime image digest, configured model profile/secrets, reachable scorer asset, and Gateway/Arca deployment credentials.

## Review Conclusion

The earlier architecture that put ParseBench lifecycle commands in AWorld CLI was removed. Execution adaptation now belongs to Runtime; ParseBench task/scoring adaptation belongs to the standalone Dataset ZIP; AWorld/FileX remains generic. No unresolved critical or important cross-boundary issue remains in the implemented local scope.
