"""Read existing AWorld model profiles without importing the legacy CLI."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re

from aworld.models.context_window import resolve_model_context_window


@dataclass(frozen=True)
class ModelSettings:
    model: str | None
    base_url: str
    api_key: str | None = field(repr=False)
    context_window: int
    max_output_tokens: int
    window_source: str
    output_source: str
    profile: str | None = None
    parameters: dict = field(default_factory=dict, repr=False)


def _value(mapping, *keys):
    values = {str(key).lower(): value for key, value in mapping.items()}
    return next((values[key.lower()] for key in keys if values.get(key.lower()) not in (None, "")), None)


def _tokens(value, name):
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        value = int(value)
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def resolve_model_settings(args, *, environ=None, home=None):
    env = os.environ if environ is None else environ
    models = {}
    for root in (Path.home() if home is None else Path(home), args.cwd):
        path = root / ".aworld" / "aworld.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                raise ValueError(f"Cannot read model configuration: {path}") from None
            if not isinstance(data, dict) or not isinstance(data.get("models", {}), dict):
                raise ValueError(f"Model configuration must contain a models object: {path}")
            models.update(data.get("models", {}))
    profile_name = args.model_profile or env.get("AWORLD_MODEL_PROFILE")
    environment_model = _value(env, "AWORLD_MODEL", "OPENAI_MODEL", "LLM_MODEL_NAME")
    if not profile_name and not args.demo and not args.model and not environment_model and "default" in models:
        profile_name = "default"
    profile = {}
    if profile_name:
        profile = models.get(profile_name)
        if profile_name == "default" and profile is None and environment_model:
            profile = {}
        elif not isinstance(profile, dict):
            matches = [value for value in models.values() if isinstance(value, dict)
                       and _value(value, "llm_model_name", "model", "model_name") == profile_name]
            if len(matches) != 1:
                raise ValueError(f"Model profile is missing or ambiguous: {profile_name}")
            profile = matches[0]
    provider = _value(profile, "llm_provider", "provider")
    if provider and str(provider).lower() not in ("openai", "openai-compatible"):
        raise ValueError("The core CLI currently requires an OpenAI-compatible model profile")
    model = args.model or _value(profile, "llm_model_name", "model", "model_name") or environment_model
    base_url = args.base_url or _value(profile, "llm_base_url", "base_url") or _value(env, "AWORLD_BASE_URL", "OPENAI_BASE_URL", "LLM_BASE_URL") or "https://api.openai.com/v1"
    key = _value(profile, "llm_api_key", "api_key", "key", "token")
    key_env = _value(profile, "llm_api_key_env", "api_key_env", "key_env", "token_env")
    if not key and key_env:
        key = env.get(str(key_env))
    key = key or _value(env, "AWORLD_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY")
    compiler = _value(profile, "context_compiler") or {}
    if not isinstance(compiler, dict):
        raise ValueError("model profile context_compiler must be an object")
    limit = _value(env, "AWORLD_CONTEXT_LIMIT_TOKENS")
    window = _value(env, "AWORLD_CONTEXT_WINDOW_TOKENS", "AWORLD_CONTEXT_WINDOW")
    configured = _value(profile, "max_model_len", "context_window", "context_window_tokens", "AWORLD_CONTEXT_WINDOW_TOKENS")
    if args.context_window is not None:
        context_window, window_source = args.context_window, "cli"
    elif limit is not None:
        context_window, window_source = _tokens(limit, "AWORLD_CONTEXT_LIMIT_TOKENS"), "environment_context_limit"
    elif window is not None:
        context_window, window_source = _tokens(window, "AWORLD_CONTEXT_WINDOW_TOKENS"), "environment"
    elif compiler.get("context_limit") is not None:
        context_window, window_source = _tokens(compiler["context_limit"], "context_limit"), "profile_context_limit"
    elif configured is not None:
        context_window, window_source = _tokens(configured, "model profile context window"), "profile"
    else:
        resolved = resolve_model_context_window(model)
        context_window = 128000 if resolved.is_fallback else resolved.tokens
        window_source = "core_fallback" if resolved.is_fallback else resolved.source
    params = _value(profile, "params") or {}
    if not isinstance(params, dict):
        raise ValueError("model profile params must be an object")
    # Keep request identity and tools under the loop's ownership.
    unknown = set(params) - {"temperature", "top_p", "max_tokens", "reasoning_effort"}
    if unknown:
        raise ValueError("Unsupported core model profile params: " + ", ".join(sorted(unknown)))
    output_env = _value(env, "AWORLD_MAX_OUTPUT_TOKENS")
    output = _value(profile, "max_tokens", "max_output_tokens")
    if output is None:
        output = params.get("max_tokens")
    if args.max_output_tokens is not None:
        max_output, output_source = args.max_output_tokens, "cli"
    elif output_env is not None:
        max_output, output_source = _tokens(output_env, "AWORLD_MAX_OUTPUT_TOKENS"), "environment"
    elif output is not None:
        max_output, output_source = _tokens(output, "model profile max_tokens"), "profile"
    else:
        max_output, output_source = min(32768, max(1, context_window // 2)), "core_fallback"
    if type(context_window) is not int or context_window <= 0 or type(max_output) is not int or not 0 < max_output < context_window:
        raise ValueError("Model output limit must leave room for input within the context window")
    parameters = dict(params)
    temperature = _value(profile, "llm_temperature", "temperature")
    if temperature is not None:
        parameters["temperature"] = float(temperature)
    parameters.pop("max_tokens", None)
    if args.reasoning_effort is not None:
        parameters["reasoning_effort"] = args.reasoning_effort
    for name, lower, upper in (("temperature", 0, 2), ("top_p", 0, 1)):
        if name in parameters:
            value = parameters[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lower <= value <= upper:
                raise ValueError(f"model profile {name} is outside its supported range")
    if "reasoning_effort" in parameters and parameters["reasoning_effort"] not in ("none", "minimal", "low", "medium", "high", "xhigh"):
        raise ValueError("Unsupported model profile reasoning_effort")
    return ModelSettings(model, base_url, key, context_window, max_output, window_source, output_source,
                         profile_name, parameters)
