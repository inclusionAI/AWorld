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

### Requirement: Provider cache usage MUST be normalized into common AWorld token fields

AWorld model providers that expose native prompt cache usage MUST map those counters into the common AWorld usage schema rather than exposing only provider-specific field names.

#### Scenario: Anthropic response includes prompt cache usage

- **WHEN** an Anthropic request returns native cache usage counters
- **THEN** AWorld MUST normalize cache-read tokens into `cache_hit_tokens`
- **AND** it MUST normalize cache-creation tokens into `cache_write_tokens`
- **AND** the common usage payload MUST remain usable alongside `prompt_tokens`, `completion_tokens`, and `total_tokens`

### Requirement: Task-level observability MUST surface normalized cache token usage

When common usage contains normalized cache token fields, task-level observability outputs MUST surface those fields together with ordinary token usage.

#### Scenario: Task finishes after a cache-aware provider call

- **WHEN** a task completes and the accumulated token usage includes `cache_hit_tokens` or `cache_write_tokens`
- **THEN** task-finished logging and equivalent task-completion payloads MUST include those normalized fields
- **AND** operators MUST NOT need to inspect raw provider responses to observe prompt cache hit or write volume

### Requirement: Prompt-level observability MUST surface cache path and normalized cache usage

When a request goes through prompt logging, AWorld MUST surface the prompt-cache path and any normalized cache usage that is available for that request.

#### Scenario: Prompt logger records a cache-aware request

- **WHEN** `prompt_logger.log` records a request that used cache-aware assembly
- **THEN** it MUST identify whether the request used cache-aware assembly only or also used provider-native cache lowering
- **AND** it MUST include normalized `cache_hit_tokens` and `cache_write_tokens` when those counters are available for that request
- **AND** operators MUST be able to distinguish cache-path behavior from ordinary prompt logging without reading raw provider payloads
