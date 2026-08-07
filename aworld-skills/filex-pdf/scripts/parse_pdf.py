#!/usr/bin/env python3
"""Run FileX PDF parsing and emit a compact, agent-friendly JSON result."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse a PDF to Markdown with FileX")
    parser.add_argument("--input", required=True, help="Absolute or current-directory PDF path")
    parser.add_argument("--output", help="Optional Markdown destination")
    parser.add_argument("--provider", help="Configured FileX PDF provider, for example liteparse")
    parser.add_argument("--pages", help="One-based pages, for example 1,3-5")
    parser.add_argument("--page-batch-size", type=int, help="Sequential FileX page batch size")
    parser.add_argument("--first-batch-pages", type=int, help="Pages in the first consumable batch")
    parser.add_argument("--force-refresh", action="store_true", help="Refresh a cached result")
    parser.add_argument("--no-cache", action="store_true", help="Bypass FileX cache")
    parser.add_argument(
        "--filex-bin",
        default=os.environ.get("FILEX_BIN", "filex"),
        help="FileX executable (default: FILEX_BIN or filex)",
    )
    parser.add_argument(
        "--workspace-root",
        default=os.environ.get("FILEX_WORKSPACE_ROOT", str(Path.home() / "workspace")),
        help="Root used to resolve FileX relative output paths",
    )
    return parser


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _fail(message: str, *, error_type: str = "FileXError", task_id: str = "") -> int:
    _emit(
        {
            "success": False,
            "message": message,
            "error_type": error_type,
            "task_id": task_id,
        }
    )
    return 2


def _load_payload(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"FileX returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("FileX returned a non-object JSON payload")
    return payload


def _resolve_result_path(payload: dict[str, Any], workspace_root: Path) -> Path:
    raw_path = str(payload.get("file_path") or "").strip()
    if not raw_path:
        raise ValueError("FileX succeeded without a Markdown file_path")
    result_path = Path(raw_path).expanduser()
    if not result_path.is_absolute():
        result_path = workspace_root / result_path
    result_path = result_path.resolve()
    try:
        result_path.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"FileX Markdown output is outside the workspace: {result_path}") from exc
    if not result_path.is_file():
        raise ValueError(f"FileX Markdown output does not exist: {result_path}")
    return result_path


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace_root = Path(args.workspace_root).expanduser().resolve()
    source = Path(args.input).expanduser().resolve()
    if not source.is_file():
        return _fail(f"PDF file does not exist: {source}", error_type="InputError")
    try:
        source.relative_to(workspace_root)
    except ValueError:
        return _fail(
            f"PDF must be inside the FileX workspace {workspace_root}: {source}",
            error_type="InputError",
        )
    with source.open("rb") as source_file:
        signature = source_file.read(4)
    if signature != b"%PDF":
        return _fail(f"Input does not have a PDF signature: {source}", error_type="InputError")
    if args.page_batch_size is not None and args.page_batch_size < 1:
        return _fail("page-batch-size must be greater than zero", error_type="InputError")
    if args.first_batch_pages is not None and args.first_batch_pages < 1:
        return _fail("first-batch-pages must be greater than zero", error_type="InputError")

    executable = shutil.which(args.filex_bin)
    if executable is None:
        return _fail(
            f"FileX executable not found: {args.filex_bin}. Use a FileX-enabled sandbox image.",
            error_type="DependencyError",
        )

    command = [
        executable,
        "parse",
        "--workspace-path",
        str(source),
        "--file-type",
        "pdf",
        "--sync-mode",
        "sync",
        "--asset-reference-mode",
        "local_path",
    ]
    if args.provider:
        command.extend(
            [
                "--env-content-json",
                json.dumps({"filex_parse_provider": args.provider}, ensure_ascii=False),
            ]
        )
    if args.pages:
        command.extend(["--pages", args.pages])
    if args.page_batch_size is not None:
        command.extend(["--page-batch-size", str(args.page_batch_size)])
    if args.first_batch_pages is not None:
        command.extend(["--first-batch-pages", str(args.first_batch_pages)])
    if args.force_refresh:
        command.append("--force-refresh")
    if args.no_cache:
        command.append("--no-cache")

    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        return _fail(f"Failed to start FileX: {exc}", error_type="DependencyError")
    try:
        payload = _load_payload(completed.stdout)
    except ValueError as exc:
        detail = completed.stderr.strip()
        message = f"{exc}. stderr: {detail}" if detail else str(exc)
        return _fail(message, error_type="ProtocolError")

    if completed.returncode != 0 or not payload.get("success"):
        message = str(payload.get("message") or completed.stderr.strip() or "FileX parsing failed")
        return _fail(
            message,
            error_type=str(payload.get("error_type") or "FileXError"),
            task_id=str(payload.get("task_id") or ""),
        )

    try:
        result_path = _resolve_result_path(payload, workspace_root)
    except ValueError as exc:
        return _fail(str(exc), error_type="OutputError", task_id=str(payload.get("task_id") or ""))

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        try:
            output_path.relative_to(workspace_root)
        except ValueError:
            return _fail(
                f"Markdown output must be inside the FileX workspace {workspace_root}: {output_path}",
                error_type="InputError",
                task_id=str(payload.get("task_id") or ""),
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path != result_path:
            shutil.copy2(result_path, output_path)
    else:
        output_path = result_path

    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    _emit(
        {
            "success": True,
            "message": "PDF parsed successfully",
            "input_path": str(source),
            "output_path": str(output_path),
            "task_id": str(payload.get("task_id") or ""),
            "provider": str(metrics.get("provider") or args.provider or ""),
            "metrics_file_path": str(payload.get("metrics_file_path") or ""),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
