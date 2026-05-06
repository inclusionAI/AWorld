## 1. Configuration

- [ ] 1.1 Add `ContextCacheConfig` to `aworld/core/context/amni/config.py` and wire it into `AgentContextConfig`.
- [ ] 1.2 Add matching `context_cache` configuration to `aworld/config/conf.py::ModelConfig`.
- [ ] 1.3 Add tests covering default-enabled behavior and explicit opt-out on both agent and model configs.

## 2. Prompt Assembly

- [ ] 2.1 Create `aworld/core/context/amni/prompt/assembly/` with plan, state, and hashing modules.
- [ ] 2.2 Implement a provider-neutral `PromptAssemblyPlan` and `PromptSection` model.
- [ ] 2.3 Implement stable/dynamic section classification for phase-1 content sources.
- [ ] 2.4 Implement hash-based stable prefix reuse in request-time runtime state only.
- [ ] 2.5 Keep stable prefix out of history/memory persistence paths.
- [ ] 2.6 Add unit tests for section classification, hash stability, and runtime-state reuse.

## 3. AMNI Integration

- [ ] 3.1 Update `system_prompt_augment_op` to build assembly inputs without rewriting existing neuron production logic.
- [ ] 3.2 Preserve the default fallback path that folds the assembly plan back into ordinary request payloads.
- [ ] 3.3 Add regression tests proving AWORLD.md, relevant memory, and system prompt augmentation semantics remain intact.

## 4. Provider Capability and Lowering

- [ ] 4.1 Add provider-native prompt cache capability declarations under `aworld/models/`.
- [ ] 4.2 Add a default lowerer that converts `PromptAssemblyPlan` into ordinary provider request payloads.
- [ ] 4.3 Add an Anthropic lowerer that emits native prompt-cache-aware request structure when enabled.
- [ ] 4.4 Ensure provider-native lowering is skipped whenever agent/model config disables the feature.
- [ ] 4.5 Add unit tests for fallback behavior and Anthropic native lowering behavior.

## 5. Validation

- [ ] 5.1 Run targeted context assembly tests.
- [ ] 5.2 Run targeted provider lowering tests.
- [ ] 5.3 Run relevant regression tests for existing `amni` prompt augmentation behavior.
- [ ] 5.4 Validate the OpenSpec change with `openspec validate 2026-05-06-aworld-context-cache-assembly`.
