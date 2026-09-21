"""Exact-type registry for reviewed provider lowering adapters."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from .rollout import ProviderLoweringCapability


@dataclass(frozen=True, slots=True)
class ReviewedProviderAdapter:
    provider_type: type
    capability: ProviderLoweringCapability


@dataclass(frozen=True, slots=True)
class ReviewedProviderProxyAdapter:
    proxy_type: type
    delegate_attribute: str


class ReviewedProviderLoweringRegistry:
    """Registry whose membership is limited to framework-owned provider types.

    A capability declaration is only corroborating evidence. A provider must
    not be able to authorize itself merely by registering at runtime, so this
    registry additionally fences registration with an immutable framework
    allowlist.
    """

    _FRAMEWORK_TYPE_IDENTITIES = frozenset(
        {
            ("aworld.models.openai_provider", "OpenAIProvider"),
            ("aworld.models.openai_provider", "AzureOpenAIProvider"),
            ("aworld.models.anthropic_provider", "AnthropicProvider"),
            ("aworld.models.ant_provider", "AntProvider"),
            (
                "aworld.models.reviewed_custom_provider",
                "ReviewedCustomChatProvider",
            ),
        }
    )
    _FRAMEWORK_PROXY_TYPE_IDENTITIES = frozenset(
        {
            (
                "aworld.self_evolve.candidate_generation",
                "_SanitizingProvider",
            ),
        }
    )

    def __init__(self) -> None:
        self._lock = RLock()
        self._by_type: dict[type, ReviewedProviderAdapter] = {}
        self._proxy_by_type: dict[type, ReviewedProviderProxyAdapter] = {}

    def register(
        self, provider_type: type, capability: ProviderLoweringCapability
    ) -> None:
        if not isinstance(provider_type, type):
            raise TypeError("provider_type must be a class")
        if not isinstance(capability, ProviderLoweringCapability):
            raise TypeError("capability must be ProviderLoweringCapability")
        identity = (provider_type.__module__, provider_type.__qualname__)
        if identity not in self._FRAMEWORK_TYPE_IDENTITIES:
            raise ValueError("provider type is not framework-reviewed")
        adapter = ReviewedProviderAdapter(provider_type, capability)
        with self._lock:
            existing = self._by_type.get(provider_type)
            if existing is not None and existing != adapter:
                raise ValueError(
                    "provider type already has a different reviewed adapter"
                )
            self._by_type[provider_type] = adapter

    def register_proxy(
        self, proxy_type: type, *, delegate_attribute: str
    ) -> None:
        """Register one framework-reviewed, exact-type transparent proxy.

        Proxies do not receive a lowering capability of their own. Resolution
        succeeds only when their delegate is itself an exact registered
        provider whose declaration matches its reviewed adapter.
        """
        if not isinstance(proxy_type, type):
            raise TypeError("proxy_type must be a class")
        if not isinstance(delegate_attribute, str) or not delegate_attribute:
            raise ValueError("delegate_attribute must be a non-empty string")
        identity = (proxy_type.__module__, proxy_type.__qualname__)
        if identity not in self._FRAMEWORK_PROXY_TYPE_IDENTITIES:
            raise ValueError("provider proxy type is not framework-reviewed")
        adapter = ReviewedProviderProxyAdapter(proxy_type, delegate_attribute)
        with self._lock:
            existing = self._proxy_by_type.get(proxy_type)
            if existing is not None and existing != adapter:
                raise ValueError(
                    "provider proxy type already has a different reviewed adapter"
                )
            self._proxy_by_type[proxy_type] = adapter

    def resolve(
        self, provider: object, provider_name: str
    ) -> ProviderLoweringCapability | None:
        with self._lock:
            adapter = self._by_type.get(type(provider))
            proxy_adapter = self._proxy_by_type.get(type(provider))
        if adapter is None:
            if proxy_adapter is None:
                return None
            try:
                delegate = object.__getattribute__(
                    provider, proxy_adapter.delegate_attribute
                )
            except Exception:
                return None
            if delegate is provider:
                return None
            return self.resolve(delegate, provider_name)
        if adapter.capability.provider_name != provider_name:
            return None
        try:
            declared = provider.context_candidate_lowering_capability()
        except Exception:
            return None
        return adapter.capability if declared == adapter.capability else None

    def capabilities(self) -> tuple[ProviderLoweringCapability, ...]:
        with self._lock:
            return tuple(adapter.capability for adapter in self._by_type.values())


reviewed_provider_lowerings = ReviewedProviderLoweringRegistry()


__all__ = [
    "ReviewedProviderAdapter",
    "ReviewedProviderLoweringRegistry",
    "ReviewedProviderProxyAdapter",
    "reviewed_provider_lowerings",
]
