"""
PDF 版面提取服务。

使用系统 `pdftohtml -xml` 提取 PDF 页内文本框与图片框坐标，
供 Markdown 回填图片位置时使用。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

from ..paths import DOCUMENT_PARSE_WORKSPACE

logger = logging.getLogger(__name__)


class PdfLayoutExtractService:
    """封装 PDF 版面坐标提取逻辑。"""

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
            raise ValueError(f"仅支持 PDF 版面提取: {file_path}")

        output_dir = self._workspace_base / task_id / "pdf_layout"
        self._prepare_output_dir(output_dir)
        output_prefix = output_dir / source_file_name
        xml_path = Path(f"{output_prefix}.xml")

        command = ["pdftohtml", "-xml", "-nodrm", "-hidden", "-noframes"]
        command.extend(self._build_password_args(password))
        command.extend([str(file_path), str(output_prefix)])
        self._run_command(command, "提取 PDF 版面")

        if not xml_path.exists():
            raise RuntimeError(f"pdftohtml 未生成布局 XML: {xml_path}")

        pages = self._parse_layout_xml(xml_path)
        flat_images = [
            image
            for page in pages
            for image in page["images"]
        ]

        logger.info(
            "pdf_layout_extract_service extracted layout | file_path=%s task_id=%s page_count=%s image_count=%s xml_path=%s",
            file_path,
            task_id,
            len(pages),
            len(flat_images),
            xml_path,
        )
        return {
            "page_count": len(pages),
            "image_count": len(flat_images),
            "pages": pages,
            "images": flat_images,
            "xml_path": xml_path,
            "output_dir": output_dir,
        }

    def _prepare_output_dir(self, output_dir: Path) -> None:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    def _parse_layout_xml(self, xml_path: Path) -> list[dict[str, Any]]:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        pages: list[dict[str, Any]] = []

        for page_element in root.findall("page"):
            page_number = self._safe_int(page_element.get("number")) or 0
            page_width = self._safe_int(page_element.get("width")) or 0
            page_height = self._safe_int(page_element.get("height")) or 0

            texts: list[dict[str, Any]] = []
            images: list[dict[str, Any]] = []
            order = 0

            for child in page_element:
                if child.tag == "text":
                    text_content = self._extract_text(child)
                    if not text_content:
                        continue
                    texts.append(
                        {
                            "page_number": page_number,
                            "order": order,
                            "top": self._safe_int(child.get("top")) or 0,
                            "left": self._safe_int(child.get("left")) or 0,
                            "width": self._safe_int(child.get("width")) or 0,
                            "height": self._safe_int(child.get("height")) or 0,
                            "text": text_content,
                        }
                    )
                    order += 1
                    continue

                if child.tag == "image":
                    image_path = Path(child.get("src") or "")
                    if not image_path.exists():
                        logger.warning(
                            "pdf_layout_extract_service layout image file missing | xml_path=%s image_path=%s",
                            xml_path,
                            image_path,
                        )
                        continue
                    images.append(
                        {
                            "page_number": page_number,
                            "order": order,
                            "top": self._safe_int(child.get("top")) or 0,
                            "left": self._safe_int(child.get("left")) or 0,
                            "width": self._safe_int(child.get("width")) or 0,
                            "height": self._safe_int(child.get("height")) or 0,
                            "path": image_path,
                            "name": image_path.name,
                        }
                    )
                    order += 1

            pages.append(
                {
                    "page_number": page_number,
                    "width": page_width,
                    "height": page_height,
                    "texts": sorted(texts, key=lambda item: (item["top"], item["left"], item["order"])),
                    "images": sorted(images, key=lambda item: (item["top"], item["left"], item["order"])),
                }
            )

        return pages

    def _run_command(self, command: list[str], action: str) -> subprocess.CompletedProcess[str]:
        logger.info("pdf_layout_extract_service command started | action=%s command=%s", action, command)
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
    def _extract_text(element: ET.Element) -> str:
        text = "".join(element.itertext()).replace("\xa0", " ")
        return " ".join(text.split()).strip()

    @staticmethod
    def _build_password_args(password: str | None) -> list[str]:
        if not password:
            return []
        return ["-opw", password, "-upw", password]

    @staticmethod
    def _safe_int(value: str | None) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
