"""Command line interface for document parsing."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any, Optional

from utils import generate_trace_id, set_trace_id

from .service import DocumentParseService, normalize_env_content
from .paths import DOCUMENT_PARSE_WORKSPACE
from .pdf.pdf_batch_checkpoint import PdfBatchCheckpointStore


def main(argv: Optional[list[str]] = None) -> int:
    _configure_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)
    trace_id = generate_trace_id(_trace_method_name(args.command))
    set_trace_id(trace_id)

    try:
        if args.command == "save":
            payload = asyncio.run(_run_save(args))
        elif args.command == "parse":
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
        description="Save or parse documents using filesystem_server document_parse_service.",
    )
    subparsers = parser.add_subparsers(dest="command")

    save = subparsers.add_parser("save", help="Download a remote AFTS file into the filesystem workspace")
    save.add_argument("--file-id", required=True, help="AFTS file id")
    save.add_argument("--output", required=True, help="Absolute output path under workspace")
    _add_env_content_args(save, required=True)

    parse = subparsers.add_parser("parse", help="Parse a remote or local file into Markdown")
    group = parse.add_mutually_exclusive_group(required=True)
    group.add_argument("--file-id", help="AFTS file id")
    group.add_argument("--workspace-path", help="Absolute local path under workspace")
    parse.add_argument("--file-type", help="Optional explicit source file type")
    parse.add_argument("--sync-mode", choices=["sync", "async"], default="sync", help="Run in sync or async mode")
    parse.add_argument(
        "--asset-reference-mode",
        choices=["remote_id", "local_path"],
        default="remote_id",
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


async def _run_save(args: argparse.Namespace) -> dict[str, Any]:
    service = DocumentParseService()
    env_content = _load_optional_json_argument(args.env_content_json, args.env_content_file)
    return await service.save_file(
        file_id=args.file_id,
        output_path=args.output,
        env_content=env_content,
    )


async def _run_parse(args: argparse.Namespace, *, trace_id: str) -> dict[str, Any]:
    service = DocumentParseService()
    env_content = _load_optional_json_argument(args.env_content_json, args.env_content_file)
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
        file_id=args.file_id,
        workspace_path=args.workspace_path,
        file_type=args.file_type,
        task_id=args.task_id or trace_id,
        sync_mode=args.sync_mode,
        asset_reference_mode=args.asset_reference_mode,
        env_content=env_content,
    )


def _load_optional_json_argument(inline: Optional[str], file_path: Optional[str]) -> dict[str, Any]:
    if file_path:
        raw = open(file_path, "r", encoding="utf-8").read()
        return normalize_env_content(raw)
    if inline is None:
        return {}
    return normalize_env_content(inline)


def _trace_method_name(command: Optional[str]) -> str:
    return {
        "save": "save_file",
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
