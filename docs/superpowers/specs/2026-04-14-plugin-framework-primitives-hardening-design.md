Title: Plugin Framework Primitives Hardening (Task 1)
Date: 2026-04-14
Status: Draft

## Summary
Harden the plugin framework manifest loader and models for Task 1 by adding explicit validation, canonicalizing the plugin root, and returning shallow-immutable structures to prevent casual mutation after validation.

## Goals
- Canonicalize `plugin_root` to an absolute resolved path in the returned manifest.
- Validate manifest structure and common invalid cases with clear `ValueError` messages.
- Make manifest data effectively immutable at the contract boundary (shallow immutability is sufficient).
- Add tests that lock down validation behavior and canonical root handling.

## Non-Goals
- No new plugin discovery, state, commands, hooks, or HUD behavior.
- No deep immutable container implementation beyond shallow protections.
- No changes outside the owned Task 1 files.

## Proposed Changes
### Manifest Loading
- Resolve `plugin_root` using `Path.resolve()` and store that string in `PluginManifest.plugin_root`.
- Validate required top-level fields: `id` and `version`.
- Validate `entrypoints` is a mapping when present; otherwise raise `ValueError` containing `entrypoints must be a mapping`.
- Validate each entrypoint item is a dict; otherwise raise `ValueError` containing `entrypoint must be an object`.
- Validate `metadata` and `permissions` are mappings when present; otherwise raise `ValueError` containing `metadata must be a mapping` or `permissions must be a mapping`.

### Immutability
- Convert `capabilities` to `frozenset`.
- Convert entrypoint lists into tuples.
- Wrap manifest `entrypoints` mapping in `types.MappingProxyType`.
- Wrap `metadata` and `permissions` in `types.MappingProxyType` on `PluginEntrypoint`.

## Data Flow
`load_plugin_manifest(Path)` → read `plugin.json` → validate → construct `PluginEntrypoint` objects → freeze collections → return `PluginManifest`.

## Error Handling
- Raise `ValueError` with short, semantic substrings for invalid shapes and missing required fields.
- Preserve existing duplicate entrypoint id check.

## Testing
- Extend manifest tests to assert:
  - Missing required fields (`id`, `version`) raise `ValueError` with `missing required`.
  - `entrypoints` not a mapping raises `ValueError` with `entrypoints must be a mapping`.
  - Non-dict entrypoint items raise `ValueError` with `entrypoint must be an object`.
  - Invalid `metadata`/`permissions` raise `ValueError` with `metadata must be a mapping` or `permissions must be a mapping`.
  - `plugin_root` stored as resolved absolute path.
  - Returned collections are shallow-immutable (tuples/mapping proxies).

## Risks
- Shallow immutability does not prevent deep mutation of nested objects inside metadata/permissions if they are mutable.

## Rollout
- Local tests only: `pytest tests/plugins/test_plugin_framework_manifest.py tests/plugins/test_plugin_framework_resources.py -v`.
