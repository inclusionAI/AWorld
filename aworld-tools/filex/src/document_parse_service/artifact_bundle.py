"""Create self-verifying artifact bundles from successful FileX parses.

The bundle is a FileX CLI contract.  It deliberately has no agent, harness,
benchmark, or scorer dependency, so any caller can request the same durable
artifacts and verify their provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .parse_output_export import document_ir_to_parse_output

ARTIFACT_BUNDLE_SCHEMA = "filex.artifact-bundle/v1"
FILEX_PROVENANCE_SCHEMA = "filex.provenance/v1"
LAYOUT_FORMATS = frozenset({"document-ir", "parse-output"})


def prepare_artifact_destination(raw_destination: str | Path) -> Path:
    """Create a bundle directory and invalidate any prior success receipt.

    Call this before parsing starts.  The other files are intentionally kept as
    diagnostics, but without ``result.json`` they cannot represent a successful
    current attempt.
    """

    supplied = Path(raw_destination).expanduser()
    if supplied.exists() and (supplied.is_symlink() or not supplied.is_dir()):
        raise ValueError(f"artifact destination is not a regular directory: {supplied}")
    destination = supplied.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "result.json").unlink(missing_ok=True)
    return destination


def export_artifact_bundle(
    *,
    destination: str | Path,
    source: str | Path,
    markdown: str | Path,
    document_ir: str | Path,
    filex_response: dict[str, Any],
    layout_format: str,
) -> dict[str, Any]:
    """Write a FileX artifact bundle and commit ``result.json`` last."""

    if layout_format not in LAYOUT_FORMATS:
        raise ValueError("layout format must be document-ir or parse-output")
    destination_path = prepare_artifact_destination(destination)
    if (
        not isinstance(filex_response, dict)
        or filex_response.get("success") is not True
    ):
        raise ValueError("artifact bundle requires a successful FileX response")
    source_path = _regular_file(source, "source")
    markdown_path = _regular_file(markdown, "Markdown")
    document_ir_path = _regular_file(document_ir, "Document IR")

    source_bytes = source_path.read_bytes()
    markdown_bytes = markdown_path.read_bytes()
    original_ir_bytes = document_ir_path.read_bytes()
    document_ir_value = _load_document_ir(original_ir_bytes)

    if layout_format == "parse-output":
        try:
            markdown_text = markdown_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("FileX Markdown is not valid UTF-8") from exc
        layout_value = document_ir_to_parse_output(
            document_ir_value,
            markdown=markdown_text,
            example_id=str(filex_response.get("task_id") or source_path.stem),
        )
    else:
        layout_value = document_ir_value
    layout_bytes = _canonical_json_bytes(layout_value)

    document_output = destination_path / "document.md"
    layout_output = destination_path / "layout.json"
    result_output = destination_path / "result.json"
    bundle: dict[str, Any] = {
        "schema_version": ARTIFACT_BUNDLE_SCHEMA,
        "status": "succeeded",
        "layout_format": layout_format,
        "source": _artifact_entry(source_path, source_bytes),
        "artifacts": {
            "document": _artifact_entry(document_output, markdown_bytes),
            "layout": _artifact_entry(layout_output, layout_bytes),
        },
        # Preserve the exact successful control response before CLI bundle fields
        # are added.  Its canonical hash is bound by the provenance receipt.
        "filex": filex_response,
    }

    if layout_format == "parse-output":
        raw_ir_output = destination_path / "document-ir.json"
        bundle["artifacts"]["document_ir"] = _artifact_entry(
            raw_ir_output, original_ir_bytes
        )
        bundle["filex_provenance"] = _filex_provenance(
            filex_response=filex_response,
            source_bytes=source_bytes,
            markdown_bytes=markdown_bytes,
            layout_bytes=layout_bytes,
            document_ir_bytes=original_ir_bytes,
        )
        _atomic_write(raw_ir_output, original_ir_bytes)

    _atomic_write(document_output, markdown_bytes)
    _atomic_write(layout_output, layout_bytes)
    # This receipt is the commit marker and must always be written last.
    _atomic_write(result_output, _canonical_json_bytes(bundle))
    return {
        "artifacts_dir": str(destination_path),
        "artifact_result": str(result_output),
        "layout_format": layout_format,
        **(
            {"filex_provenance": bundle["filex_provenance"]}
            if layout_format == "parse-output"
            else {}
        ),
    }


def _regular_file(raw_path: str | Path, label: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"FileX {label} file does not exist: {path}")
    return path


def _artifact_entry(path: Path, content: bytes) -> dict[str, Any]:
    return {
        "path": str(path),
        "size": len(content),
        "sha256": _sha256(content),
    }


def _filex_provenance(
    *,
    filex_response: dict[str, Any],
    source_bytes: bytes,
    markdown_bytes: bytes,
    layout_bytes: bytes,
    document_ir_bytes: bytes,
) -> dict[str, Any]:
    metrics = filex_response.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("FileX response is missing provider metrics")
    provider = str(metrics.get("provider") or "").strip()
    if not provider:
        raise ValueError("FileX response is missing the effective provider")
    task_id = str(filex_response.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("FileX response is missing task_id provenance")
    return {
        "schema_version": FILEX_PROVENANCE_SCHEMA,
        "status": "succeeded",
        "producer": "filex",
        "exporter": "filex-cli",
        "provider": provider,
        "provider_version": str(metrics.get("provider_version") or "").strip(),
        "task_id": task_id,
        "layout_format": "parse-output",
        "source_sha256": _sha256(source_bytes),
        "document_ir_sha256": _sha256(document_ir_bytes),
        "document_sha256": _sha256(markdown_bytes),
        "layout_sha256": _sha256(layout_bytes),
        "filex_response_sha256": _sha256(_canonical_json_bytes(filex_response)),
    }


def _load_document_ir(content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            content,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, ValueError) as exc:
        raise ValueError("FileX Document IR is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("FileX Document IR must be a JSON object")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate FileX Document IR JSON key")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> None:
    raise ValueError("nonfinite FileX Document IR JSON number")


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("FileX response or artifact contains invalid JSON") from exc


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "ARTIFACT_BUNDLE_SCHEMA",
    "FILEX_PROVENANCE_SCHEMA",
    "LAYOUT_FORMATS",
    "export_artifact_bundle",
    "prepare_artifact_destination",
]
