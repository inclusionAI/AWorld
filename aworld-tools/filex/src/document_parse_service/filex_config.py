"""Load filex default configuration."""

from __future__ import annotations

import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_FILEX_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "filex.yaml"


def load_filex_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load the default filex YAML config.

    Missing config files are treated as an empty config so local development can
    still run with explicit env_content only.
    """

    resolved_path = config_path or _resolve_config_path()
    if not resolved_path.exists():
        logger.info("filex config not found, using empty defaults | path=%s", resolved_path)
        return {}

    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("filex config requires PyYAML. Install pyyaml or remove filex.yaml.") from exc

    with resolved_path.open("r", encoding="utf-8") as config_file:
        loaded = yaml.safe_load(config_file) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"filex config must be a YAML object: {resolved_path}")
    return loaded


def build_default_env_content(
    *,
    file_type: str,
    media_type: str = "",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build env_content-compatible defaults for the requested parse target."""

    resolved_config = config if config is not None else load_filex_config()
    env: dict[str, Any] = {}
    afts_config = resolved_config.get("afts") or {}
    if isinstance(afts_config, dict):
        env.update(
            {
                key: value
                for key, value in afts_config.items()
                if key in {"afts_app_id", "afts_base_url", "afts_biz_key", "afts_biz_secret"}
            }
        )

    gateway_vllm_config = resolved_config.get("gateway_vllm") or {}
    if isinstance(gateway_vllm_config, dict):
        env["gateway_vllm"] = deepcopy(gateway_vllm_config)

    document_parse_config = resolved_config.get("document_parse") or {}
    if isinstance(document_parse_config, dict):
        liteparse_config = document_parse_config.get("liteparse") or {}
        if isinstance(liteparse_config, dict):
            env.update(_prefix_keys(liteparse_config, "liteparse_"))
        if file_type.lower().strip() == "pdf":
            pdf_config = document_parse_config.get("pdf") or {}
            if isinstance(pdf_config, dict):
                env.update(_prefix_keys(pdf_config, "pdf_"))
        if file_type.lower().strip() in {"ppt", "pptx"}:
            pptx_config = document_parse_config.get("pptx") or {}
            if isinstance(pptx_config, dict):
                env.update(_prefix_keys(pptx_config, "pptx_"))

    media_defaults = _resolve_media_defaults(
        resolved_config=resolved_config,
        media_type=media_type,
    )
    if media_defaults:
        env.update(media_defaults)
    return env


def merge_env_content(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Deep merge env_content dictionaries with overrides taking precedence."""

    return _deep_merge(defaults, overrides)


def _resolve_config_path() -> Path:
    configured = str(os.getenv("FILEX_CONFIG_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_FILEX_CONFIG_PATH


def _resolve_media_defaults(
    *,
    resolved_config: dict[str, Any],
    media_type: str,
) -> dict[str, Any]:
    if not media_type:
        return {}
    media_parse_config = resolved_config.get("media_parse") or {}
    if not isinstance(media_parse_config, dict):
        return {}
    media_config = media_parse_config.get(media_type) or {}
    if not isinstance(media_config, dict):
        return {}

    backend_name = str(media_config.get("backend") or "").strip()
    if not backend_name:
        return {}

    backend_options = media_config.get(backend_name) or {}
    if backend_options and not isinstance(backend_options, dict):
        raise ValueError(f"media_parse.{media_type}.{backend_name} must be an object")

    return {
        "media_parse_backend": backend_name,
        "media_parse_options": deepcopy(backend_options),
    }


def _prefix_keys(config: dict[str, Any], prefix: str) -> dict[str, Any]:
    return {f"{prefix}{key}": value for key, value in config.items()}


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overrides.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
