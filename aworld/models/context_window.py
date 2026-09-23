"""Model context capacity resolution without provider calls or name guessing.

Explicit limits are caller/deployment declarations. Registry matches are model
registrations, not live verification of a custom deployment. Unknown aliases
retain an operational fallback with an explicit source label.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_CONTEXT_WINDOW_TOKENS = 1_000_000
# Existing model registrations are retained, with exact matching. GPT-4o/turbo
# use decimal 128000; GPT-4.1 uses the provider's documented 1,047,576 tokens:
# https://developers.openai.com/api/docs/models/gpt-4.1
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # OpenAI models
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8 * 1024,
    "gpt-3.5-turbo": 16 * 1024,

    # Verified GPT-4.1 capacity; preserve decimal token count from provider docs.
    "gpt-4.1": 1_047_576,

    # Anthropic models
    "claude-sonnet-4": 200 * 1024,
    "claude-3.7-sonnet": 200 * 1024,
    "claude-opus-4.1": 200 * 1024,
    "claude-3.5-haiku": 200 * 1024,
    "claude-3.5-sonnet": 200 * 1024,
    "claude-opus-4": 200 * 1024,
    "claude-3-haiku": 200 * 1024,
    "claude-3-opus": 200 * 1024,
    "claude-3-sonnet": 200 * 1024,
    "claude-2": 100 * 1024,
    "claude-instant": 100 * 1024,

    # Google models
    "gemini-pro": 32 * 1024,
    "gemini-2.5-flash": 1024 * 1024,
    "gemini-2.5-pro": 1024 * 1024,
    "gemini-2.5-flash-lite": 1024 * 1024,
    "gemini-2.5-flash-lite-preview": 1024 * 1024,

    # Meta models
    "llama-2": 4 * 1024,
    "llama-3": 8 * 1024,
    "codellama": 16 * 1024,

    # Mistral models
    "mistral": 8 * 1024,
    "mixtral": 32 * 1024,

    # BAILING models (Ant Group)
    "ling-max-1.5-0527": 128 * 1024,

    # QWEN models (Alibaba Cloud) - Additional models
    "qwen2.5-1.5b-instruct": 32 * 1024,
    "qwen2.5-vl-3b-instruct": 32 * 1024,
    "qwen3-235b-a22b-instruct-2507": 256 * 1024,

    # KIMI models
    "kimi-k2-instruct": 128 * 1024,
    "kimi-k2-instruct-0905": 256 * 1024,

    # DEEPSEEK models - Additional models
    "deepseek-r1-0528": 64 * 1024,
    "deepseek-v3.1": 128 * 1024,

    # BYTEDANCE models
    "seed-oss-36b-instruct": 128 * 1024,

    # ZHIPUAI models - Additional models
    "glm-4.5": 128 * 1024,
    "glm-4.6": 128 * 1024,
    "glm-4.5v": 64 * 1024,

    # OpenAI Open Source models
    "gpt-oss-120b": 128 * 1024,

    # Default fallback
    "default": DEFAULT_CONTEXT_WINDOW_TOKENS
}

# Explicit model identity aliases, not substring/family matching.
MODEL_CONTEXT_ALIASES = {
    "claude-3-7-sonnet-latest": "claude-3.7-sonnet",
    "claude-3-5-sonnet-latest": "claude-3.5-sonnet",
    "claude-3-5-haiku-latest": "claude-3.5-haiku",
}
_MODEL_NAMESPACES = {
    "openai": ("gpt-", "o1", "o3", "o4"),
    "anthropic": ("claude-",),
    "google": ("gemini-",),
    "meta-llama": ("llama-", "codellama"),
    "qwen": ("qwen",),
    "moonshotai": ("kimi-",),
    "deepseek-ai": ("deepseek-",),
    "zai-org": ("glm-",),
}


@dataclass(frozen=True)
class ContextWindowResolution:
    tokens: int
    source: str
    model_name: str | None
    matched_model: str | None = None

    @property
    def is_fallback(self) -> bool:
        return self.source == "fallback"


def _positive_tokens(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _model_key(model_name: str | None) -> str:
    if model_name is None:
        return ""
    if not isinstance(model_name, str):
        raise TypeError("model_name must be text or None")
    return model_name.strip().casefold()


def register_model_context_window(model_name: str, tokens: int) -> None:
    """Register an exact deployment/model ID; no implicit family aliases."""
    key = _model_key(model_name)
    if not key:
        raise ValueError("model_name must be non-empty")
    MODEL_CONTEXT_WINDOWS[key] = _positive_tokens(tokens, "context window")


def resolve_model_context_window(
    model_name: str | None,
    *,
    context_limit: int | None = None,
    max_model_len: int | None = None,
) -> ContextWindowResolution:
    """Resolve explicit compiler > explicit deployment/model > registry > fallback.

    max_model_len also carries reliable deployment metadata supplied by callers.
    A tokenizer vocabulary and a guessed model-name substring are never capacity
    evidence. Explicit settings do not mutate global model registrations.
    """
    key = _model_key(model_name)
    if context_limit is not None:
        return ContextWindowResolution(_positive_tokens(context_limit, "context_limit"),
                                       "explicit_context_limit", model_name)
    if max_model_len is not None:
        return ContextWindowResolution(_positive_tokens(max_model_len, "max_model_len"),
                                       "explicit_max_model_len", model_name)
    candidates = [key]
    namespace, separator, bare_name = key.partition("/")
    if separator and bare_name.startswith(_MODEL_NAMESPACES.get(namespace, ())):
        candidates.append(bare_name)
    for candidate in candidates:
        canonical = candidate if candidate in MODEL_CONTEXT_WINDOWS else MODEL_CONTEXT_ALIASES.get(candidate, candidate)
        if canonical and canonical != "default" and canonical in MODEL_CONTEXT_WINDOWS:
            return ContextWindowResolution(_positive_tokens(MODEL_CONTEXT_WINDOWS[canonical], "registered context window"),
                                           "model_registry", model_name, canonical)
    return ContextWindowResolution(
        _positive_tokens(MODEL_CONTEXT_WINDOWS.get("default", DEFAULT_CONTEXT_WINDOW_TOKENS), "fallback context window"),
        "fallback", model_name,
    )
