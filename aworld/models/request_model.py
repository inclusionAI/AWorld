"""Read the target model selected by the supported chat provider adapters.

This module neither constructs a provider nor invokes one. Model identifiers are
opaque strings: routing precedence is adapter behavior, not capability evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


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
    expose only their bound identity
    here because their arbitrary kwargs do not establish routing semantics.

    Provider imports are deferred to avoid adding a configuration/provider
    dependency cycle merely by importing this pure resolver module.
    """
    from aworld.models.anthropic_provider import AnthropicProvider
    from aworld.models.openai_provider import OpenAIProvider

    kwargs = request_kwargs if request_kwargs is not None else {}
    bound_model = getattr(provider, "model_name", None)
    if isinstance(provider, OpenAIProvider):
        model = kwargs.get("model_name", bound_model or "")
        params = dict(provider.kwargs.get("params", {}))
        override = kwargs.get("model", params.get("model"))
        # get_openai_params omits None values from its final merge, so an
        # explicit model=None clears the configured override and exposes the
        # named/bound model again. Empty strings remain actual overrides.
        if override is not None:
            model = override
        extra_body = kwargs.get("extra_body", params.get("extra_body"))
        if extra_body is not None:
            is_http = getattr(provider, "is_http_provider", None)
            if is_http is not True:
                if not isinstance(extra_body, Mapping):
                    return None
                if "model" in extra_body:
                    if is_http is not False:
                        return None  # The final merge depends on the transport.
                    # Unlike the adapter parameter merge, SDK extra_body uses
                    # None as a JSON null override rather than clearing it.
                    model = extra_body["model"]
    elif isinstance(provider, AnthropicProvider):
        model = kwargs.get("model_name", bound_model or "")
    else:
        model = bound_model
    # Preserve every valid string exactly. Invalid wire values are not turned
    # into invented model IDs; validation remains with the caller/provider.
    return model if isinstance(model, str) else None


__all__ = ["effective_request_model_name"]
