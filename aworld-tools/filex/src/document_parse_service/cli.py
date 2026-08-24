"""Command line interface for document parsing."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from utils import generate_trace_id, set_trace_id

from .service import DocumentParseService, normalize_env_content
from .paths import DOCUMENT_PARSE_WORKSPACE, FS_WORKSPACE_ROOT
from .pdf.pdf_batch_checkpoint import PdfBatchCheckpointStore


def main(argv: Optional[list[str]] = None) -> int:
    _configure_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)
    trace_id = generate_trace_id(_trace_method_name(args.command))
    set_trace_id(trace_id)

    try:
        if args.command == "parse":
            payload = asyncio.run(_run_parse(args, trace_id=trace_id))
        elif args.command == "status":
            store = PdfBatchCheckpointStore(
                DOCUMENT_PARSE_WORKSPACE / "pdf_batch_checkpoints",
                args.batch_resume_id,
            )
            progress = store.read_progress()
            payload = {"success": True, **progress}
            if args.include_results:
                batches = store.read_incremental_results(
                    after_batch_index=args.after_batch,
                    max_batches=args.max_batches,
                )
                cursor = max(
                    [args.after_batch, *(int(batch.get("batch_index") or 0) for batch in batches)],
                )
                payload["incremental_result"] = {
                    "schema_version": "1.0",
                    "stream_id": args.batch_resume_id,
                    "cursor": cursor,
                    "status": str(progress.get("status") or "queued"),
                    "is_final": str(progress.get("status") or "").lower() in {"succeeded", "failed"},
                    "failed_batch": int(progress.get("failed_batch") or 0),
                    "error": str(progress.get("error") or ""),
                    "batches": batches,
                }
        else:
            parser.print_help(sys.stderr)
            return 2
    except ValueError as exc:
        payload = _error_payload("ValidationError", str(exc))
    except BaseException as exc:
        payload = _error_payload(type(exc).__name__, f"Document parse CLI failed: {exc}")

    if args.command != "status":
        payload["task_id"] = payload.get("task_id") or trace_id
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0 if payload.get("success") else 1


def _configure_logging() -> None:
    level_name = os.getenv("DOCUMENT_PARSE_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stderr,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="filex",
        description="Parse local paths or HTTP(S) URLs using FileX.",
    )
    subparsers = parser.add_subparsers(dest="command")

    parse = subparsers.add_parser("parse", help="Parse a URL or local file into Markdown")
    group = parse.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="HTTP(S) file URL")
    group.add_argument("--workspace-path", help="Absolute local path under workspace")
    parse.add_argument("--file-type", help="Optional explicit source file type")
    parse.add_argument("--sync-mode", choices=["sync", "async"], default="sync", help="Run in sync or async mode")
    parse.add_argument(
        "--asset-reference-mode",
        choices=["remote_id", "local_path"],
        default="local_path",
        help="How Markdown references extracted assets",
    )
    parse.add_argument("--task-id", help="Optional task id override")
    parse.add_argument(
        "--pages",
        help="Optional one-based PDF pages, for example: 1,3-5",
    )
    parse.add_argument(
        "--first-batch-pages",
        type=int,
        help="Number of processed PDF pages counted as the first consumable batch",
    )
    parse.add_argument(
        "--page-batch-size",
        type=int,
        help="Process PDF pages in sequential provider batches of this size",
    )
    parse.add_argument(
        "--batch-resume-id",
        help="Stable identifier used to reuse completed PDF page batches",
    )
    parse.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore a cached result and refresh it",
    )
    parse.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass result cache reads and writes",
    )
    _add_env_content_args(parse, required=False)

    status_parser = subparsers.add_parser("status", help="Read a resumable PDF parse progress snapshot")
    status_parser.add_argument("--batch-resume-id", required=True, help="Stable PDF batch resume identifier")
    status_parser.add_argument(
        "--include-results",
        action="store_true",
        help="Include completed batch Markdown after the supplied cursor",
    )
    status_parser.add_argument(
        "--after-batch",
        type=int,
        default=0,
        help="Return only completed batches with an index greater than this cursor",
    )
    status_parser.add_argument(
        "--max-batches",
        type=int,
        default=10,
        help="Maximum completed batch results returned by one status call",
    )

    return parser


def _add_env_content_args(parser: argparse.ArgumentParser, *, required: bool) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--env-content-json", help="env_content as inline JSON")
    group.add_argument("--env-content-file", help="Read env_content JSON from a file")


async def _run_parse(args: argparse.Namespace, *, trace_id: str) -> dict[str, Any]:
    service = DocumentParseService()
    env_content = _load_optional_json_argument(args.env_content_json, args.env_content_file)
    workspace_path = args.workspace_path
    if args.url:
        workspace_path = str(await asyncio.to_thread(_download_url, args.url))
    if args.pages:
        env_content["pdf_pages"] = args.pages
    if args.first_batch_pages is not None:
        if args.first_batch_pages < 1:
            raise ValueError("first_batch_pages must be greater than zero")
        env_content["first_batch_page_count"] = args.first_batch_pages
    if args.page_batch_size is not None:
        if args.page_batch_size < 1:
            raise ValueError("page_batch_size must be greater than zero")
        env_content["pdf_page_batch_size"] = args.page_batch_size
    if args.batch_resume_id:
        env_content["pdf_batch_resume_id"] = args.batch_resume_id
    if args.force_refresh:
        env_content["filex_force_refresh"] = True
    if args.no_cache:
        env_content["filex_no_cache"] = True
    return await service.parse(
        workspace_path=workspace_path,
        file_type=args.file_type,
        task_id=args.task_id or trace_id,
        sync_mode=args.sync_mode,
        asset_reference_mode=args.asset_reference_mode,
        env_content=env_content,
    )


def _download_url(raw_url: str) -> Path:
    url = str(raw_url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an absolute HTTP(S) URL")

    timeout = max(1, int(os.getenv("FILEX_DOWNLOAD_TIMEOUT_SECONDS", "120")))
    max_bytes = max(1, int(os.getenv("FILEX_MAX_DOWNLOAD_BYTES", str(512 * 1024 * 1024))))
    request = Request(url, headers={"User-Agent": "AWorld-FileX/1.0"})
    identity = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    target_dir = FS_WORKSPACE_ROOT / "url_downloads" / identity
    target_dir.mkdir(parents=True, exist_ok=True)

    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - HTTP(S) validated above
        final_url = str(response.geturl() or url)
        if urlparse(final_url).scheme not in {"http", "https"}:
            raise ValueError("url redirected to an unsupported scheme")
        content_length = int(response.headers.get("Content-Length") or 0)
        if content_length > max_bytes:
            raise ValueError(f"url content exceeds FILEX_MAX_DOWNLOAD_BYTES ({max_bytes})")

        candidate = Path(unquote(urlparse(final_url).path)).name or "downloaded_file"
        file_name = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._") or "downloaded_file"
        target_path = target_dir / file_name
        temporary_path = target_dir / f".{file_name}.{uuid.uuid4().hex}.tmp"
        total = 0
        try:
            with temporary_path.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"url content exceeds FILEX_MAX_DOWNLOAD_BYTES ({max_bytes})")
                    output.write(chunk)
            os.replace(temporary_path, target_path)
        finally:
            temporary_path.unlink(missing_ok=True)
    return target_path


def _load_optional_json_argument(inline: Optional[str], file_path: Optional[str]) -> dict[str, Any]:
    if file_path:
        raw = open(file_path, "r", encoding="utf-8").read()
        return normalize_env_content(raw)
    if inline is None:
        return {}
    return normalize_env_content(inline)


def _trace_method_name(command: Optional[str]) -> str:
    return {
        "parse": "file_parse",
    }.get(command or "", "filex_cli")


def _error_payload(error_type: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "message": message,
        "error_type": error_type,
        "warnings": [],
    }


if __name__ == "__main__":
    raise SystemExit(main())
