# Repository Instructions

This repository uses OpenSpec for change management.

## Working Model

- Check `openspec/specs/` before changing behavior, interfaces, or contributor workflows.
- Create or update a change under `openspec/changes/` before landing work that changes repository behavior.
- Treat `docs/superpowers/` as historical material only. Do not add new active change proposals or plans there.

## Workspace Focus

- This workspace is for the AWorld framework and harness layer.
- Prefer framework changes in `aworld/core/agent/`, then related infrastructure in `aworld/core/tool/`, `aworld/core/context/`, `aworld/runners/`, `aworld/events/`, and `aworld/memory/`.
- Preserve backward compatibility for single-agent flows when touching multi-agent orchestration.

## Validation Expectations

- For runtime, orchestration, or harness changes, benchmark validation is the primary bar.
- Use GAIA for information-retrieval and reasoning regressions: `cd examples/gaia && python run.py --split validation --start 0 --end 50`
- Use XBench for multi-agent search and reasoning regressions: `cd examples/xbench && python eval.py`
- Use targeted `pytest` coverage for local correctness and regression locking.

## Browser Routing

- Use `agent-browser` first for browser automation tasks in this repository.
- Do not default to `browser-use` here unless explicitly requested.

## Git And Review Hygiene

- Never push, open a PR, or run other remote git operations without explicit user instruction.
- Review `git status` before staging and avoid `git add .`.
- Do not commit temporary process artifacts such as `Claude-Sessions/`, root-level ad hoc test files, `*_FINAL*.md`, `*_COMPLETE*.md`, `*_FIX*.md`, or `*__tmp_action.py`.

## Runtime Notes

- Tools currently execute locally through the sandbox abstraction layer in the existing Python environment.
- Key contributor-facing environment variables include `LOCAL_AGENTS_DIR`, `REMOTE_AGENTS_BACKEND`, `SKILLS_PATH`, and `AWORLD_DISABLE_CONSOLE_LOG`.

## Primary Sources

- Stable capability contracts: `openspec/specs/`
- Proposed or in-flight changes: `openspec/changes/`
- User and product overview: `README.md`
- Tutorials and deep reference material: `docs/`
