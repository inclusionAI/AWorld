## ADDED Requirements

### Requirement: Model providers MUST treat native prompt cache as an optional enhancement

AWorld model providers MUST support ordinary request execution even when provider-native prompt cache lowering is unavailable, disabled, or unsupported.

#### Scenario: Provider does not support native prompt cache

- **WHEN** AWorld selects a provider path that does not support native prompt cache lowering
- **THEN** the provider MUST still be able to send the request by falling back to ordinary request assembly
- **AND** the lack of native prompt cache support MUST NOT block the normal request path

### Requirement: Provider-native lowering MUST consume provider-neutral assembly plans

AWorld model providers that implement native prompt cache lowering MUST consume the shared provider-neutral assembly plan rather than requiring context-layer code to emit provider-specific wire-format fields.

#### Scenario: Anthropic-native lowering is enabled

- **WHEN** the selected provider supports native prompt cache lowering and the feature is enabled by configuration
- **THEN** the provider MAY translate the shared prompt assembly plan into provider-specific request structure
- **AND** that translation MUST happen inside the provider adaptation layer instead of leaking provider-specific fields into the `amni` context layer

### Requirement: Tools MUST remain semantically stable while provider serialization stays provider-owned

AWorld model providers MUST preserve the assembly-layer decision that tools belong to a stable semantic section, while retaining ownership of provider-specific tool serialization.

#### Scenario: Prompt assembly includes a tools stability hint

- **WHEN** a prompt assembly plan identifies tools as part of the stable semantic section
- **THEN** the provider MUST preserve that stability meaning when deciding whether native prompt cache can be applied
- **AND** the provider MUST remain responsible for the final tool schema serialization required by its own wire format

### Requirement: Provider-native prompt cache MUST remain separately configurable from generic assembly

AWorld model providers MUST allow generic prompt assembly behavior to remain enabled even when provider-native cache lowering is disabled.

#### Scenario: Generic assembly is enabled but provider-native cache is disabled

- **WHEN** the request path enables generic prompt assembly but disables provider-native prompt cache lowering
- **THEN** AWorld MUST still use the provider-neutral prompt assembly behavior
- **AND** the provider MUST fall back to ordinary request payload generation without attempting native prompt cache markers
