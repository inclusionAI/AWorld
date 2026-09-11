# Project Standards

## Correctness and Architecture

- Core Context and cache models remain provider-neutral.
- Provider-native fields exist only in provider adapters and captured raw usage.
- Final candidate requests are immutable; no transform is allowed after compile.
- Missing, invalid, or conflicting cache data is never coerced into an exact zero.
- Trust, scope, permission, tool atomicity, and required Context invariants take
  precedence over cache reuse.
- Cache hit ratio is diagnostic; uncached/cache-adjusted cost per successful task
  is the optimization target.

## Code Quality

- Use typed immutable records for cross-layer receipts.
- Names reveal whether a value is provider truth, derived, bounded, or heuristic.
- Error paths are explicit and fail closed for claims, not for task execution.
- Avoid new dependencies unless the existing standard library and project models
  cannot express the requirement.

## Testing

- Write a failing behavioral test before implementation.
- Cover explicit zero separately from missing data.
- Cover sync, async, and stream paths where provider behavior differs.
- Capture/replay tests verify the actual provider-bound request, not only logical
  hashes.
- Live provider failures are infrastructure-invalid, never benchmark reward zero.
- No task-specific benchmark behavior is permitted.

## Git

- Work only on `codex/cache-economics-release`.
- Preserve unrelated untracked user files.
- Each commit is a coherent milestone or review fix with passing tests.
- Do not push until explicitly requested by the user.
