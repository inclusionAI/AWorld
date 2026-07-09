from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from aworld.config.conf import ModelConfig


def resolve_model_profile(
    profile_name: str | None,
    *,
    config_dict: Mapping[str, Any] | None = None,
) -> ModelConfig | None:
    """Resolve a named model profile from CLI config without mutating env."""
    profile = str(profile_name or "").strip()
    if not profile:
        return None

    candidates: list[Mapping[str, Any]] = []
    if config_dict is not None:
        models = config_dict.get("models") if isinstance(config_dict, Mapping) else None
        if isinstance(models, Mapping):
            candidate = models.get(profile)
            if isinstance(candidate, Mapping):
                candidates.append(candidate)

    try:
        from aworld_cli.core.config import get_config

        models = get_config().load_config().get("models") or {}
        if isinstance(models, Mapping):
            candidate = models.get(profile)
            if isinstance(candidate, Mapping):
                candidates.append(candidate)
    except Exception:
        pass

    if profile == "default":
        env_profile = _profile_from_env()
        if env_profile:
            candidates.append(env_profile)

    for candidate in candidates:
        model_config = _model_config_from_profile(candidate)
        if model_config is not None:
            return model_config
    raise KeyError(f"model profile not found or incomplete: {profile}")


def _profile_from_env() -> dict[str, Any] | None:
    model_name = os.environ.get("LLM_MODEL_NAME")
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not model_name or not api_key:
        return None
    return {
        "provider": os.environ.get("LLM_PROVIDER") or "openai",
        "model": model_name,
        "api_key": api_key,
        "base_url": os.environ.get("LLM_BASE_URL"),
        "temperature": os.environ.get("LLM_TEMPERATURE"),
    }


def _model_config_from_profile(profile: Mapping[str, Any]) -> ModelConfig | None:
    model_name = (
        profile.get("llm_model_name")
        or profile.get("model")
        or profile.get("model_name")
    )
    provider = profile.get("llm_provider") or profile.get("provider")
    api_key = profile.get("llm_api_key") or profile.get("api_key")
    api_key_env = profile.get("api_key_env")
    if not api_key and api_key_env:
        api_key = os.environ.get(str(api_key_env))
    if not api_key and provider:
        provider_key = str(provider).strip().upper()
        api_key = os.environ.get(f"{provider_key}_API_KEY")
    base_url = profile.get("llm_base_url") or profile.get("base_url")
    temperature = profile.get("llm_temperature", profile.get("temperature"))
    params = profile.get("params")

    if not model_name:
        return None

    kwargs: dict[str, Any] = {
        "llm_model_name": str(model_name),
        "llm_provider": str(provider) if provider else None,
        "llm_api_key": str(api_key) if api_key else None,
        "llm_base_url": str(base_url) if base_url else None,
    }
    if temperature not in (None, ""):
        kwargs["llm_temperature"] = float(temperature)
    if isinstance(params, Mapping):
        kwargs["params"] = dict(params)
    return ModelConfig(**kwargs)
