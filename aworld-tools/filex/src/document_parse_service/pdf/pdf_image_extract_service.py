"""Extract embedded images from PDF files.

The service uses the system ``pdfimages`` command and stores extracted assets
in the document parsing workspace.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Callable

from ..paths import DOCUMENT_PARSE_WORKSPACE

logger = logging.getLogger(__name__)


class PdfImageExtractService:
    """Extract embedded images from PDF files."""

    def __init__(
        self,
        workspace_base: Path | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._workspace_base = workspace_base or DOCUMENT_PARSE_WORKSPACE
        self._command_runner = command_runner or subprocess.run

    def extract_from_pdf(
        self,
        file_path: Path,
        *,
        task_id: str,
        source_file_name: str,
        password: str | None = None,
    ) -> dict[str, Any]:
        if not file_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {file_path}")

        if file_path.suffix.lower() != ".pdf":
            raise ValueError(f"仅支持 PDF 提图: {file_path}")

        output_dir = self._workspace_base / task_id / "pdf_images"
        archive_path = self._workspace_base / task_id / f"{source_file_name}_pdf_images.zip"
        self._prepare_output_dir(output_dir)
        if archive_path.exists():
            archive_path.unlink()

        manifest = self._list_images(file_path, password=password)
        if not manifest:
            logger.info(
                "pdf_image_extract_service no embedded images found | file_path=%s task_id=%s",
                file_path,
                task_id,
            )
            return {
                "image_count": 0,
                "images": [],
                "manifest": [],
                "output_dir": output_dir,
                "archive_path": None,
            }

        output_prefix = output_dir / source_file_name
        extracted_files = self._extract_images(
            file_path=file_path,
            output_prefix=output_prefix,
            password=password,
        )
        if not extracted_files:
            raise RuntimeError("pdfimages 已识别到图片，但未生成任何输出文件")

        self._create_archive(extracted_files, archive_path)
        logger.info(
            "pdf_image_extract_service extracted images | file_path=%s task_id=%s image_count=%s archive_path=%s",
            file_path,
            task_id,
            len(extracted_files),
            archive_path,
        )
        return {
            "image_count": len(extracted_files),
            "images": extracted_files,
            "manifest": manifest,
            "output_dir": output_dir,
            "archive_path": archive_path,
        }

    def _prepare_output_dir(self, output_dir: Path) -> None:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    def _list_images(self, file_path: Path, *, password: str | None) -> list[dict[str, Any]]:
        command = ["pdfimages", "-list"]
        command.extend(self._build_password_args(password))
        command.extend([str(file_path)])
        result = self._run_command(command, "枚举 PDF 图片")

        manifest: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped or not stripped[0].isdigit():
                continue
            parts = stripped.split()
            if len(parts) < 15:
                logger.debug("pdf_image_extract_service skip unparsable list line | line=%s", line)
                continue
            manifest.append(
                {
                    "page": self._safe_int(parts[0]),
                    "num": self._safe_int(parts[1]),
                    "type": parts[2],
                    "width": self._safe_int(parts[3]),
                    "height": self._safe_int(parts[4]),
                    "color": parts[5],
                    "comp": self._safe_int(parts[6]),
                    "bpc": self._safe_int(parts[7]),
                    "enc": parts[8],
                    "interp": parts[9],
                    "object_id": self._safe_int(parts[10]),
                    "object_generation": self._safe_int(parts[11]),
                    "x_ppi": self._safe_int(parts[12]),
                    "y_ppi": self._safe_int(parts[13]),
                    "size": parts[14],
                    "ratio": parts[15] if len(parts) > 15 else "",
                }
            )
        return manifest

    def _extract_images(
        self,
        *,
        file_path: Path,
        output_prefix: Path,
        password: str | None,
    ) -> list[Path]:
        command = ["pdfimages", "-all", "-p", "-print-filenames"]
        command.extend(self._build_password_args(password))
        command.extend([str(file_path), str(output_prefix)])
        result = self._run_command(command, "提取 PDF 图片")

        extracted_files: list[Path] = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            output_path = Path(stripped)
            if output_path.exists():
                extracted_files.append(output_path)

        if extracted_files:
            return extracted_files

            # Some versions omit filenames; scan the output directory as a fallback.
        parent_dir = output_prefix.parent
        prefix_name = output_prefix.name
        return sorted(
            path
            for path in parent_dir.iterdir()
            if path.is_file() and path.name.startswith(prefix_name)
        )

    def _create_archive(self, image_paths: list[Path], archive_path: Path) -> None:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for image_path in image_paths:
                archive.write(image_path, arcname=f"pdf_images/{image_path.name}")

    def _run_command(self, command: list[str], action: str) -> subprocess.CompletedProcess[str]:
        logger.info("pdf_image_extract_service command started | action=%s command=%s", action, command)
        result = self._command_runner(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            raise RuntimeError(
                f"{action}失败: {stderr or stdout or f'exit code {result.returncode}'}"
            )
        return result

    @staticmethod
    def _build_password_args(password: str | None) -> list[str]:
        if not password:
            return []
        return ["-opw", password, "-upw", password]

    @staticmethod
    def _safe_int(value: str) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
