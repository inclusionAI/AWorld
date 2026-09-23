"""Read model selection and output limits of supported chat provider adapters.

This module neither constructs a provider nor invokes one. Model identifiers are
opaque strings: routing precedence is adapter behavior, not capability evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _unwrap_provider(provider: Any) -> Any:
    """Unwrap only the exact framework-owned forwarding adapter."""
    seen = set()
    while (
        type(provider).__module__ == "aworld.self_evolve.candidate_generation"
        and type(provider).__name__ == "_SanitizingProvider"
    ):
        # Only this wrapper forwards requests unchanged. Checking its identity
        # before importing also avoids loading self-evolve for ordinary calls.
        from aworld.self_evolve.candidate_generation import _SanitizingProvider

        if type(provider) is not _SanitizingProvider:
            break
        if id(provider) in seen:
            raise ValueError("cyclic framework provider wrapper")
        seen.add(id(provider))
        try:
            provider = object.__getattribute__(provider, "_delegate")
        except AttributeError as exc:
            raise ValueError("framework provider wrapper has no delegate") from exc
        if provider is None:
            raise ValueError("framework provider wrapper has no delegate")
    return provider


def _openai_request_params(provider: Any, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    params = dict(provider.kwargs.get("params", {}))
    params.update(kwargs)
    return params


def _openai_extra_body_overrides(
    provider: Any, params: Mapping[str, Any], fields: tuple[str, ...],
) -> dict[str, Any]:
    extra_body = params.get("extra_body")
    is_http = getattr(provider, "is_http_provider", None)
    if extra_body is None or is_http is True:
        return {}
    if not isinstance(extra_body, Mapping):
        raise ValueError("cannot resolve SDK extra_body without a mapping")
    overrides = {field: extra_body[field] for field in fields if field in extra_body}
    if overrides and is_http is not False:
        raise ValueError("cannot resolve extra_body overrides without transport identity")
    return overrides


def effective_request_model_name(
    provider: Any,
    request_kwargs: Mapping[str, Any] | None = None,
) -> str | None:
    """Return the adapter's effective model identifier without changing inputs.

    OpenAI/Azure merge configured ``params.model`` with per-call ``model``;
    that result overrides ``model_name`` only when non-None. The SDK then merges
    ``extra_body`` into the JSON body; the direct HTTP adapter sends that field
    nested instead. Anthropic supports the per-call ``model_name`` field. Ant
    and the reviewed custom transport retain their bound model. Other adapters
    expose only their bound identity because their arbitrary kwargs do not
    establish routing semantics.

    Provider imports are deferred to avoid adding a configuration/provider
    dependency cycle merely by importing this pure resolver module.
    """
    from aworld.models.anthropic_provider import AnthropicProvider
    from aworld.models.openai_provider import OpenAIProvider

    try:
        provider = _unwrap_provider(provider)
    except ValueError:
        return None
    kwargs = request_kwargs if request_kwargs is not None else {}
    bound_model = getattr(provider, "model_name", None)
    if isinstance(provider, OpenAIProvider):
        model = kwargs.get("model_name", bound_model or "")
        params = _openai_request_params(provider, kwargs)
        override = params.get("model")
        # get_openai_params omits None values from its final merge, so an
        # explicit model=None clears the configured override and exposes the
        # named/bound model again. Empty strings remain actual overrides.
        if override is not None:
            model = override
        try:
            model = _openai_extra_body_overrides(provider, params, ("model",)).get("model", model)
        except ValueError:
            return None
    elif isinstance(provider, AnthropicProvider):
        model = kwargs.get("model_name", bound_model or "")
    else:
        model = bound_model
    # Preserve every valid string exactly. Invalid wire values are not turned
    # into invented model IDs; validation remains with the caller/provider.
    return model if isinstance(model, str) else None


def effective_request_output_limits(
    provider: Any,
    *,
    max_tokens: Any = None,
    request_kwargs: Mapping[str, Any] | None = None,
) -> tuple[Any, ...]:
    """Read effective output caps; callers validate positive integer values.

    Pass the named max_tokens argument after applying the caller's own default.
    OpenAI/Azure overwrite params.max_tokens even when that argument is None,
    while max_completion_tokens follows the configured/per-call merge. SDK
    extra_body overrides either field, including explicit null; direct HTTP
    keeps the extra_body object nested. Unknown transport semantics for these
    overrides raise ValueError rather than silently reusing a smaller cap.

    Native Anthropic supplies 4096 when its named cap is falsey. Other existing
    chat adapters receive the named cap unchanged. None denotes an unset/null
    cap, not a verified provider maximum.
    """
    from aworld.models.anthropic_provider import AnthropicProvider
    from aworld.models.openai_provider import OpenAIProvider

    provider = _unwrap_provider(provider)
    if isinstance(provider, OpenAIProvider):
        kwargs = request_kwargs if request_kwargs is not None else {}
        params = _openai_request_params(provider, kwargs)
        limits = {"max_tokens": max_tokens, "max_completion_tokens": params.get("max_completion_tokens")}
        limits.update(_openai_extra_body_overrides(provider, params, tuple(limits)))
        return tuple(limits.values())
    if isinstance(provider, AnthropicProvider):
        return (max_tokens or 4096,)
    return (max_tokens,)


__all__ = ["effective_request_model_name", "effective_request_output_limits"]
