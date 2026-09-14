"""Validate deterministic FileX artifacts for the public ParseBench task contract.

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
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from .contracts import DATASET_REVISION, SCORER_REVISION

TASK_SPEC_SCHEMA_VERSION = "aworld-parsebench-task/v1"
RESULT_SCHEMA_VERSION = "aworld-parsebench-filex-result/v2"
FILEX_DOCUMENT_IR_SCHEMA_VERSION = "filex-document-ir-v2"
FILEX_COORDINATE_SYSTEM = "pixel_top_left_xyxy"
DEFAULT_TASK_SPEC_PATH = Path("/workspace/parsebench-task.json")
DEFAULT_ARTIFACTS_ROOT = Path("/logs/artifacts")
DEFAULT_PROVIDER = "paddle_ocr"
DEFAULT_VLM_MODEL_PROFILE = "ai_cloud_Kimi_k26_pgc"
DEFAULT_LAYOUT_MODEL_DIR = Path(
    "/opt/skillsbench-agent-frameworks/paddlex-models/PP-DocLayoutV3"
)
LAYOUT_MODEL_NAME = "PP-DocLayoutV3"
LAYOUT_MODEL_MANIFEST_SHA256 = (
    "sha256:effeb59959c7da305dd1d0e74382e82dfa82f10b8ffb9be25b20a2b21d5bad6f"
)
FILEX_METRICS_SCHEMA_VERSION = "1.0"

_LAYOUT_MODEL_FILES = {
    "inference.json": (
        1_196_890,
        "sha256:2b68367c5b312a03de5a6e1642c597c8f95165a7e40cd59c6700cf4a5042f4fd",
    ),
    "inference.pdiparams": (
        130_806_572,
        "sha256:70bd316b0582769ec968829fd1feb1a6a58b7c941b938327e551b6b12b45c137",
    ),
    "inference.yml": (
        1_482,
        "sha256:506fcfac13b3b546ae40d7886b44126420f392adb694e3f8bb6a6286a1f90fdc",
    ),
}

_TASK_FIELDS = frozenset(
    {"schema_version", "task_id", "dataset_revision", "scorer_revision", "source"}
)
_SOURCE_FIELDS = frozenset({"runtime_path", "size", "sha256", "page"})
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROVIDER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PROVIDER_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_REQUESTED_PROVIDER_VERSIONS = {"paddle_ocr": "paddleocr-vl-1.6"}
_MAX_TASK_SPEC_BYTES = 64 * 1024
_MAX_RUNNER_OUTPUT_BYTES = 4 * 1024 * 1024
_MAX_RUNNER_ERROR_BYTES = 4 * 1024 * 1024
_PROCESS_TERM_GRACE_SECONDS = 1.0


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
    vlm_model_profile: str


@dataclass(frozen=True, slots=True)
class FileXRunResult:
    payload: Mapping[str, object]
    resolved_model_name: str
    layout_model_name: str = LAYOUT_MODEL_NAME
    layout_model_manifest_sha256: str = LAYOUT_MODEL_MANIFEST_SHA256


class FileXRunner(Protocol):
    def run(self, request: FileXRunRequest) -> FileXRunResult: ...


class SubprocessFileXRunner:
    """Invoke FileX without a shell and with a hard timeout."""

    def __init__(
        self,
        *,
        executable: str = "filex",
        environment: Mapping[str, str] | None = None,
        max_stdout_bytes: int = _MAX_RUNNER_OUTPUT_BYTES,
        max_stderr_bytes: int = _MAX_RUNNER_ERROR_BYTES,
    ) -> None:
        if not isinstance(executable, str) or not executable.strip():
            raise ValueError("executable must be a non-empty string")
        self._executable = executable.strip()
        self._environment = None if environment is None else dict(environment)
        self._max_stdout_bytes = _positive_limit(max_stdout_bytes, "max_stdout_bytes")
        self._max_stderr_bytes = _positive_limit(max_stderr_bytes, "max_stderr_bytes")

    def run(self, request: FileXRunRequest) -> FileXRunResult:
        if request.no_cache is not True:
            raise FileXAdapterError(
                "cache_not_bypassed", "FileX benchmark execution requires no-cache"
            )
        process_env = (
            os.environ.copy() if self._environment is None else dict(self._environment)
        )
        base_url, model_name, http_model_name, api_key = _resolved_gateway_vllm(
            process_env
        )
        paddle_model_name = str(
            process_env.get("FILEX_PADDLE_OCR_VL_REC_API_MODEL_NAME")
            or http_model_name
        ).strip()
        layout_model_dir = _validated_layout_model_dir(process_env)
        process_env.pop("LLM_API_KEY", None)
        process_env["PADDLE_PDX_CACHE_HOME"] = "/tmp/filex-paddlex-cache"
        env_content: dict[str, object] = {
            "filex_parse_provider": request.provider,
            "filex_cache_enabled": False,
            "filex_no_cache": True,
            "paddle_ocr_pipeline_version": "v1.6",
            "paddle_ocr_layout_detection_model_name": LAYOUT_MODEL_NAME,
            "paddle_ocr_layout_detection_model_dir": str(layout_model_dir),
            "paddle_ocr_vl_rec_backend": "vllm-server",
            "paddle_ocr_vl_rec_max_concurrency": 1,
            "paddle_ocr_vlm_max_retries": 3,
            "paddle_ocr_vlm_retry_base_delay_ms": 500,
            "paddle_ocr_vlm_retry_max_delay_ms": 8000,
            "paddle_ocr_use_doc_orientation_classify": False,
            "paddle_ocr_use_doc_unwarping": False,
            "paddle_ocr_use_layout_detection": True,
            # ParseBench's chart dimension requires chart blocks to be sent to
            # the protected remote VLM.  Disabling this makes PaddleOCR-VL
            # classify charts as image-only blocks and drops their structured
            # data from the generated markdown.
            "paddle_ocr_use_chart_recognition": True,
            "paddle_ocr_use_seal_recognition": False,
            "paddle_ocr_use_ocr_for_image_block": True,
            "paddle_ocr_format_block_content": False,
            "paddle_ocr_merge_layout_blocks": True,
            "paddle_ocr_use_queues": False,
            "gateway_vllm": {
                "base_url": base_url,
                "model_name": model_name,
                "http_model_name": http_model_name,
            },
        }
        command = _filex_command(self._executable, request, env_content)
        process_env["FILEX_WORKSPACE_ROOT"] = str(request.workspace_root)
        # FileX reads this key without ever placing the credential in argv,
        # task material, logs, or result artifacts.
        process_env["GATEWAY_VLLM_API_KEY"] = api_key
        stdout, _stderr, returncode = _run_bounded_process(
            command,
            environment=process_env,
            timeout=request.timeout_seconds,
            max_stdout_bytes=self._max_stdout_bytes,
            max_stderr_bytes=self._max_stderr_bytes,
        )
        if returncode != 0:
            raise FileXAdapterError(
                "filex_execution_failed",
                f"FileX execution failed with exit status {returncode}",
            )
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise FileXAdapterError(
                "invalid_filex_output", "FileX returned invalid JSON control output"
            ) from exc
        if not isinstance(payload, dict):
            raise FileXAdapterError(
                "invalid_filex_output", "FileX control output must be a JSON object"
            )
        return FileXRunResult(
            payload=payload,
            resolved_model_name=paddle_model_name,
            layout_model_name=LAYOUT_MODEL_NAME,
            layout_model_manifest_sha256=LAYOUT_MODEL_MANIFEST_SHA256,
        )


def _filex_command(
    executable: str,
    request: FileXRunRequest,
    env_content: Mapping[str, object],
) -> list[str]:
    command = [
        executable,
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
    return command


def _resolved_gateway_vllm(
    environment: Mapping[str, str],
) -> tuple[str, str, str, str]:
    base_url = str(
        environment.get("GATEWAY_VLLM_BASE_URL")
        or environment.get("LLM_BASE_URL")
        or ""
    ).strip()
    model_name = str(
        environment.get("GATEWAY_VLLM_MODEL_NAME")
        or environment.get("LLM_MODEL_NAME")
        or ""
    ).strip()
    http_model_name = str(
        environment.get("GATEWAY_VLLM_HTTP_MODEL_NAME") or model_name
    ).strip()
    api_key = str(
        environment.get("GATEWAY_VLLM_API_KEY")
        or environment.get("LLM_API_KEY")
        or ""
    )
    if not base_url or not model_name or not api_key.strip():
        raise FileXAdapterError(
            "missing_model_configuration",
            "protected FileX model configuration is incomplete",
        )
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise FileXAdapterError(
            "invalid_model_configuration", "LLM_BASE_URL is not a safe HTTP endpoint"
        )
    try:
        _validate_logical_profile(model_name, "LLM_MODEL_NAME")
        _validate_logical_profile(http_model_name, "GATEWAY_VLLM_HTTP_MODEL_NAME")
    except ValueError as exc:
        raise FileXAdapterError(
            "invalid_model_configuration", "LLM_MODEL_NAME is invalid"
        ) from exc
    if len(api_key) > 16_384 or any(ord(character) < 32 for character in api_key):
        raise FileXAdapterError("invalid_model_configuration", "LLM_API_KEY is invalid")
    return base_url, model_name, http_model_name, api_key


def _validated_layout_model_dir(environment: Mapping[str, str]) -> Path:
    configured = str(
        environment.get("AWORLD_PARSEBENCH_LAYOUT_MODEL_DIR")
        or DEFAULT_LAYOUT_MODEL_DIR
    ).strip()
    candidate = Path(configured)
    if not candidate.is_absolute():
        raise FileXAdapterError(
            "invalid_layout_model", "ParseBench layout model path must be absolute"
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise FileXAdapterError(
            "missing_layout_model", "pinned ParseBench layout model is unavailable"
        ) from exc
    if candidate != resolved or not resolved.is_dir():
        raise FileXAdapterError(
            "invalid_layout_model", "pinned ParseBench layout model path is unsafe"
        )
    for name, (expected_size, expected_sha256) in _LAYOUT_MODEL_FILES.items():
        artifact = resolved / name
        try:
            artifact_stat = artifact.lstat()
            if (
                not stat.S_ISREG(artifact_stat.st_mode)
                or artifact_stat.st_size != expected_size
                or _sha256_file(artifact) != expected_sha256
            ):
                raise FileXAdapterError(
                    "layout_model_mismatch",
                    "pinned ParseBench layout model failed integrity validation",
                )
        except FileXAdapterError:
            raise
        except OSError as exc:
            raise FileXAdapterError(
                "layout_model_mismatch",
                "pinned ParseBench layout model failed integrity validation",
            ) from exc
    return resolved


def _run_bounded_process(
    command: list[str],
    *,
    environment: Mapping[str, str],
    timeout: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
) -> tuple[bytes, bytes, int]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(environment),
        shell=False,
        start_new_session=True,
    )
    if process.stdout is None or process.stderr is None:  # pragma: no cover
        _terminate_process_group(process)
        raise FileXAdapterError(
            "filex_execution_failed", "FileX process pipes were not created"
        )
    streams = {
        process.stdout: ("stdout", max_stdout_bytes),
        process.stderr: ("stderr", max_stderr_bytes),
    }
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_group(process)
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _events in selector.select(min(remaining, 0.1)):
                stream = key.fileobj
                name, limit = streams[stream]
                try:
                    chunk = os.read(stream.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                buffer = buffers[name]
                if len(buffer) + len(chunk) > limit:
                    _terminate_process_group(process)
                    raise FileXAdapterError(
                        f"filex_{name}_too_large",
                        f"FileX {name} exceeds the size limit",
                    )
                buffer.extend(chunk)
        remaining = max(0.0, deadline - time.monotonic())
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _terminate_process_group(process)
            raise subprocess.TimeoutExpired(command, timeout) from None
    except BaseException:
        if process.poll() is None:
            _terminate_process_group(process)
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return bytes(buffers["stdout"]), bytes(buffers["stderr"]), returncode


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    process_group_id = process.pid
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        try:
            process.wait(timeout=_PROCESS_TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
    # A child can retain the process group's pipes after the direct FileX
    # process exits, so address the group even when the parent is already reaped.
    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.wait()


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
    options.artifacts_root.mkdir(parents=True, exist_ok=True)
    _invalidate_result_marker(options.artifacts_root)
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
        vlm_model_profile=options.vlm_model_profile,
    )
    selected_runner = runner or SubprocessFileXRunner()
    try:
        run_result = selected_runner.run(request)
    except subprocess.TimeoutExpired as exc:
        raise FileXAdapterError("filex_timeout", "FileX execution timed out") from exc
    except FileXAdapterError:
        raise
    except Exception as exc:
        raise FileXAdapterError(
            "filex_execution_failed", "FileX execution failed"
        ) from exc
    if not isinstance(run_result, FileXRunResult):
        raise FileXAdapterError(
            "invalid_runner_result", "FileX runner did not return execution evidence"
        )
    raw_result = run_result.payload
    if not isinstance(raw_result, Mapping) or raw_result.get("success") is not True:
        raise FileXAdapterError(
            "filex_execution_failed", "FileX execution did not succeed"
        )

    provider_version, model_name, cache_status, timing_ms = (
        _validate_execution_identity(
            raw_result,
            requested_provider=options.provider,
            resolved_model_name=run_result.resolved_model_name,
        )
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
                "requested_model_profile": options.vlm_model_profile,
                "resolved_model_name": model_name,
                "layout_model_name": run_result.layout_model_name,
                "layout_model_manifest_sha256": (
                    run_result.layout_model_manifest_sha256
                ),
                "document_ir_schema_version": FILEX_DOCUMENT_IR_SCHEMA_VERSION,
                "coordinate_transform": "pixel-xyxy-to-pixel-xywh",
                "bbox_policy": "clip" if options.clip_bboxes else "fail_closed",
            },
        },
        "timing_ms": timing_ms,
    }
    result_bytes = _canonical_json_bytes(result, newline=True)
    _publish_artifacts(
        artifacts_root=options.artifacts_root,
        document_bytes=document_bytes,
        layout_bytes=layout_bytes,
        result_bytes=result_bytes,
        expected_task_id=spec.task_id,
        expected_source=spec.source,
    )
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
            if confidence is None:
                segment["confidence"] = 1.0
            else:
                score = _finite_number(confidence, "confidence")
                if not 0 <= score <= 1:
                    raise FileXAdapterError(
                        "invalid_document_ir", "confidence must be between zero and one"
                    )
                segment["confidence"] = score
            item: dict[str, Any] = {
                "type": "table" if label == "table" else "text",
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
    "caption": "caption",
    "figure-title": "caption",
    "footnote": "footnote",
    "vision-footnote": "footnote",
    "formula": "formula",
    "display-formula": "formula",
    "inline-formula": "formula",
    "list-item": "list-item",
    "list-items": "list-item",
    "page-footer": "page-footer",
    "footer": "page-footer",
    "footer-image": "page-footer",
    "page-header": "page-header",
    "header": "page-header",
    "header-image": "page-header",
    "picture": "picture",
    "image": "picture",
    "chart": "picture",
    "seal": "picture",
    "section-header": "section-header",
    "paragraph-title": "section-header",
    "heading": "section-header",
    "table": "table",
    "text": "text",
    "content": "text",
    "abstract": "text",
    "reference": "text",
    "reference-content": "text",
    "aside-text": "text",
    "vertical-text": "text",
    "number": "text",
    "formula-number": "text",
    "title": "title",
    "doc-title": "title",
    "document-index": "document-index",
    "code": "code",
    "algorithm": "code",
    "checkbox-selected": "checkbox-selected",
    "checkbox-unselected": "checkbox-unselected",
    "form": "form",
    "key-value-region": "key-value-region",
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
    return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


def _validate_execution_identity(
    result: Mapping[str, object],
    *,
    requested_provider: str,
    resolved_model_name: str,
) -> tuple[str, str, str, dict[str, float]]:
    metrics = result.get("metrics")
    if not isinstance(metrics, Mapping):
        raise FileXAdapterError(
            "missing_provenance", "FileX did not emit provider metrics"
        )
    if metrics.get("schema_version") != FILEX_METRICS_SCHEMA_VERSION:
        raise FileXAdapterError(
            "metrics_schema_mismatch", "FileX metrics schema is not pinned"
        )
    provider = metrics.get("provider")
    if provider != requested_provider:
        raise FileXAdapterError(
            "provider_fallback", "FileX provider fallback is forbidden"
        )
    provider_version = metrics.get("provider_version")
    if (
        not isinstance(provider_version, str)
        or _PROVIDER_VERSION_PATTERN.fullmatch(provider_version) is None
    ):
        raise FileXAdapterError(
            "missing_provenance", "FileX did not emit a provider version"
        )
    requested_provider_version = _REQUESTED_PROVIDER_VERSIONS.get(requested_provider)
    if (
        requested_provider_version is None
        or metrics.get("requested_provider_version") != requested_provider_version
    ):
        raise FileXAdapterError(
            "provider_version_mismatch",
            "FileX requested provider contract version is not pinned",
        )
    cache = metrics.get("cache")
    if not isinstance(cache, Mapping) or cache.get("status") != "bypass":
        raise FileXAdapterError(
            "cache_not_bypassed", "FileX benchmark execution used cache"
        )
    if metrics.get("requested_provider") != requested_provider:
        raise FileXAdapterError(
            "missing_provenance", "FileX did not preserve the requested provider"
        )
    status = metrics.get("status")
    if status != "success":
        raise FileXAdapterError(
            "partial_filex_output", "FileX reported a partial parse failure"
        )
    for section_name, count_name in (
        ("work", "failed"),
        ("error", "count"),
        ("model", "timeout_count"),
    ):
        section = metrics.get(section_name)
        count = section.get(count_name) if isinstance(section, Mapping) else None
        if isinstance(count, bool) or not isinstance(count, int) or count != 0:
            raise FileXAdapterError(
                "partial_filex_output", "FileX reported a partial parse failure"
            )
    model = metrics.get("model")
    if not isinstance(model, Mapping) or model.get("name") != resolved_model_name:
        raise FileXAdapterError(
            "model_identity_mismatch", "FileX resolved model identity does not match"
        )
    timings = metrics.get("timings_ms")
    if not isinstance(timings, Mapping):
        raise FileXAdapterError("missing_provenance", "FileX timings are missing")
    timing_ms: dict[str, float] = {}
    for key in ("initialization", "model_wait", "parse", "total"):
        timing_ms[key] = _nonnegative_finite(timings.get(key), f"timings_ms.{key}")
    return provider_version.strip(), resolved_model_name, "bypass", timing_ms


def _validated_source(spec: ParseBenchTaskSpec, workspace_root: Path) -> Path:
    declared_path = spec.source.runtime_path
    try:
        workspace_relative = declared_path.relative_to(DEFAULT_TASK_SPEC_PATH.parent)
    except ValueError:
        candidate = declared_path
    else:
        # Authored Dataset tasks use container-stable /workspace paths.  Map
        # those paths onto an explicitly selected local workspace for bounded
        # smoke diagnostics without rewriting the signed public task contract.
        candidate = workspace_root / workspace_relative
    try:
        source_path = candidate.resolve(strict=True)
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


def validate_parsebench_artifacts(
    *,
    result_path: Path,
    markdown_path: Path,
    layout_path: Path,
    expected_task_id: str | None = None,
    expected_source: ParseBenchTaskSource | None = None,
) -> dict[str, Any]:
    """Validate the result commit marker and its two content-addressed artifacts."""

    result_bytes = _read_regular_file(result_path, "result")
    markdown_bytes = _read_regular_file(markdown_path, "Markdown")
    layout_bytes = _read_regular_file(layout_path, "layout")
    try:
        markdown = markdown_bytes.decode("utf-8")
        result = json.loads(
            result_bytes.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
        layout = json.loads(
            layout_bytes.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise FileXAdapterError(
            "invalid_result_artifact",
            "ParseBench result artifacts contain invalid JSON",
        ) from exc
    if not isinstance(result, dict) or not isinstance(layout, dict):
        raise FileXAdapterError(
            "invalid_result_artifact",
            "ParseBench result artifacts must be JSON objects",
        )
    _exact_result_fields(
        result,
        {
            "schema_version",
            "status",
            "task_id",
            "artifacts",
            "provenance",
            "timing_ms",
        },
        "result",
    )
    if (
        result["schema_version"] != RESULT_SCHEMA_VERSION
        or result["status"] != "succeeded"
    ):
        raise FileXAdapterError(
            "invalid_result_artifact", "ParseBench result marker is not successful"
        )
    task_id = result["task_id"]
    if not isinstance(task_id, str) or _TASK_ID_PATTERN.fullmatch(task_id) is None:
        raise FileXAdapterError("invalid_result_artifact", "result task_id is invalid")
    if expected_task_id is not None and task_id != expected_task_id:
        raise FileXAdapterError("result_mismatch", "result task_id does not match")

    artifacts = result["artifacts"]
    if not isinstance(artifacts, Mapping):
        raise FileXAdapterError(
            "invalid_result_artifact", "result artifacts are invalid"
        )
    _exact_result_fields(artifacts, {"document", "layout"}, "result artifacts")
    _validate_artifact_evidence(
        artifacts["document"],
        expected_path="/logs/artifacts/document.md",
        content=markdown_bytes,
    )
    _validate_artifact_evidence(
        artifacts["layout"],
        expected_path="/logs/artifacts/layout.json",
        content=layout_bytes,
    )

    provenance = result["provenance"]
    if not isinstance(provenance, Mapping):
        raise FileXAdapterError(
            "invalid_result_artifact", "result provenance is invalid"
        )
    _exact_result_fields(
        provenance,
        {"dataset_revision", "scorer_revision", "source", "filex"},
        "result provenance",
    )
    if (
        provenance["dataset_revision"] != DATASET_REVISION
        or provenance["scorer_revision"] != SCORER_REVISION
    ):
        raise FileXAdapterError("revision_mismatch", "result revisions are not pinned")
    source = provenance["source"]
    if not isinstance(source, Mapping):
        raise FileXAdapterError("invalid_result_artifact", "result source is invalid")
    _exact_result_fields(
        source, {"runtime_path", "size", "sha256", "page"}, "result source"
    )
    if (
        not isinstance(source["runtime_path"], str)
        or not Path(source["runtime_path"]).is_absolute()
        or isinstance(source["size"], bool)
        or not isinstance(source["size"], int)
        or source["size"] <= 0
        or not isinstance(source["sha256"], str)
        or _SHA256_PATTERN.fullmatch(source["sha256"]) is None
        or (
            source["page"] is not None
            and (
                isinstance(source["page"], bool)
                or not isinstance(source["page"], int)
                or source["page"] < 1
            )
        )
    ):
        raise FileXAdapterError(
            "invalid_result_artifact", "result source identity is invalid"
        )
    if expected_source is not None and source != {
        "runtime_path": str(expected_source.runtime_path),
        "size": expected_source.size,
        "sha256": expected_source.sha256,
        "page": expected_source.page,
    }:
        raise FileXAdapterError("result_mismatch", "result source does not match")

    filex = provenance["filex"]
    if not isinstance(filex, Mapping):
        raise FileXAdapterError(
            "invalid_result_artifact", "FileX provenance is invalid"
        )
    _exact_result_fields(
        filex,
        {
            "provider",
            "provider_version",
            "fallback_allowed",
            "cache",
            "requested_model_profile",
            "resolved_model_name",
            "layout_model_name",
            "layout_model_manifest_sha256",
            "document_ir_schema_version",
            "coordinate_transform",
            "bbox_policy",
        },
        "FileX provenance",
    )
    for identity_key in (
        "provider",
        "provider_version",
        "requested_model_profile",
        "resolved_model_name",
        "layout_model_name",
    ):
        if not isinstance(filex[identity_key], str) or not filex[identity_key].strip():
            raise FileXAdapterError(
                "invalid_result_artifact", "FileX identity is incomplete"
            )
    if (
        filex["layout_model_name"] != LAYOUT_MODEL_NAME
        or filex["layout_model_manifest_sha256"] != LAYOUT_MODEL_MANIFEST_SHA256
    ):
        raise FileXAdapterError(
            "invalid_result_artifact", "FileX layout model identity is invalid"
        )
    if filex["fallback_allowed"] is not False or filex["cache"] != "bypass":
        raise FileXAdapterError(
            "invalid_result_artifact", "FileX deterministic controls are invalid"
        )
    if filex["document_ir_schema_version"] != FILEX_DOCUMENT_IR_SCHEMA_VERSION:
        raise FileXAdapterError(
            "invalid_result_artifact", "FileX Document IR identity is invalid"
        )
    if filex["coordinate_transform"] != "pixel-xyxy-to-pixel-xywh":
        raise FileXAdapterError(
            "invalid_result_artifact", "FileX coordinate identity is invalid"
        )
    if filex["bbox_policy"] not in {"clip", "fail_closed"}:
        raise FileXAdapterError(
            "invalid_result_artifact", "FileX bbox policy is invalid"
        )

    timing = result["timing_ms"]
    if not isinstance(timing, Mapping):
        raise FileXAdapterError("invalid_result_artifact", "result timing is invalid")
    _exact_result_fields(
        timing, {"initialization", "model_wait", "parse", "total"}, "result timing"
    )
    for key, value in timing.items():
        _nonnegative_finite(value, f"timing_ms.{key}")

    _exact_result_fields(
        layout,
        {
            "task_type",
            "example_id",
            "pipeline_name",
            "pages",
            "layout_pages",
            "markdown",
        },
        "ParseOutput",
    )
    if (
        layout["task_type"] != "parse"
        or layout["example_id"] != task_id
        or not isinstance(layout["pipeline_name"], str)
        or not layout["pipeline_name"].startswith("filex/")
        or layout["markdown"] != markdown
        or not isinstance(layout["pages"], list)
        or not isinstance(layout["layout_pages"], list)
        or not layout["layout_pages"]
    ):
        raise FileXAdapterError(
            "invalid_result_artifact", "layout is not a compatible ParseOutput"
        )
    _validate_parse_output_layout(layout)
    if expected_source is not None and expected_source.page is not None:
        pages = layout["pages"]
        if (
            len(pages) != 1
            or not isinstance(pages[0], Mapping)
            or pages[0].get("page_index") != expected_source.page - 1
        ):
            raise FileXAdapterError("result_mismatch", "layout page does not match")
    return result


def _publish_artifacts(
    *,
    artifacts_root: Path,
    document_bytes: bytes,
    layout_bytes: bytes,
    result_bytes: bytes,
    expected_task_id: str,
    expected_source: ParseBenchTaskSource,
) -> None:
    staging = Path(tempfile.mkdtemp(prefix=".parsebench-stage-", dir=artifacts_root))
    try:
        staged_document = staging / "document.md"
        staged_layout = staging / "layout.json"
        staged_result = staging / "result.json"
        _write_fsynced(staged_document, document_bytes)
        _write_fsynced(staged_layout, layout_bytes)
        _write_fsynced(staged_result, result_bytes)
        _fsync_directory(staging)
        validate_parsebench_artifacts(
            result_path=staged_result,
            markdown_path=staged_document,
            layout_path=staged_layout,
            expected_task_id=expected_task_id,
            expected_source=expected_source,
        )
        os.replace(staged_document, artifacts_root / "document.md")
        os.replace(staged_layout, artifacts_root / "layout.json")
        _fsync_directory(artifacts_root)
        # The result is the commit marker and is always published last.
        os.replace(staged_result, artifacts_root / "result.json")
        _fsync_directory(artifacts_root)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _invalidate_result_marker(artifacts_root: Path) -> None:
    marker = artifacts_root / "result.json"
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    _fsync_directory(artifacts_root)


def _write_fsynced(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_regular_file(path: Path, description: str) -> bytes:
    path = Path(path)
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise FileXAdapterError(
                    "invalid_result_artifact",
                    f"ParseBench {description} artifact is not a regular file",
                )
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except FileXAdapterError:
        raise
    except OSError as exc:
        raise FileXAdapterError(
            "invalid_result_artifact",
            f"ParseBench {description} artifact is unreadable",
        ) from exc


def _validate_artifact_evidence(
    evidence: object, *, expected_path: str, content: bytes
) -> None:
    if not isinstance(evidence, Mapping):
        raise FileXAdapterError(
            "invalid_result_artifact", "artifact evidence is invalid"
        )
    _exact_result_fields(evidence, {"path", "size", "sha256"}, "artifact evidence")
    if (
        evidence["path"] != expected_path
        or evidence["size"] != len(content)
        or evidence["sha256"] != hashlib.sha256(content).hexdigest()
    ):
        raise FileXAdapterError(
            "artifact_checksum_mismatch", "artifact checksum does not match"
        )


def _validate_parse_output_layout(layout: Mapping[str, object]) -> None:
    pages = layout["pages"]
    layout_pages = layout["layout_pages"]
    if not isinstance(pages, list) or not isinstance(layout_pages, list):
        raise FileXAdapterError(
            "invalid_result_artifact", "ParseOutput pages are invalid"
        )
    if not pages or len(pages) != len(layout_pages):
        raise FileXAdapterError(
            "invalid_result_artifact", "ParseOutput page cardinality is invalid"
        )
    allowed_labels = frozenset(_LABELS.values())
    seen_indices: set[int] = set()
    for page, layout_page in zip(pages, layout_pages, strict=True):
        if not isinstance(page, Mapping) or not isinstance(layout_page, Mapping):
            raise FileXAdapterError(
                "invalid_result_artifact", "ParseOutput page is invalid"
            )
        _exact_result_fields(page, {"page_index", "markdown"}, "ParseOutput page")
        _exact_result_fields(
            layout_page,
            {"page_number", "width", "height", "md", "text", "items"},
            "ParseOutput layout page",
        )
        page_index = page["page_index"]
        if (
            isinstance(page_index, bool)
            or not isinstance(page_index, int)
            or page_index < 0
            or page_index in seen_indices
            or layout_page["page_number"] != page_index + 1
            or not isinstance(page["markdown"], str)
            or layout_page["md"] != page["markdown"]
            or not isinstance(layout_page["text"], str)
        ):
            raise FileXAdapterError(
                "invalid_result_artifact", "ParseOutput page identity is invalid"
            )
        seen_indices.add(page_index)
        width = _result_positive_finite(layout_page["width"], "layout page width")
        height = _result_positive_finite(layout_page["height"], "layout page height")
        items = layout_page["items"]
        if not isinstance(items, list):
            raise FileXAdapterError(
                "invalid_result_artifact", "ParseOutput layout items are invalid"
            )
        previous_order = -1
        for item in items:
            if not isinstance(item, Mapping):
                raise FileXAdapterError(
                    "invalid_result_artifact", "ParseOutput layout item is invalid"
                )
            _exact_result_fields(
                item,
                {
                    "type",
                    "md",
                    "html",
                    "value",
                    "bbox",
                    "layout_segments",
                    "reading_order",
                },
                "ParseOutput layout item",
            )
            if (
                item["type"] not in {"text", "table"}
                or not all(
                    isinstance(item[key], str) for key in ("md", "html", "value")
                )
                or not isinstance(item["layout_segments"], list)
                or len(item["layout_segments"]) != 1
                or item["bbox"] != item["layout_segments"][0]
            ):
                raise FileXAdapterError(
                    "invalid_result_artifact", "ParseOutput layout item is invalid"
                )
            order = item["reading_order"]
            if order is not None:
                if (
                    isinstance(order, bool)
                    or not isinstance(order, int)
                    or order < previous_order
                ):
                    raise FileXAdapterError(
                        "invalid_result_artifact",
                        "ParseOutput reading order is invalid",
                    )
                previous_order = order
            _validate_result_bbox(
                item["bbox"], width=width, height=height, labels=allowed_labels
            )


def _validate_result_bbox(
    bbox: object, *, width: float, height: float, labels: frozenset[str]
) -> None:
    if not isinstance(bbox, Mapping) or set(bbox) not in (
        {"x", "y", "w", "h", "label"},
        {"x", "y", "w", "h", "label", "confidence"},
    ):
        raise FileXAdapterError(
            "invalid_result_artifact", "ParseOutput bbox is invalid"
        )
    x = _result_nonnegative_finite(bbox["x"], "bbox.x")
    y = _result_nonnegative_finite(bbox["y"], "bbox.y")
    w = _result_positive_finite(bbox["w"], "bbox.w")
    h = _result_positive_finite(bbox["h"], "bbox.h")
    if x + w > width or y + h > height or bbox["label"] not in labels:
        raise FileXAdapterError(
            "invalid_result_artifact", "ParseOutput bbox is outside its page"
        )
    if "confidence" in bbox:
        confidence = _result_nonnegative_finite(bbox["confidence"], "confidence")
        if confidence > 1:
            raise FileXAdapterError(
                "invalid_result_artifact", "ParseOutput confidence is invalid"
            )


def _result_nonnegative_finite(value: object, name: str) -> float:
    try:
        return _nonnegative_finite(value, name)
    except FileXAdapterError as exc:
        raise FileXAdapterError(
            "invalid_result_artifact", f"ParseOutput {name} is invalid"
        ) from exc


def _result_positive_finite(value: object, name: str) -> float:
    number = _result_nonnegative_finite(value, name)
    if number <= 0:
        raise FileXAdapterError(
            "invalid_result_artifact", f"ParseOutput {name} is invalid"
        )
    return number


def _exact_result_fields(
    value: Mapping[str, object], expected: set[str], description: str
) -> None:
    if set(value) != expected:
        raise FileXAdapterError(
            "invalid_result_artifact",
            f"{description} fields do not match the pinned schema",
        )


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


def _positive_limit(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FileXAdapterError("missing_provenance", f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise FileXAdapterError(
            "missing_provenance", f"{name} must be non-negative and finite"
        )
    return number


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


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
    return "sha256:" + digest.hexdigest()


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
    "DEFAULT_PROVIDER",
    "DEFAULT_TASK_SPEC_PATH",
    "DEFAULT_VLM_MODEL_PROFILE",
    "FileXAdapterError",
    "FileXExecutionOptions",
    "FileXRunRequest",
    "FileXRunResult",
    "ParseBenchTaskSource",
    "ParseBenchTaskSpec",
    "SubprocessFileXRunner",
    "execute_filex_parsebench",
    "load_parsebench_task_spec",
    "normalize_filex_document_ir",
    "validate_parsebench_artifacts",
)
