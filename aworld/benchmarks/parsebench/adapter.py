"""Deterministic FileX adapter for the public ParseBench task contract.

The adapter deliberately consumes only ``environment/parsebench-task.json``.
Official rules and expected outputs remain verifier-only inputs.  FileX runs in
its own process by default so parser timeouts have a real process boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aworld.benchmarks.parsebench.contracts import DATASET_REVISION, SCORER_REVISION


TASK_SPEC_SCHEMA_VERSION = "aworld-parsebench-task/v1"
RESULT_SCHEMA_VERSION = "aworld-parsebench-filex-result/v1"
FILEX_DOCUMENT_IR_SCHEMA_VERSION = "filex-document-ir-v2"
FILEX_COORDINATE_SYSTEM = "pixel_top_left_xyxy"
DEFAULT_TASK_SPEC_PATH = Path("/workspace/parsebench-task.json")
DEFAULT_ARTIFACTS_ROOT = Path("/logs/artifacts")
DEFAULT_PROVIDER = "paddle_ocr"
DEFAULT_LLM_MODEL_PROFILE = "default__gpt-5.5"
DEFAULT_VLM_MODEL_PROFILE = "default__gemini-3.1-pro-preview"

_TASK_FIELDS = frozenset(
    {"schema_version", "task_id", "dataset_revision", "scorer_revision", "source"}
)
_SOURCE_FIELDS = frozenset({"runtime_path", "size", "sha256", "page"})
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_TASK_SPEC_BYTES = 64 * 1024
_MAX_RUNNER_OUTPUT_BYTES = 4 * 1024 * 1024


class FileXAdapterError(RuntimeError):
    """Stable, content-blind adapter failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ParseBenchTaskSource:
    runtime_path: Path
    size: int
    sha256: str
    page: int | None


@dataclass(frozen=True, slots=True)
class ParseBenchTaskSpec:
    schema_version: str
    task_id: str
    dataset_revision: str
    scorer_revision: str
    source: ParseBenchTaskSource


@dataclass(frozen=True, slots=True)
class FileXExecutionOptions:
    """Protected execution controls; none are sourced from dataset rows."""

    workspace_root: Path = Path("/workspace")
    artifacts_root: Path = DEFAULT_ARTIFACTS_ROOT
    provider: str = DEFAULT_PROVIDER
    timeout_seconds: float = 600.0
    no_cache: bool = True
    clip_bboxes: bool = True
    llm_model_profile: str = DEFAULT_LLM_MODEL_PROFILE
    vlm_model_profile: str = DEFAULT_VLM_MODEL_PROFILE

    def __post_init__(self) -> None:
        workspace = _absolute_path(self.workspace_root, "workspace_root")
        artifacts = _absolute_path(self.artifacts_root, "artifacts_root")
        object.__setattr__(self, "workspace_root", workspace)
        object.__setattr__(self, "artifacts_root", artifacts)
        if not _PROVIDER_PATTERN.fullmatch(self.provider) or self.provider == "auto":
            raise ValueError("provider must be an explicit canonical FileX provider")
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ):
            raise TypeError("timeout_seconds must be a finite number")
        timeout = float(self.timeout_seconds)
        if not math.isfinite(timeout) or not 0.1 <= timeout <= 7_200:
            raise ValueError("timeout_seconds must be between 0.1 and 7200")
        object.__setattr__(self, "timeout_seconds", timeout)
        if self.no_cache is not True:
            raise ValueError("ParseBench execution requires no_cache=True")
        if not isinstance(self.clip_bboxes, bool):
            raise TypeError("clip_bboxes must be a bool")
        _validate_logical_profile(self.llm_model_profile, "llm_model_profile")
        _validate_logical_profile(self.vlm_model_profile, "vlm_model_profile")


@dataclass(frozen=True, slots=True)
class FileXRunRequest:
    source_path: Path
    task_id: str
    file_type: str
    provider: str
    page: int | None
    no_cache: bool
    timeout_seconds: float
    workspace_root: Path
    llm_model_profile: str
    vlm_model_profile: str


class FileXRunner(Protocol):
    def run(self, request: FileXRunRequest) -> Mapping[str, object]: ...


class SubprocessFileXRunner:
    """Invoke FileX without a shell and with a hard timeout."""

    def __init__(self, *, executable: str = "filex") -> None:
        if not isinstance(executable, str) or not executable.strip():
            raise ValueError("executable must be a non-empty string")
        self._executable = executable.strip()

    def run(self, request: FileXRunRequest) -> Mapping[str, object]:
        if request.no_cache is not True:
            raise FileXAdapterError(
                "cache_not_bypassed", "FileX benchmark execution requires no-cache"
            )
        env_content: dict[str, object] = {
            "filex_parse_provider": request.provider,
            "filex_cache_enabled": False,
            "filex_no_cache": True,
            # These are logical identities resolved by the protected runtime.
            # Credentials and provider endpoints never enter task material.
            "benchmark_llm_model_profile": request.llm_model_profile,
            "benchmark_vlm_model_profile": request.vlm_model_profile,
        }
        command = [
            self._executable,
            "parse",
            "--workspace-path",
            str(request.source_path),
            "--source-provider",
            "local",
            "--file-type",
            request.file_type,
            "--sync-mode",
            "sync",
            "--asset-reference-mode",
            "local_path",
            "--task-id",
            request.task_id,
            "--no-cache",
            "--env-content-json",
            _canonical_json_text(env_content),
        ]
        if request.page is not None:
            command.extend(("--pages", str(request.page)))
        process_env = os.environ.copy()
        process_env["FILEX_WORKSPACE_ROOT"] = str(request.workspace_root)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=request.timeout_seconds,
            env=process_env,
            shell=False,
        )
        if completed.returncode != 0:
            raise FileXAdapterError(
                "filex_execution_failed",
                f"FileX execution failed with exit status {completed.returncode}",
            )
        if len(completed.stdout.encode("utf-8")) > _MAX_RUNNER_OUTPUT_BYTES:
            raise FileXAdapterError(
                "filex_output_too_large", "FileX control output exceeds the size limit"
            )
        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise FileXAdapterError(
                "invalid_filex_output", "FileX returned invalid JSON control output"
            ) from exc
        if not isinstance(payload, dict):
            raise FileXAdapterError(
                "invalid_filex_output", "FileX control output must be a JSON object"
            )
        return payload


def load_parsebench_task_spec(
    path: Path | str = DEFAULT_TASK_SPEC_PATH,
) -> ParseBenchTaskSpec:
    """Load the strict public v1 task spec without accepting scoring metadata."""

    spec_path = Path(path)
    if spec_path.is_dir():
        spec_path = spec_path / "parsebench-task.json"
    try:
        if spec_path.stat().st_size > _MAX_TASK_SPEC_BYTES:
            raise FileXAdapterError(
                "task_spec_too_large", "ParseBench task spec exceeds the size limit"
            )
        payload = json.loads(spec_path.read_text(encoding="utf-8"))
    except FileXAdapterError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FileXAdapterError(
            "invalid_task_spec", "ParseBench task spec is not readable JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise FileXAdapterError(
            "invalid_task_spec", "ParseBench task spec must be a JSON object"
        )
    _require_exact_fields(payload, _TASK_FIELDS, "task spec")
    if payload["schema_version"] != TASK_SPEC_SCHEMA_VERSION:
        raise FileXAdapterError(
            "unsupported_task_spec", "unsupported ParseBench task spec schema_version"
        )
    task_id = payload["task_id"]
    if not isinstance(task_id, str) or _TASK_ID_PATTERN.fullmatch(task_id) is None:
        raise FileXAdapterError("invalid_task_spec", "task_id is invalid")
    if payload["dataset_revision"] != DATASET_REVISION:
        raise FileXAdapterError(
            "revision_mismatch", "task spec dataset_revision is not pinned"
        )
    if payload["scorer_revision"] != SCORER_REVISION:
        raise FileXAdapterError(
            "revision_mismatch", "task spec scorer_revision is not pinned"
        )
    source = payload["source"]
    if not isinstance(source, dict):
        raise FileXAdapterError("invalid_task_spec", "source must be an object")
    _require_exact_fields(source, _SOURCE_FIELDS, "source")
    runtime_path = source["runtime_path"]
    if not isinstance(runtime_path, str) or not runtime_path.strip():
        raise FileXAdapterError("invalid_task_spec", "source.runtime_path is invalid")
    parsed_runtime_path = Path(runtime_path)
    if not parsed_runtime_path.is_absolute():
        raise FileXAdapterError(
            "invalid_task_spec", "source.runtime_path must be absolute"
        )
    size = source["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise FileXAdapterError("invalid_task_spec", "source.size must be positive")
    digest = source["sha256"]
    if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
        raise FileXAdapterError("invalid_task_spec", "source.sha256 is invalid")
    page = source["page"]
    if page is not None and (
        isinstance(page, bool) or not isinstance(page, int) or page < 1
    ):
        raise FileXAdapterError(
            "invalid_task_spec", "source.page must be null or a one-based integer"
        )
    return ParseBenchTaskSpec(
        schema_version=TASK_SPEC_SCHEMA_VERSION,
        task_id=task_id,
        dataset_revision=DATASET_REVISION,
        scorer_revision=SCORER_REVISION,
        source=ParseBenchTaskSource(
            runtime_path=parsed_runtime_path,
            size=size,
            sha256=digest,
            page=page,
        ),
    )


def execute_filex_parsebench(
    spec: ParseBenchTaskSpec,
    *,
    options: FileXExecutionOptions,
    runner: FileXRunner | None = None,
) -> dict[str, Any]:
    """Run one public ParseBench task and atomically publish its artifacts."""

    if not isinstance(spec, ParseBenchTaskSpec):
        raise TypeError("spec must be a ParseBenchTaskSpec")
    source_path = _validated_source(spec, options.workspace_root)
    request = FileXRunRequest(
        source_path=source_path,
        task_id=spec.task_id,
        file_type=_file_type(source_path),
        provider=options.provider,
        page=spec.source.page,
        no_cache=True,
        timeout_seconds=options.timeout_seconds,
        workspace_root=options.workspace_root,
        llm_model_profile=options.llm_model_profile,
        vlm_model_profile=options.vlm_model_profile,
    )
    selected_runner = runner or SubprocessFileXRunner()
    try:
        raw_result = selected_runner.run(request)
    except subprocess.TimeoutExpired as exc:
        raise FileXAdapterError("filex_timeout", "FileX execution timed out") from exc
    except FileXAdapterError:
        raise
    except Exception as exc:
        raise FileXAdapterError(
            "filex_execution_failed", "FileX execution failed"
        ) from exc
    if not isinstance(raw_result, Mapping) or raw_result.get("success") is not True:
        raise FileXAdapterError(
            "filex_execution_failed", "FileX execution did not succeed"
        )

    provider_version, cache_status = _validate_execution_identity(
        raw_result, requested_provider=options.provider
    )
    markdown_path = _resolve_filex_artifact(
        raw_result.get("file_path"), options.workspace_root, "Markdown"
    )
    document_ir_path = _resolve_filex_artifact(
        raw_result.get("document_file_path"), options.workspace_root, "Document IR"
    )
    try:
        markdown = markdown_path.read_text(encoding="utf-8")
        document_ir = json.loads(document_ir_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FileXAdapterError(
            "invalid_filex_artifact", "FileX emitted an unreadable artifact"
        ) from exc
    if not isinstance(document_ir, dict):
        raise FileXAdapterError(
            "invalid_document_ir", "FileX Document IR must be a JSON object"
        )
    parse_output = normalize_filex_document_ir(
        document_ir,
        markdown=markdown,
        task_id=spec.task_id,
        provider=options.provider,
        provider_version=provider_version,
        selected_page=spec.source.page,
        clip_bboxes=options.clip_bboxes,
    )

    document_bytes = markdown.encode("utf-8")
    layout_bytes = _canonical_json_bytes(parse_output, newline=True)
    document_evidence = _artifact_evidence(
        "/logs/artifacts/document.md", document_bytes
    )
    layout_evidence = _artifact_evidence("/logs/artifacts/layout.json", layout_bytes)
    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "succeeded",
        "task_id": spec.task_id,
        "artifacts": {
            "document": document_evidence,
            "layout": layout_evidence,
        },
        "provenance": {
            "dataset_revision": spec.dataset_revision,
            "scorer_revision": spec.scorer_revision,
            "source": {
                "runtime_path": str(spec.source.runtime_path),
                "size": spec.source.size,
                "sha256": spec.source.sha256,
                "page": spec.source.page,
            },
            "filex": {
                "provider": options.provider,
                "provider_version": provider_version,
                "fallback_allowed": False,
                "cache": cache_status,
                "llm_model_profile": options.llm_model_profile,
                "vlm_model_profile": options.vlm_model_profile,
                "document_ir_schema_version": FILEX_DOCUMENT_IR_SCHEMA_VERSION,
                "coordinate_transform": "pixel-xyxy-to-normalized-xywh",
                "bbox_policy": "clip" if options.clip_bboxes else "fail_closed",
            },
        },
    }
    result_bytes = _canonical_json_bytes(result, newline=True)
    options.artifacts_root.mkdir(parents=True, exist_ok=True)
    _atomic_write(options.artifacts_root / "document.md", document_bytes)
    _atomic_write(options.artifacts_root / "layout.json", layout_bytes)
    _atomic_write(options.artifacts_root / "result.json", result_bytes)
    return result


def normalize_filex_document_ir(
    document_ir: Mapping[str, object],
    *,
    markdown: str,
    task_id: str,
    provider: str,
    provider_version: str,
    selected_page: int | None,
    clip_bboxes: bool,
) -> dict[str, Any]:
    """Convert FileX v2 pixel geometry into the pinned ParseOutput shape."""

    if document_ir.get("schema_version") != FILEX_DOCUMENT_IR_SCHEMA_VERSION:
        raise FileXAdapterError(
            "unsupported_document_ir", "unsupported FileX Document IR schema_version"
        )
    if document_ir.get("coordinate_system") != FILEX_COORDINATE_SYSTEM:
        raise FileXAdapterError(
            "unsupported_coordinates", "unsupported FileX coordinate system"
        )
    raw_pages = document_ir.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raise FileXAdapterError("invalid_document_ir", "FileX Document IR has no pages")
    pages: list[tuple[int, dict[str, Any], str]] = []
    seen_pages: set[int] = set()
    for raw_page in raw_pages:
        if not isinstance(raw_page, Mapping):
            raise FileXAdapterError(
                "invalid_document_ir", "FileX Document IR page is invalid"
            )
        page_index = _nonnegative_int(raw_page.get("page_index"), "page_index")
        if page_index in seen_pages:
            raise FileXAdapterError(
                "invalid_document_ir", "FileX Document IR has a duplicate page"
            )
        seen_pages.add(page_index)
        width = _positive_finite(raw_page.get("width"), "page width")
        height = _positive_finite(raw_page.get("height"), "page height")
        raw_elements = raw_page.get("elements")
        if not isinstance(raw_elements, list):
            raise FileXAdapterError(
                "invalid_document_ir", "FileX Document IR elements are invalid"
            )
        sortable: list[tuple[tuple[float, float, float, int], dict[str, Any]]] = []
        for index, raw_element in enumerate(raw_elements):
            if not isinstance(raw_element, Mapping):
                raise FileXAdapterError(
                    "invalid_document_ir", "FileX Document IR element is invalid"
                )
            text = raw_element.get("text")
            if not isinstance(text, str):
                raise FileXAdapterError(
                    "invalid_document_ir", "FileX Document IR element text is invalid"
                )
            bbox = _normalize_bbox(
                raw_element.get("bbox"),
                width=width,
                height=height,
                clip=clip_bboxes,
            )
            raw_type = raw_element.get("type")
            if not isinstance(raw_type, str) or not raw_type.strip():
                raise FileXAdapterError(
                    "invalid_document_ir", "FileX Document IR element label is invalid"
                )
            label = _canonical_label(raw_type)
            reading_order = raw_element.get("reading_order")
            if reading_order is None:
                order_key = math.inf
                exposed_order: int | None = None
            else:
                exposed_order = _nonnegative_int(reading_order, "reading_order")
                order_key = float(exposed_order)
            segment = {**bbox, "label": label}
            confidence = raw_element.get("confidence")
            if confidence is not None:
                score = _finite_number(confidence, "confidence")
                if not 0 <= score <= 1:
                    raise FileXAdapterError(
                        "invalid_document_ir", "confidence must be between zero and one"
                    )
                segment["confidence"] = score
            item: dict[str, Any] = {
                "type": "table" if label == "Table" else "text",
                "md": text,
                "html": "",
                "value": text,
                "bbox": dict(segment),
                "layout_segments": [segment],
                "reading_order": exposed_order,
            }
            sortable.append(((order_key, bbox["y"], bbox["x"], index), item))
        sortable.sort(key=lambda pair: pair[0])
        items = [item for _, item in sortable]
        page_text = "\n\n".join(item["value"] for item in items if item["value"])
        pages.append(
            (
                page_index,
                {
                    "page_number": page_index + 1,
                    "width": width,
                    "height": height,
                    "md": "",  # resolved after page cardinality is known
                    "text": page_text,
                    "items": items,
                },
                page_text,
            )
        )
    pages.sort(key=lambda item: item[0])
    if selected_page is not None:
        expected_index = selected_page - 1
        if len(pages) != 1 or pages[0][0] != expected_index:
            raise FileXAdapterError(
                "page_mismatch", "FileX output does not match the selected page"
            )
    page_outputs: list[dict[str, object]] = []
    layout_pages: list[dict[str, Any]] = []
    for page_index, layout_page, page_text in pages:
        page_markdown = markdown if len(pages) == 1 else page_text
        layout_page["md"] = page_markdown
        page_outputs.append({"page_index": page_index, "markdown": page_markdown})
        layout_pages.append(layout_page)
    return {
        "task_type": "parse",
        "example_id": task_id,
        "pipeline_name": f"filex/{provider}@{provider_version}",
        "pages": page_outputs,
        "layout_pages": layout_pages,
        "markdown": markdown,
    }


_LABELS = {
    "caption": "Caption",
    "figure-title": "Caption",
    "footnote": "Footnote",
    "formula": "Formula",
    "list-item": "List-item",
    "list-items": "List-item",
    "page-footer": "Page-footer",
    "footer": "Page-footer",
    "page-header": "Page-header",
    "header": "Page-header",
    "picture": "Picture",
    "image": "Picture",
    "chart": "Picture",
    "seal": "Picture",
    "section-header": "Section-header",
    "paragraph-title": "Section-header",
    "heading": "Section-header",
    "table": "Table",
    "text": "Text",
    "content": "Text",
    "abstract": "Text",
    "reference": "Text",
    "reference-content": "Text",
    "aside-text": "Text",
    "number": "Text",
    "formula-number": "Text",
    "title": "Title",
    "doc-title": "Title",
    "document-index": "Document Index",
    "code": "Code",
    "algorithm": "Code",
    "checkbox-selected": "Checkbox-Selected",
    "checkbox-unselected": "Checkbox-Unselected",
    "form": "Form",
    "key-value-region": "Key-Value Region",
}


def _canonical_label(raw_label: str) -> str:
    key = raw_label.strip().lower().replace("_", " ")
    key = "-".join(key.split())
    try:
        return _LABELS[key]
    except KeyError as exc:
        raise FileXAdapterError(
            "unknown_layout_label", "FileX emitted an unsupported layout label"
        ) from exc


def _normalize_bbox(
    raw_bbox: object, *, width: float, height: float, clip: bool
) -> dict[str, float]:
    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        raise FileXAdapterError("invalid_bbox", "FileX bbox must contain four values")
    x1, y1, x2, y2 = (_finite_number(value, "bbox coordinate") for value in raw_bbox)
    if x2 <= x1 or y2 <= y1:
        raise FileXAdapterError("invalid_bbox", "FileX bbox has invalid extents")
    if clip:
        x1, x2 = max(0.0, min(width, x1)), max(0.0, min(width, x2))
        y1, y2 = max(0.0, min(height, y1)), max(0.0, min(height, y2))
        if x2 <= x1 or y2 <= y1:
            raise FileXAdapterError("invalid_bbox", "FileX bbox is outside its page")
    elif x1 < 0 or y1 < 0 or x2 > width or y2 > height:
        raise FileXAdapterError("invalid_bbox", "FileX bbox exceeds its page")
    return {
        "x": x1 / width,
        "y": y1 / height,
        "w": (x2 - x1) / width,
        "h": (y2 - y1) / height,
    }


def _validate_execution_identity(
    result: Mapping[str, object], *, requested_provider: str
) -> tuple[str, str]:
    metrics = result.get("metrics")
    if not isinstance(metrics, Mapping):
        raise FileXAdapterError(
            "missing_provenance", "FileX did not emit provider metrics"
        )
    provider = metrics.get("provider")
    if provider != requested_provider:
        raise FileXAdapterError(
            "provider_fallback", "FileX provider fallback is forbidden"
        )
    provider_version = metrics.get("provider_version")
    if not isinstance(provider_version, str) or not provider_version.strip():
        raise FileXAdapterError(
            "missing_provenance", "FileX did not emit a provider version"
        )
    cache = metrics.get("cache")
    if not isinstance(cache, Mapping) or cache.get("status") != "bypass":
        raise FileXAdapterError(
            "cache_not_bypassed", "FileX benchmark execution used cache"
        )
    status = metrics.get("status")
    if status not in (None, "success"):
        raise FileXAdapterError(
            "partial_filex_output", "FileX reported a partial parse failure"
        )
    for section_name, count_name in (
        ("work", "failed"),
        ("error", "count"),
        ("model", "timeout_count"),
    ):
        section = metrics.get(section_name)
        if isinstance(section, Mapping) and section.get(count_name) not in (None, 0):
            raise FileXAdapterError(
                "partial_filex_output", "FileX reported a partial parse failure"
            )
    for failure_key in ("errors", "vlm_errors", "failed_pages"):
        failure = metrics.get(failure_key)
        if failure not in (None, [], {}, "", 0):
            raise FileXAdapterError(
                "partial_filex_output", "FileX reported a partial parse failure"
            )
    return provider_version.strip(), "bypass"


def _validated_source(spec: ParseBenchTaskSpec, workspace_root: Path) -> Path:
    try:
        source_path = spec.source.runtime_path.resolve(strict=True)
    except OSError as exc:
        raise FileXAdapterError(
            "invalid_source", "ParseBench source is unreadable"
        ) from exc
    input_root = (workspace_root / "input").resolve()
    if not source_path.is_relative_to(input_root) or not source_path.is_file():
        raise FileXAdapterError(
            "invalid_source_path",
            "ParseBench source must be a file under workspace/input",
        )
    try:
        size = source_path.stat().st_size
        digest = _sha256_file(source_path)
    except OSError as exc:
        raise FileXAdapterError(
            "invalid_source", "ParseBench source is unreadable"
        ) from exc
    if size != spec.source.size or digest != spec.source.sha256:
        raise FileXAdapterError(
            "source_integrity_mismatch", "ParseBench source integrity check failed"
        )
    return source_path


def _resolve_filex_artifact(
    raw_path: object, workspace_root: Path, description: str
) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise FileXAdapterError(
            "missing_filex_artifact", f"FileX did not emit its {description} artifact"
        )
    path = Path(raw_path)
    if not path.is_absolute():
        path = workspace_root / path
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise FileXAdapterError(
            "missing_filex_artifact", f"FileX {description} artifact is missing"
        ) from exc
    if not resolved.is_relative_to(workspace_root) or not resolved.is_file():
        raise FileXAdapterError(
            "unsafe_filex_artifact",
            f"FileX {description} artifact escaped its workspace",
        )
    return resolved


def _file_type(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if not suffix or not re.fullmatch(r"[a-z0-9]{1,16}", suffix):
        raise FileXAdapterError("invalid_file_type", "source file type is invalid")
    return suffix


def _artifact_evidence(path: str, content: bytes) -> dict[str, object]:
    return {
        "path": path,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _require_exact_fields(
    payload: Mapping[str, object], expected: frozenset[str], description: str
) -> None:
    missing = expected.difference(payload)
    extras = set(payload).difference(expected)
    if missing:
        raise FileXAdapterError(
            "invalid_task_spec", f"{description} is missing a required field"
        )
    if extras:
        raise FileXAdapterError(
            "unexpected_task_field", f"{description} contains an unexpected field"
        )


def _absolute_path(value: Path, name: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    return path.resolve()


def _validate_logical_profile(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 128
        or any(ord(character) < 33 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{name} must be a bounded logical identity")


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FileXAdapterError("invalid_document_ir", f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise FileXAdapterError("invalid_document_ir", f"{name} must be finite")
    return number


def _positive_finite(value: object, name: str) -> float:
    number = _finite_number(value, name)
    if number <= 0:
        raise FileXAdapterError("invalid_document_ir", f"{name} must be positive")
    return number


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FileXAdapterError(
            "invalid_document_ir", f"{name} must be a non-negative integer"
        )
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_text(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_json_bytes(value: object, *, newline: bool = False) -> bytes:
    content = _canonical_json_text(value).encode("utf-8")
    return content + (b"\n" if newline else b"")


__all__ = (
    "DEFAULT_ARTIFACTS_ROOT",
    "DEFAULT_LLM_MODEL_PROFILE",
    "DEFAULT_PROVIDER",
    "DEFAULT_TASK_SPEC_PATH",
    "DEFAULT_VLM_MODEL_PROFILE",
    "FileXAdapterError",
    "FileXExecutionOptions",
    "FileXRunRequest",
    "ParseBenchTaskSource",
    "ParseBenchTaskSpec",
    "SubprocessFileXRunner",
    "execute_filex_parsebench",
    "load_parsebench_task_spec",
    "normalize_filex_document_ir",
)
