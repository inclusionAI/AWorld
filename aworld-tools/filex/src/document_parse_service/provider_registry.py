"""Versioned FileX provider capability registry and request validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .media_file_types import AUDIO_FILE_TYPES, IMAGE_FILE_TYPES, VIDEO_FILE_TYPES


@dataclass(frozen=True)
class ProviderDescriptor:
    provider_key: str
    supported_formats: frozenset[str]
    capabilities: frozenset[str]
    provider_version: str
    aliases: frozenset[str] = frozenset()
    timeout_class: str = "short"
    cost_class: str = "none"
    supports_partial_success: bool = False
    supports_page_selection: bool = False
    metrics_schema_version: str = "1.0"


_PROVIDERS = (
    ProviderDescriptor(
        provider_key="paddle_ocr",
        aliases=frozenset({"paddleocr", "paddle"}),
        supported_formats=frozenset({"pdf"}),
        capabilities=frozenset({"ocr", "layout", "table", "image_extract", "page_selection", "metrics"}),
        provider_version="paddleocr-vl-1.6",
        timeout_class="long",
        cost_class="high",
        supports_partial_success=True,
        supports_page_selection=True,
    ),
    ProviderDescriptor(
        provider_key="liteparse",
        supported_formats=frozenset({"pdf", "pptx"}),
        capabilities=frozenset({"native_text", "ocr", "table", "page_selection", "metrics"}),
        provider_version="2.0",
        timeout_class="long",
        cost_class="medium",
        supports_page_selection=True,
    ),
    ProviderDescriptor(
        provider_key="pypdf_vlm",
        aliases=frozenset({"pypdf+vlm", "vlm_pdf"}),
        supported_formats=frozenset({"pdf"}),
        capabilities=frozenset({"native_text", "vlm", "image_extract", "page_selection", "metrics"}),
        provider_version="1",
        timeout_class="long",
        cost_class="high",
        supports_partial_success=True,
        supports_page_selection=True,
    ),
    ProviderDescriptor(
        provider_key="python_pptx",
        supported_formats=frozenset({"pptx"}),
        capabilities=frozenset({"native_text", "table", "image_extract", "speaker_notes", "metrics"}),
        provider_version="1",
    ),
    ProviderDescriptor(
        provider_key="python_docx",
        supported_formats=frozenset({"docx"}),
        capabilities=frozenset({"native_text", "table", "image_extract", "header_footer", "comments", "metrics"}),
        provider_version="1",
    ),
    ProviderDescriptor(
        provider_key="openpyxl",
        supported_formats=frozenset({"xlsx"}),
        capabilities=frozenset({"sheet", "table", "formula", "merged_cell", "chart_metrics", "metrics"}),
        provider_version="1",
    ),
    ProviderDescriptor(
        provider_key="xlrd",
        supported_formats=frozenset({"xls"}),
        capabilities=frozenset({"sheet", "table", "metrics"}),
        provider_version="1",
    ),
    ProviderDescriptor(
        provider_key="pandas",
        supported_formats=frozenset({"csv"}),
        capabilities=frozenset({"table", "encoding_detection", "delimiter_detection", "metrics"}),
        provider_version="1",
    ),
    ProviderDescriptor(
        provider_key="image_vlm",
        aliases=frozenset({"openai_compatible", "openai", "openai_chat_completions"}),
        supported_formats=frozenset(IMAGE_FILE_TYPES),
        capabilities=frozenset(
            {
                "ocr",
                "vlm",
                "image_understanding",
                "scene_routing",
                "multi_object",
                "object_crop",
                "structured_evidence",
                "query_ready_fields",
                "metrics",
            }
        ),
        provider_version="3",
        timeout_class="long",
        cost_class="high",
    ),
    ProviderDescriptor(
        provider_key="local_whisper",
        aliases=frozenset({"local", "faster_whisper"}),
        supported_formats=frozenset(AUDIO_FILE_TYPES | VIDEO_FILE_TYPES),
        capabilities=frozenset({"transcription", "timeline", "metrics"}),
        provider_version="1",
        timeout_class="long",
        cost_class="medium",
    ),
    ProviderDescriptor(
        provider_key="native_text",
        supported_formats=frozenset({"txt", "md", "markdown"}),
        capabilities=frozenset({"native_text", "encoding_detection", "metrics"}),
        provider_version="1",
    ),
)

_DEFAULT_PROVIDER_BY_FORMAT = {
    "pdf": "paddle_ocr",
    "pptx": "python_pptx",
    "docx": "python_docx",
    "xlsx": "openpyxl",
    "xls": "xlrd",
    "csv": "pandas",
    "txt": "native_text",
    "md": "native_text",
    "markdown": "native_text",
    **{file_type: "image_vlm" for file_type in IMAGE_FILE_TYPES},
    **{file_type: "local_whisper" for file_type in AUDIO_FILE_TYPES | VIDEO_FILE_TYPES},
}

_PROVIDER_BY_NAME = {
    name: descriptor
    for descriptor in _PROVIDERS
    for name in {descriptor.provider_key, *descriptor.aliases}
}


def list_provider_descriptors(file_type: str | None = None) -> list[ProviderDescriptor]:
    normalized_type = _normalize_file_type(file_type or "")
    if not normalized_type:
        return list(_PROVIDERS)
    return [descriptor for descriptor in _PROVIDERS if normalized_type in descriptor.supported_formats]


def default_provider_for_format(file_type: str) -> str:
    normalized_type = _normalize_file_type(file_type)
    provider = _DEFAULT_PROVIDER_BY_FORMAT.get(normalized_type)
    if not provider:
        raise ValueError(f"Unsupported file type: {file_type}")
    return provider


def normalize_provider_env(
    file_type: str,
    env_content: dict[str, Any] | None,
    *,
    use_default: bool = True,
) -> dict[str, Any]:
    normalized_type = _normalize_file_type(file_type)
    normalized_env = dict(env_content or {})
    requested_provider = _requested_provider(normalized_type, normalized_env)
    if not requested_provider and not use_default:
        return normalized_env
    provider_name = requested_provider or default_provider_for_format(normalized_type)
    descriptor = _PROVIDER_BY_NAME.get(provider_name.lower())
    if descriptor is None:
        raise ValueError(f"unsupported_provider: {provider_name}")
    if normalized_type not in descriptor.supported_formats:
        supported = sorted(descriptor.supported_formats)
        raise ValueError(
            f"unsupported_provider_for_format: provider={provider_name} "
            f"file_type={normalized_type} supported_formats={supported}"
        )

    normalized_env["filex_parse_provider"] = descriptor.provider_key
    normalized_env["filex_provider_version"] = descriptor.provider_version
    if normalized_type == "pdf":
        normalized_env["pdf_parse_provider"] = descriptor.provider_key
    elif normalized_type in {"ppt", "pptx"}:
        normalized_env["pptx_parse_provider"] = descriptor.provider_key
    return normalized_env


def _requested_provider(file_type: str, env_content: dict[str, Any]) -> str:
    if env_content.get("filex_parse_provider"):
        return str(env_content["filex_parse_provider"]).strip()
    if file_type == "pdf":
        return str(env_content.get("pdf_parse_provider") or env_content.get("pdf_provider") or "").strip()
    if file_type in {"ppt", "pptx"}:
        return str(env_content.get("pptx_parse_provider") or env_content.get("ppt_parse_provider") or "").strip()
    return ""


def _normalize_file_type(file_type: str) -> str:
    normalized = str(file_type or "").strip().lower().lstrip(".")
    return "md" if normalized == "markdown" else normalized
