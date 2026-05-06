## 1. Configuration

- [x] 1.1 Add `ContextCacheConfig` to `aworld/core/context/amni/config.py` and wire it into `AgentContextConfig`.
- [x] 1.2 Add matching `context_cache` configuration to `aworld/config/conf.py::ModelConfig`.
- [x] 1.3 Add tests covering default-enabled behavior and explicit opt-out on both agent and model configs.

## 2. Prompt Assembly

- [ ] 2.1 Create `aworld/core/context/amni/prompt/assembly/` with plan, provider, state, and hashing modules.
- [x] 2.2 Implement a provider-neutral `PromptAssemblyPlan` and `PromptSection` model.
- [ ] 2.3 Implement `PromptAssemblyProvider` plus `DefaultPromptAssemblyProvider` and `CacheAwarePromptAssemblyProvider`.
- [ ] 2.4 Implement stable/dynamic section classification for phase-1 content sources inside the cache-aware provider.
- [ ] 2.5 Implement hash-based stable prefix reuse in request-time runtime state only.
- [ ] 2.6 Keep stable prefix out of history/memory persistence paths.
- [ ] 2.7 Add unit tests for provider selection, section classification, hash stability, and runtime-state reuse.

## 3. AMNI Integration

- [ ] 3.1 Update `system_prompt_augment_op` to call the injected `PromptAssemblyProvider` without rewriting existing neuron production logic.
- [ ] 3.2 Preserve the default fallback path that keeps default provider behavior close to today's ordinary request payload assembly.
- [ ] 3.3 Add regression tests proving AWORLD.md, relevant memory, and system prompt augmentation semantics remain intact.
- [ ] 3.4 Ensure the implementation does not introduce a parallel `AmniContext` or replacement context backend path.

## 4. Provider Capability and Lowering

- [ ] 4.1 Add provider-native prompt cache capability declarations under `aworld/models/`.
- [ ] 4.2 Add a default lowerer that converts `PromptAssemblyPlan` into ordinary provider request payloads.
- [ ] 4.3 Add an Anthropic lowerer that emits native prompt-cache-aware request structure when enabled.
- [ ] 4.4 Ensure provider-native lowering is skipped whenever agent/model config disables the feature.
- [x] 4.5 Normalize provider cache usage into common `cache_hit_tokens` and `cache_write_tokens` fields.
- [ ] 4.6 Add unit tests for fallback behavior, Anthropic native lowering behavior, and cache usage normalization.

## 5. Logging and Observability

- [x] 5.1 Update task-finished logging and related payload assembly to include normalized cache token usage when present.
- [x] 5.2 Add regression coverage for task-finished usage output with cache hit/write token fields.
- [x] 5.3 Extend `prompt_logger.log` output with prompt-level cache observability, including assembly/lowering path and normalized cache token usage when available.
- [x] 5.4 Add regression coverage for prompt logger cache observability output.
- [x] 5.5 Replace single-value `context_info["llm_input"]` / `llm_output` / `llm_call_start_time` as the primary truth source with append-only `llm_calls` records, while preserving latest-call aliases for backward compatibility.
- [x] 5.6 Update trajectory generation to prefer `llm_calls[*].request.messages` as the real prompt snapshot for each LLM call and keep cache token usage out of trajectory output.
- [x] 5.7 Add regression coverage for multi-call same-message snapshot preservation and trajectory prompt-snapshot fidelity.

## 6. Validation

- [x] 6.1 Run targeted context assembly tests.
- [ ] 6.2 Run targeted provider lowering tests.
- [x] 6.3 Run relevant regression tests for existing `amni` prompt augmentation behavior.
- [x] 6.4 Validate the OpenSpec change with `openspec validate 2026-05-06-aworld-context-cache-assembly`.
