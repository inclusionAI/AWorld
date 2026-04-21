## ADDED Requirements

### Requirement: Gateway external naming uses the hyphenated form
The gateway subsystem SHALL use `aworld-gateway` as its external display name in user-facing CLI and HTTP metadata.

#### Scenario: Operator inspects gateway CLI or HTTP metadata
- **WHEN** the operator reads gateway-facing descriptions, titles, or naming constants intended for display
- **THEN** the visible component name is `aworld-gateway`

### Requirement: Gateway Python import naming remains underscore-based
The gateway subsystem SHALL preserve `aworld_gateway` as the Python import package name while documenting that distinction explicitly.

#### Scenario: Developer imports the gateway package
- **WHEN** Python code imports the gateway package
- **THEN** the import path remains `aworld_gateway`
- **AND** the package exposes explicit metadata that distinguishes the import name from the external display name
