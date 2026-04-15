## 1. Runtime HUD Snapshot

- [x] 1.1 Add a runtime-owned HUD snapshot store for active CLI sessions.
- [x] 1.2 Define stable snapshot buckets for session, task, activity, usage, workspace, notifications, VCS, and plugins.
- [x] 1.3 Add partial-update helpers so executors can update only the buckets they own.

## 2. Executor Lifecycle Integration

- [x] 2.1 Update local executor task-start flow to publish HUD task/session state.
- [x] 2.2 Update streaming progress flow to publish model, tool, token, and context updates.
- [x] 2.3 Update task-finish and failure flows to publish idle or completed HUD state without losing the last useful snapshot.

## 3. CLI And HUD Rendering

- [x] 3.1 Update HUD context assembly to merge the runtime snapshot with base toolbar context.
- [x] 3.2 Update `aworld-hud` to render a two-line layered toolbar using grouped segments.
- [x] 3.3 Add width-aware grouped-segment reduction so narrow terminals preserve core identity, task, and context information first.

## 4. Validation

- [x] 4.1 Add tests for runtime snapshot updates and merge behavior.
- [x] 4.2 Add tests for two-line HUD rendering, duplicate suppression, width reduction, and accepted HUD segment formatting.
- [x] 4.3 Add CLI integration tests for default enablement, lifecycle toggles, provider-failure fallback, and interactive stats fallback when HUD is disabled or unavailable.
