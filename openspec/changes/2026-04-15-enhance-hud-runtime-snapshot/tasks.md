## 1. Runtime HUD Snapshot

- [ ] 1.1 Add a runtime-owned HUD snapshot store for active CLI sessions.
- [ ] 1.2 Define stable snapshot buckets for session, task, activity, usage, workspace, notifications, VCS, and plugins.
- [ ] 1.3 Add partial-update helpers so executors can update only the buckets they own.

## 2. Executor Lifecycle Integration

- [ ] 2.1 Update local executor task-start flow to publish HUD task/session state.
- [ ] 2.2 Update streaming progress flow to publish model, tool, token, and context updates.
- [ ] 2.3 Update task-finish and failure flows to publish idle or completed HUD state without losing the last useful snapshot.

## 3. CLI And HUD Rendering

- [ ] 3.1 Update HUD context assembly to merge the runtime snapshot with base toolbar context.
- [ ] 3.2 Update `aworld-hud` to render a two-line layered toolbar using grouped segments.
- [ ] 3.3 Add width-aware grouped-segment reduction so narrow terminals preserve core identity, task, and context information first.

## 4. Validation

- [ ] 4.1 Add tests for runtime snapshot updates and merge behavior.
- [ ] 4.2 Add tests for two-line HUD rendering, duplicate suppression, and width reduction.
- [ ] 4.3 Add CLI integration tests for default enablement, lifecycle toggles, and provider-failure fallback.
