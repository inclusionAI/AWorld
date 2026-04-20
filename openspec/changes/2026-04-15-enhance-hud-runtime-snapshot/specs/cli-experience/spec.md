## ADDED Requirements

### Requirement: CLI supports layered HUD presentation for runtime state
The AWorld CLI SHALL support a layered bottom toolbar so session identity and live execution state can be presented together without flattening every HUD field into one line.

#### Scenario: Rendering the default built-in HUD plugin
- **WHEN** the built-in `aworld-hud` plugin is active
- **THEN** the CLI can render a two-line bottom toolbar with a session-oriented line and an activity-oriented line
- **AND** the layout remains owned by the CLI rather than by direct provider markup

### Requirement: CLI bounds HUD density through grouped segments
The AWorld CLI SHALL bound visible HUD density through grouped segments rather than exposing every available HUD field as an independent visible column.

#### Scenario: Rich runtime telemetry is available
- **WHEN** the runtime provides task, tool, token, context, VCS, plugin, and session metadata at the same time
- **THEN** the CLI groups that information into a bounded number of visible HUD segments
- **AND** the toolbar remains readable without expanding into an unbounded set of columns

### Requirement: CLI reduces HUD detail by priority as width shrinks
The AWorld CLI SHALL reduce HUD detail by priority when terminal width is constrained so the most important session and execution signals remain visible.

#### Scenario: Rendering on a medium-width terminal
- **WHEN** toolbar space is reduced but still supports a layered HUD
- **THEN** the CLI keeps the two-line layout
- **AND** it hides or compresses lower-priority segments before removing core session, task, and context information

#### Scenario: Rendering on a narrow terminal
- **WHEN** toolbar space is too narrow to render the full layered HUD
- **THEN** the CLI preserves core session identity on the first line
- **AND** the second line prefers task and context over lower-priority plugin or detailed activity segments
