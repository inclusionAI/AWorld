"""Bounded document parsing with a killable worker process."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from ..limits import FilesystemLimits


_OOXML_TYPES = frozenset({"docx", "xlsx", "pptx"})


def _get_parser(file_type: str):
    """Load only the selected parser inside the resource-bounded worker."""

    from .parsers.csv_parser import CsvParser
    from .parsers.excel_parser import ExcelParser
    from .parsers.md_parser import MdParser
    from .parsers.pdf_parser import PdfParser
    from .parsers.ppt_parser import PptParser
    from .parsers.txt_parser import TxtParser
    from .parsers.word_parser import WordParser

    for parser in (
        TxtParser(),
        MdParser(),
        CsvParser(),
        ExcelParser(),
        WordParser(),
        PptParser(),
        PdfParser(),
    ):
        if parser.can_handle(file_type.lower().strip()):
            return parser
    return None


def _validate_ooxml_archive(
    file_path: Path,
    file_type: str,
    limits: FilesystemLimits,
) -> None:
    """Reject archive bombs and unsafe member names before parser imports."""

    if file_type.lower() not in _OOXML_TYPES:
        return
    try:
        archive = zipfile.ZipFile(file_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Invalid {file_type.upper()} archive") from exc

    total_uncompressed = 0
    with archive:
        members = archive.infolist()
        if len(members) > limits.max_archive_members:
            raise ValueError(
                "Document archive has too many members: "
                f"{len(members)}; limit={limits.max_archive_members}"
            )
        for member in members:
            member_path = PurePosixPath(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError("Document archive contains an unsafe member path")
            if member.flag_bits & 0x1:
                raise ValueError("Encrypted document archive members are not supported")
            if member.file_size > limits.max_archive_member_bytes:
                raise ValueError(
                    "Document archive member is too large: "
                    f"{member.file_size}; limit={limits.max_archive_member_bytes}"
                )
            total_uncompressed += member.file_size
            if total_uncompressed > limits.max_archive_uncompressed_bytes:
                raise ValueError(
                    "Document archive expands beyond the allowed size: "
                    f"limit={limits.max_archive_uncompressed_bytes}"
                )
            if member.file_size >= 1024 * 1024:
                ratio = member.file_size / max(1, member.compress_size)
                if ratio > limits.max_archive_compression_ratio:
                    raise ValueError(
                        "Document archive compression ratio is unsafe: "
                        f"{ratio:.1f}; limit={limits.max_archive_compression_ratio}"
                    )


async def _parse_in_worker(
    file_path: Path,
    output_path: Path,
    file_type: str,
    limits: FilesystemLimits,
    sidecar_dir: Path,
) -> str:
    parser = _get_parser(file_type)
    if not parser:
        raise ValueError(
            f"不支持的文件类型: {file_type}。支持: pdf, txt, md, doc, docx, xlsx, xls, csv, ppt, pptx"
        )
    result = await parser.parse(
        file_path,
        task_id="",
        source_file_name=file_path.stem,
        output_path=output_path,
        filesystem_limits=limits,
        sidecar_dir=sidecar_dir,
    )
    out = result.get("file_path")
    if out is None:
        raise RuntimeError("解析未返回 file_path")
    return str(Path(out).resolve())


async def _kill_worker(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    await process.communicate()


async def _run_worker(
    file_path: Path,
    output_path: Path,
    file_type: str,
    limits: FilesystemLimits,
    sidecar_dir: Path,
) -> str:
    payload = {
        "file_path": str(file_path),
        "output_path": str(output_path),
        "file_type": file_type,
        "limits": asdict(limits),
        "sidecar_dir": str(sidecar_dir),
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "aworld.sandbox.tool_servers.filesystem.src.utils.document_processor.parse_to_path",
        "--parse-worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=(os.name == "posix"),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(json.dumps(payload).encode("utf-8")),
            timeout=limits.parse_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        await _kill_worker(process)
        raise TimeoutError(
            f"Document parsing timed out after {limits.parse_timeout_seconds:.1f}s"
        ) from exc
    except BaseException:
        await asyncio.shield(_kill_worker(process))
        raise
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "Document parser worker failed")
    try:
        decoded = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Document parser worker returned invalid output") from exc
    if "error" in decoded:
        raise RuntimeError(decoded["error"])
    return str(decoded["file_path"])


async def parse_to_path(
    file_path: str | Path,
    output_path: str | Path,
    file_type: str,
) -> str:
    """Parse a document under archive, time, and output-size budgets."""

    source = Path(file_path).resolve()
    destination = Path(output_path).resolve()
    limits = FilesystemLimits.from_env()
    _validate_ooxml_archive(source, file_type, limits)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=f".{destination.name}.aworld-parse-",
        suffix=".md",
        delete=False,
    ) as temporary:
        staged_output = Path(temporary.name)
    staged_sidecar = Path(f"{staged_output}.assets")
    published_sidecar: Path | None = None
    try:
        result = await _run_worker(
            source,
            staged_output,
            file_type,
            limits,
            staged_sidecar,
        )
        resolved_result = Path(result).resolve()
        if resolved_result != staged_output.resolve() or not resolved_result.is_file():
            raise RuntimeError("Document parser returned an unexpected output path")
        output_size = resolved_result.stat().st_size
        if output_size > limits.max_parse_output_bytes:
            raise ValueError(
                "Parsed Markdown exceeds the output size limit: "
                f"{output_size}; limit={limits.max_parse_output_bytes}"
            )

        if staged_sidecar.is_dir():
            preferred_name = (
                "images"
                if file_type.lower() in {"ppt", "pptx"}
                else f"{destination.stem}_images"
            )
            published_sidecar = destination.parent / preferred_name
            if published_sidecar.exists():
                published_sidecar = destination.parent / (
                    f"{preferred_name}-{staged_output.name.rsplit('-', 1)[-1]}"
                )
            markdown = staged_output.read_text(encoding="utf-8")
            markdown = markdown.replace(staged_sidecar.name, published_sidecar.name)
            staged_output.write_text(markdown, encoding="utf-8")
            os.replace(staged_sidecar, published_sidecar)

        os.replace(staged_output, destination)
        return str(destination)
    except BaseException:
        staged_output.unlink(missing_ok=True)
        shutil.rmtree(staged_sidecar, ignore_errors=True)
        if published_sidecar is not None:
            shutil.rmtree(published_sidecar, ignore_errors=True)
        raise


def _parse_worker_main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        limits = FilesystemLimits(**payload.pop("limits"))
        if sys.platform.startswith("linux"):
            import resource

            current_soft, current_hard = resource.getrlimit(resource.RLIMIT_AS)
            requested = limits.max_parse_memory_bytes
            if current_hard not in (-1, resource.RLIM_INFINITY):
                requested = min(requested, current_hard)
            resource.setrlimit(resource.RLIMIT_AS, (requested, requested))
        result = asyncio.run(
            _parse_in_worker(
                Path(payload["file_path"]),
                Path(payload["output_path"]),
                payload["file_type"],
                limits,
                Path(payload["sidecar_dir"]),
            )
        )
        sys.stdout.write(json.dumps({"file_path": result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        sys.stdout.write(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 0


if __name__ == "__main__" and "--parse-worker" in sys.argv:
    raise SystemExit(_parse_worker_main())
