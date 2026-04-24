from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .stdio_harness import repo_root


_ENV_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_TEMPLATE_SPECS = {
    "base": {
        "filename": "acp_stdio_host_contract.template.json",
        "requiredEnv": ["AWORLD_WORKSPACE"],
    },
    "same-host": {
        "filename": "acp_stdio_host_contract.same_host.template.json",
        "requiredEnv": ["AWORLD_WORKSPACE"],
    },
    "distributed": {
        "filename": "acp_stdio_host_contract.distributed.template.json",
        "requiredEnv": ["AWORLD_WORKER_WORKSPACE"],
    },
}

_VALIDATION_CONFIG_FIELDS = {
    "topology",
    "command",
    "cwd",
    "profile",
    "sessionParams",
    "env",
    "startupTimeoutSeconds",
    "startupRetries",
}


def validation_assets_dir() -> Path:
    return repo_root() / "tests" / "integration" / "fixtures"


def validation_config_schema_path() -> str:
    return str(validation_assets_dir() / "acp_stdio_host_contract.schema.json")


def list_validation_template_names() -> list[str]:
    return sorted(_TEMPLATE_SPECS)


def validation_template_metadata() -> list[dict[str, Any]]:
    assets_dir = validation_assets_dir()
    return [
        {
            "name": name,
            "templatePath": str(assets_dir / spec["filename"]),
            "requiredEnv": list(spec["requiredEnv"]),
        }
        for name, spec in sorted(_TEMPLATE_SPECS.items())
    ]


def list_validation_config_fields() -> list[str]:
    return sorted(_VALIDATION_CONFIG_FIELDS)


def _expand_env_placeholders_in_string(raw: str, env: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        try:
            return env[name]
        except KeyError as exc:
            raise ValueError(f"Missing environment variable for config placeholder: {name}") from exc

    return _ENV_PLACEHOLDER_PATTERN.sub(replace, raw)


def expand_env_placeholders(value: Any, env: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _expand_env_placeholders_in_string(value, env)
    if isinstance(value, list):
        return [expand_env_placeholders(item, env) for item in value]
    if isinstance(value, dict):
        return {str(key): expand_env_placeholders(item, env) for key, item in value.items()}
    return value


def load_validation_template(name: str, *, expand_placeholders: bool = False, env: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        spec = _TEMPLATE_SPECS[name]
    except KeyError as exc:
        available = ", ".join(list_validation_template_names())
        raise ValueError(f"Unknown validation template topology: {name}. Available: {available}") from exc

    payload = json.loads((validation_assets_dir() / spec["filename"]).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Validation template {name} must contain a JSON object.")
    if expand_placeholders:
        return expand_env_placeholders(payload, env or dict(os.environ))
    return payload


def validate_validation_config_shape(payload: dict[str, Any]) -> dict[str, Any]:
    unknown_fields = sorted(set(payload) - _VALIDATION_CONFIG_FIELDS)
    if unknown_fields:
        raise ValueError(
            "Validation config contains unsupported field(s): "
            + ", ".join(unknown_fields)
        )

    topology = payload.get("topology")
    if topology is not None and topology not in _TEMPLATE_SPECS:
        available = ", ".join(list_validation_template_names())
        raise ValueError(
            f"Validation config topology must be one of: {available}. Got: {topology}"
        )

    return payload
