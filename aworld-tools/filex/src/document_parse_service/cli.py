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

from .paths import DOCUMENT_PARSE_WORKSPACE, FS_WORKSPACE_ROOT
from .pdf.pdf_batch_checkpoint import PdfBatchCheckpointStore
from .service import DocumentParseService, normalize_env_content
from .source_providers import SourceResolution, YouTubeSourceProvider


def main(argv: Optional[list[str]] = None) -> int:
    _configure_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)
    trace_id = generate_trace_id(_trace_method_name(args.command))
    set_trace_id(trace_id)

    try:
        if args.command == "parse":
            payload = asyncio.run(_run_parse(args, trace_id=trace_id))
        elif args.command == "inspect":
            payload = asyncio.run(_run_inspect(args))
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
                    [
                        args.after_batch,
                        *(int(batch.get("batch_index") or 0) for batch in batches),
                    ],
                )
                payload["incremental_result"] = {
                    "schema_version": "1.0",
                    "stream_id": args.batch_resume_id,
                    "cursor": cursor,
                    "status": str(progress.get("status") or "queued"),
                    "is_final": str(progress.get("status") or "").lower()
                    in {"succeeded", "failed"},
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
        payload = _error_payload(
            type(exc).__name__, f"Document parse CLI failed: {exc}"
        )

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

    parse = subparsers.add_parser("parse", help="Parse a source into Markdown")
    parse.add_argument("source", nargs="?", help="Local path or HTTP(S) URL")
    group = parse.add_mutually_exclusive_group(required=False)
    group.add_argument("--url", help="HTTP(S) file URL")
    group.add_argument("--workspace-path", help="Absolute local path under workspace")
    parse.add_argument(
        "--source-provider",
        choices=["auto", "youtube", "http", "local"],
        default="auto",
        help="Override source discovery; defaults to URL/path detection",
    )
    parse.add_argument(
        "--mode",
        choices=["auto", "transcript"],
        default="auto",
        help=(
            "Media acquisition mode; YouTube currently supports "
            "transcript-first parsing"
        ),
    )
    parse.add_argument("--language", help="Preferred source transcript or ASR language")
    parse.add_argument(
        "--allow-media-download",
        action="store_true",
        help=(
            "Allow a source provider to download media when text tracks are unavailable"
        ),
    )
    parse.add_argument(
        "--rights-basis",
        choices=["user-owned", "licensed", "service-permitted", "applicable-law"],
        help="Required legal basis when --allow-media-download is used",
    )
    parse.add_argument("--file-type", help="Optional explicit source file type")
    parse.add_argument(
        "--sync-mode",
        choices=["sync", "async"],
        default="sync",
        help="Run in sync or async mode",
    )
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

    inspect_parser = subparsers.add_parser(
        "inspect",
        help=(
            "Discover source metadata and recommended routes without downloading media"
        ),
    )
    inspect_parser.add_argument("source", help="HTTP(S) source URL")
    inspect_parser.add_argument(
        "--source-provider",
        choices=["auto", "youtube"],
        default="auto",
        help="Override source discovery",
    )

    status_parser = subparsers.add_parser(
        "status", help="Read a resumable PDF parse progress snapshot"
    )
    status_parser.add_argument(
        "--batch-resume-id", required=True, help="Stable PDF batch resume identifier"
    )
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
    env_content = _load_optional_json_argument(
        args.env_content_json, args.env_content_file
    )
    source_kind, source_value = _resolve_parse_source(args)
    if args.rights_basis and not args.allow_media_download:
        raise ValueError("--rights-basis requires --allow-media-download")
    if source_kind != "youtube" and (args.allow_media_download or args.rights_basis):
        raise ValueError(
            "media download authorization is only supported by the "
            "YouTube source provider"
        )
    source_resolution: SourceResolution | None = None
    if source_kind == "youtube":
        if args.mode not in {"auto", "transcript"}:
            raise ValueError(f"unsupported youtube mode: {args.mode}")
        source_resolution = await asyncio.to_thread(
            YouTubeSourceProvider().resolve,
            source_value,
            output_root=FS_WORKSPACE_ROOT / "source_downloads",
            language=str(args.language or ""),
            allow_media_download=bool(args.allow_media_download),
            rights_basis=str(args.rights_basis or ""),
        )
        workspace_path = str(source_resolution.local_path)
        resolved_file_type = source_resolution.file_type
        if args.file_type and args.file_type.lower().lstrip(".") != resolved_file_type:
            raise ValueError(
                "file_type cannot override the artifact selected by a source provider: "
                f"selected={resolved_file_type} requested={args.file_type}"
            )
    elif source_kind == "http":
        workspace_path = str(await asyncio.to_thread(_download_url, source_value))
        resolved_file_type = args.file_type
    else:
        workspace_path = source_value
        resolved_file_type = args.file_type
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
    result = await service.parse(
        workspace_path=workspace_path,
        file_type=resolved_file_type,
        task_id=args.task_id or trace_id,
        sync_mode=args.sync_mode,
        asset_reference_mode=args.asset_reference_mode,
        env_content=env_content,
    )
    if source_resolution is not None:
        _attach_source_manifest(result, source_resolution.manifest)
    return result


async def _run_inspect(args: argparse.Namespace) -> dict[str, Any]:
    source = str(args.source or "").strip()
    provider = str(args.source_provider or "auto").strip().lower()
    if provider == "youtube" or (
        provider == "auto" and YouTubeSourceProvider.supports(source)
    ):
        inspection = await asyncio.to_thread(YouTubeSourceProvider().inspect, source)
        return {"success": True, **inspection}
    raise ValueError("no source provider can inspect this source")


def _resolve_parse_source(args: argparse.Namespace) -> tuple[str, str]:
    supplied = [
        value
        for value in (
            str(args.source or "").strip(),
            str(args.url or "").strip(),
            str(args.workspace_path or "").strip(),
        )
        if value
    ]
    if len(supplied) != 1:
        raise ValueError("provide exactly one source, --url, or --workspace-path")
    value = supplied[0]
    requested_provider = str(args.source_provider or "auto").strip().lower()
    is_http = urlparse(value).scheme in {"http", "https"}
    if requested_provider == "youtube":
        if not YouTubeSourceProvider.supports(value):
            raise ValueError("source is not a supported YouTube URL")
        return "youtube", value
    if requested_provider == "http":
        if not is_http:
            raise ValueError("http source provider requires an HTTP(S) URL")
        return "http", value
    if requested_provider == "local":
        if is_http:
            raise ValueError("local source provider requires a filesystem path")
        return "local", value
    if YouTubeSourceProvider.supports(value):
        return "youtube", value
    return ("http", value) if is_http else ("local", value)


def _attach_source_manifest(result: dict[str, Any], manifest: dict[str, Any]) -> None:
    output_dir = FS_WORKSPACE_ROOT / str(result.get("file_dir_path") or "")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "source.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    relative_manifest_path = str(manifest_path.relative_to(FS_WORKSPACE_ROOT))
    result["source"] = manifest
    result["source_manifest_path"] = relative_manifest_path
    metrics = result.get("metrics")
    if isinstance(metrics, dict):
        metrics["source"] = {
            "provider": manifest.get("source_provider"),
            "provider_version": manifest.get("source_provider_version"),
            "selected_route": manifest.get("selected_route"),
            "video_id": manifest.get("video_id"),
            "media_downloaded": manifest.get("media_downloaded"),
        }
        metrics_relative_path = str(result.get("metrics_file_path") or "").strip()
        if metrics_relative_path:
            (FS_WORKSPACE_ROOT / metrics_relative_path).write_text(
                json.dumps(metrics, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )


def _download_url(raw_url: str) -> Path:
    url = str(raw_url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an absolute HTTP(S) URL")

    timeout = max(1, int(os.getenv("FILEX_DOWNLOAD_TIMEOUT_SECONDS", "120")))
    max_bytes = max(
        1, int(os.getenv("FILEX_MAX_DOWNLOAD_BYTES", str(512 * 1024 * 1024)))
    )
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
            raise ValueError(
                f"url content exceeds FILEX_MAX_DOWNLOAD_BYTES ({max_bytes})"
            )

        candidate = Path(unquote(urlparse(final_url).path)).name or "downloaded_file"
        file_name = (
            re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._") or "downloaded_file"
        )
        target_path = target_dir / file_name
        temporary_path = target_dir / f".{file_name}.{uuid.uuid4().hex}.tmp"
        total = 0
        try:
            with temporary_path.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(
                            "url content exceeds FILEX_MAX_DOWNLOAD_BYTES "
                            f"({max_bytes})"
                        )
                    output.write(chunk)
            os.replace(temporary_path, target_path)
        finally:
            temporary_path.unlink(missing_ok=True)
    return target_path


def _load_optional_json_argument(
    inline: Optional[str], file_path: Optional[str]
) -> dict[str, Any]:
    if file_path:
        raw = open(file_path, "r", encoding="utf-8").read()
        return normalize_env_content(raw)
    if inline is None:
        return {}
    return normalize_env_content(inline)


def _trace_method_name(command: Optional[str]) -> str:
    return {
        "parse": "file_parse",
        "inspect": "file_inspect",
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
