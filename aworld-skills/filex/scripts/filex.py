#!/usr/bin/env python3
"""Run FileX commands and emit agent-friendly JSON without exposing credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Use FileX from an AWorld sandbox")
    parser.add_argument(
        "--filex-bin",
        default=os.environ.get("FILEX_BIN", "filex"),
        help="FileX executable (default: FILEX_BIN or filex)",
    )
    parser.add_argument(
        "--workspace-root",
        default=os.environ.get("FILEX_WORKSPACE_ROOT", str(Path.home() / "workspace")),
        help="FileX workspace root",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    parse = commands.add_parser(
        "parse", help="Parse a local path or HTTP(S) URL to Markdown"
    )
    source = parse.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Local file inside the workspace")
    source.add_argument("--url", help="HTTP(S) file URL")
    parse.add_argument(
        "--output", help="Optional Markdown destination inside the workspace"
    )
    parse.add_argument(
        "--artifacts-dir",
        help=(
            "Optional directory for a self-verifying FileX artifact bundle. "
            "Must be inside FILEX_ARTIFACTS_ROOT (default: /logs/artifacts)."
        ),
    )
    parse.add_argument(
        "--layout-format",
        choices=("document-ir", "parse-output"),
        default=os.environ.get("FILEX_LAYOUT_FORMAT", "document-ir"),
        help="Artifact layout format (default: FILEX_LAYOUT_FORMAT or document-ir)",
    )
    parse.add_argument(
        "--file-type", help="Explicit source type; otherwise infer from the file"
    )
    parse.add_argument("--provider", help="Configured FileX provider")
    parse.add_argument(
        "--env-file", help="Credential/config JSON file inside the workspace"
    )
    parse.add_argument("--sync-mode", choices=("sync", "async"), default="sync")
    parse.add_argument(
        "--asset-reference-mode",
        choices=("remote_id", "local_path"),
        default="local_path",
    )
    parse.add_argument("--task-id", help="Optional FileX task id")
    parse.add_argument("--pages", help="One-based PDF pages, for example 1,3-5")
    parse.add_argument(
        "--page-batch-size", type=int, help="Sequential PDF page batch size"
    )
    parse.add_argument(
        "--first-batch-pages", type=int, help="Pages in the first PDF batch"
    )
    parse.add_argument("--batch-resume-id", help="Stable PDF batch resume id")
    parse.add_argument("--force-refresh", action="store_true")
    parse.add_argument("--no-cache", action="store_true")
    parse.add_argument("--mode", choices=("auto", "transcript"), default="auto")
    parse.add_argument("--language", help="Preferred transcript or ASR language")
    parse.add_argument("--allow-media-download", action="store_true")
    parse.add_argument(
        "--rights-basis",
        choices=("user-owned", "licensed", "service-permitted", "applicable-law"),
    )

    inspect = commands.add_parser(
        "inspect", help="Inspect a supported source URL without downloading media"
    )
    inspect.add_argument("--url", required=True, help="Supported source URL")

    status = commands.add_parser("status", help="Read resumable PDF batch progress")
    status.add_argument("--batch-resume-id", required=True)
    status.add_argument("--include-results", action="store_true")
    status.add_argument("--after-batch", type=int, default=0)
    status.add_argument("--max-batches", type=int, default=10)
    return parser


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _fail(message: str, *, error_type: str = "FileXError") -> int:
    _emit({"success": False, "message": message, "error_type": error_type})
    return 2


def _inside_workspace(
    raw_path: str, workspace: Path, *, must_exist: bool = False
) -> Path:
    path = Path(raw_path).expanduser().resolve()
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(
            f"Path must be inside the FileX workspace {workspace}: {path}"
        ) from exc
    if must_exist and not path.is_file():
        raise ValueError(f"File does not exist: {path}")
    return path


def _result_path(payload: dict[str, Any], workspace: Path) -> Path:
    raw_path = str(payload.get("file_path") or "").strip()
    if not raw_path:
        raise ValueError("FileX succeeded without a Markdown file_path")
    result = Path(raw_path).expanduser()
    if not result.is_absolute():
        result = workspace / result
    return _inside_workspace(str(result), workspace, must_exist=True)


def _workspace_artifact_path(
    payload: dict[str, Any], key: str, workspace: Path, label: str
) -> Path:
    raw_path = str(payload.get(key) or "").strip()
    if not raw_path:
        raise ValueError(f"FileX succeeded without a {label} path")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return _inside_workspace(str(path), workspace, must_exist=True)


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate Document IR JSON key")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> None:
    raise ValueError("nonfinite Document IR JSON number")


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _export_artifact_bundle(
    *,
    artifacts_dir: str,
    source: Path,
    markdown: Path,
    payload: dict[str, Any],
    workspace: Path,
    layout_format: str = "document-ir",
) -> dict[str, Any]:
    root = (
        Path(os.environ.get("FILEX_ARTIFACTS_ROOT", "/logs/artifacts"))
        .expanduser()
        .resolve()
    )
    destination = Path(artifacts_dir).expanduser().resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "Artifact directory must be inside FILEX_ARTIFACTS_ROOT "
            f"{root}: {destination}"
        ) from exc
    if destination.exists() and (destination.is_symlink() or not destination.is_dir()):
        raise ValueError(
            f"Artifact destination is not a regular directory: {destination}"
        )
    destination.mkdir(parents=True, exist_ok=True)

    document_ir = _workspace_artifact_path(
        payload, "document_file_path", workspace, "Document IR"
    )
    source_bytes = source.read_bytes()
    markdown_bytes = markdown.read_bytes()
    original_ir_bytes = document_ir.read_bytes()
    try:
        layout = json.loads(
            original_ir_bytes,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, ValueError) as exc:
        raise ValueError("FileX Document IR is not valid JSON") from exc
    if not isinstance(layout, dict):
        raise TypeError("FileX Document IR must be a JSON object")
    if layout_format == "parse-output":
        layout = _export_parse_output(
            document_ir=layout,
            markdown=markdown_bytes.decode("utf-8"),
            example_id=str(payload.get("task_id") or source.stem),
        )
    elif layout_format != "document-ir":
        raise ValueError("layout format must be document-ir or parse-output")
    layout_bytes = (
        json.dumps(
            layout,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")

    document_output = destination / "document.md"
    layout_output = destination / "layout.json"
    result_output = destination / "result.json"
    result = {
        "schema_version": "filex.skill.parse-result/v1"
        if layout_format == "document-ir"
        else "filex.skill.parse-result/v2",
        "status": "succeeded",
        "source": {
            "path": str(source),
            "size": len(source_bytes),
            "sha256": _sha256(source_bytes),
        },
        "artifacts": {
            "document": {
                "path": str(document_output),
                "size": len(markdown_bytes),
                "sha256": _sha256(markdown_bytes),
            },
            "layout": {
                "path": str(layout_output),
                "size": len(layout_bytes),
                "sha256": _sha256(layout_bytes),
            },
        },
        "filex": payload,
    }
    if layout_format == "parse-output":
        raw_ir_output = destination / "document-ir.json"
        result["layout_format"] = layout_format
        result["artifacts"]["document_ir"] = {
            "path": str(raw_ir_output),
            "size": len(original_ir_bytes),
            "sha256": _sha256(original_ir_bytes),
        }
        _atomic_write(raw_ir_output, original_ir_bytes)
    _atomic_write(document_output, markdown_bytes)
    _atomic_write(layout_output, layout_bytes)
    _atomic_write(
        result_output,
        (
            json.dumps(
                result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode("utf-8"),
    )
    return {
        "artifacts_dir": str(destination),
        "artifact_result": str(result_output),
        "layout_format": layout_format,
    }


def _export_parse_output(
    *, document_ir: dict[str, Any], markdown: str, example_id: str
) -> dict[str, Any]:
    """Call the wheel's converter using a snapshot, without exposing file paths."""

    executable = os.environ.get("FILEX_PYTHON") or sys.executable
    request = {
        "document_ir": document_ir,
        "markdown": markdown,
        "example_id": example_id,
    }
    try:
        completed = subprocess.run(
            [executable, "-m", "document_parse_service.parse_output_export"],
            input=json.dumps(request, ensure_ascii=False, allow_nan=False),
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            "Could not run FileX ParseOutput exporter with FILEX_PYTHON"
        ) from exc
    if completed.returncode:
        detail = completed.stderr.strip()
        raise ValueError(
            "FileX ParseOutput export failed; FILEX_PYTHON must select a Python "
            "environment with the current aworld-filex wheel installed. " + detail
        )
    result = json.loads(completed.stdout)
    if not isinstance(result, dict) or result.get("task_type") != "parse":
        raise ValueError("FileX ParseOutput exporter returned invalid output")
    return result


def _run(command: list[str]) -> tuple[int, dict[str, Any]]:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        return 2, {
            "success": False,
            "message": f"Failed to start FileX: {exc}",
            "error_type": "DependencyError",
        }
    try:
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise TypeError("JSON result is not an object")
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        detail = completed.stderr.strip()
        return 2, {
            "success": False,
            "message": f"FileX returned invalid JSON: {exc}"
            + (f". stderr: {detail}" if detail else ""),
            "error_type": "ProtocolError",
        }
    return (0 if completed.returncode == 0 and payload.get("success") else 2), payload


def _parse(args: argparse.Namespace, executable: str, workspace: Path) -> int:
    if args.layout_format not in {"document-ir", "parse-output"}:
        return _fail(
            "FILEX_LAYOUT_FORMAT must be document-ir or parse-output",
            error_type="InputError",
        )
    if args.provider and args.env_file:
        return _fail(
            "Use --provider without --env-file, or put filex_parse_provider "
            "in the env file.",
            error_type="InputError",
        )
    if args.artifacts_dir and (not args.input or args.sync_mode != "sync"):
        return _fail(
            "--artifacts-dir requires synchronous parsing of a local --input",
            error_type="InputError",
        )
    for name in ("page_batch_size", "first_batch_pages"):
        value = getattr(args, name)
        if value is not None and value < 1:
            return _fail(
                f"{name.replace('_', '-')} must be greater than zero",
                error_type="InputError",
            )

    command = [executable, "parse"]
    source_path: Path | None = None
    try:
        if args.input:
            source_path = _inside_workspace(args.input, workspace, must_exist=True)
            command.extend(["--workspace-path", str(source_path)])
        else:
            command.extend(["--url", args.url])
        if args.env_file:
            env_file = _inside_workspace(args.env_file, workspace, must_exist=True)
            command.extend(["--env-content-file", str(env_file)])
    except ValueError as exc:
        return _fail(str(exc), error_type="InputError")

    command.extend(
        [
            "--sync-mode",
            args.sync_mode,
            "--asset-reference-mode",
            args.asset_reference_mode,
        ]
    )
    if args.file_type:
        command.extend(["--file-type", args.file_type])
    if args.provider:
        command.extend(
            ["--env-content-json", json.dumps({"filex_parse_provider": args.provider})]
        )
    for flag, value in (
        ("--task-id", args.task_id),
        ("--pages", args.pages),
        ("--page-batch-size", args.page_batch_size),
        ("--first-batch-pages", args.first_batch_pages),
        ("--batch-resume-id", args.batch_resume_id),
    ):
        if value is not None:
            command.extend([flag, str(value)])
    if args.force_refresh:
        command.append("--force-refresh")
    if args.no_cache:
        command.append("--no-cache")
    command.extend(["--mode", args.mode])
    if args.language:
        command.extend(["--language", args.language])
    if args.allow_media_download:
        command.append("--allow-media-download")
    if args.rights_basis:
        command.extend(["--rights-basis", args.rights_basis])

    return_code, payload = _run(command)
    if return_code or args.sync_mode == "async":
        _emit(payload)
        return return_code
    original_response = dict(payload)
    try:
        result = _result_path(payload, workspace)
        if args.output:
            output = _inside_workspace(args.output, workspace)
            output.parent.mkdir(parents=True, exist_ok=True)
            if output != result:
                shutil.copy2(result, output)
        else:
            output = result
    except ValueError as exc:
        return _fail(str(exc), error_type="OutputError")
    payload["input_path"] = str(source_path) if source_path else args.url
    payload["output_path"] = str(output)
    if args.artifacts_dir:
        try:
            payload.update(
                _export_artifact_bundle(
                    artifacts_dir=args.artifacts_dir,
                    source=source_path,
                    markdown=output,
                    payload=original_response,
                    workspace=workspace,
                    layout_format=args.layout_format,
                )
            )
        except (OSError, TypeError, ValueError) as exc:
            return _fail(str(exc), error_type="OutputError")
    _emit(payload)
    return 0


def _status(args: argparse.Namespace, executable: str) -> int:
    command = [executable, "status", "--batch-resume-id", args.batch_resume_id]
    if args.include_results:
        command.append("--include-results")
    command.extend(
        ["--after-batch", str(args.after_batch), "--max-batches", str(args.max_batches)]
    )
    return_code, payload = _run(command)
    _emit(payload)
    return return_code


def _inspect(args: argparse.Namespace, executable: str) -> int:
    return_code, payload = _run([executable, "inspect", args.url])
    _emit(payload)
    return return_code


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace = Path(args.workspace_root).expanduser().resolve()
    executable = shutil.which(args.filex_bin)
    if executable is None:
        return _fail(
            f"FileX executable not found: {args.filex_bin}. "
            "Use a FileX-enabled sandbox image.",
            error_type="DependencyError",
        )
    if args.command == "parse":
        return _parse(args, executable, workspace)
    if args.command == "inspect":
        return _inspect(args, executable)
    return _status(args, executable)


if __name__ == "__main__":
    raise SystemExit(main())
