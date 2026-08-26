"""Shared one-based PDF page selection for FileX providers."""

from __future__ import annotations

from pathlib import Path


def parse_page_selection(value: str | list[int] | tuple[int, ...] | None) -> list[int]:
    """Return ordered, de-duplicated one-based page numbers."""
    if value in (None, "", []):
        return []
    if isinstance(value, (list, tuple)):
        raw_pages = value
    else:
        raw_pages: list[int] = []
        for token in str(value).split(","):
            item = token.strip()
            if not item:
                continue
            if "-" not in item:
                raw_pages.append(_positive_page(item))
                continue
            start_text, end_text = item.split("-", 1)
            start = _positive_page(start_text)
            end = _positive_page(end_text)
            if end < start:
                raise ValueError(f"invalid descending page range: {item}")
            raw_pages.extend(range(start, end + 1))

    pages: list[int] = []
    seen: set[int] = set()
    for raw_page in raw_pages:
        page = _positive_page(raw_page)
        if page not in seen:
            seen.add(page)
            pages.append(page)
    return pages


def create_pdf_subset(source_path: Path, output_path: Path, pages: list[int]) -> tuple[Path, int]:
    """Create a PDF containing selected pages in caller-specified order."""
    if not pages:
        return source_path, _pdf_page_count(source_path)
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:
        raise RuntimeError("PDF page selection requires pypdf") from exc

    reader = PdfReader(str(source_path))
    page_count = len(reader.pages)
    invalid_pages = [page for page in pages if page > page_count]
    if invalid_pages:
        raise ValueError(
            f"requested PDF page out of range: {invalid_pages}; page_count={page_count}"
        )
    writer = PdfWriter()
    for page in pages:
        writer.add_page(reader.pages[page - 1])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as output_file:
        writer.write(output_file)
    return output_path, page_count


def _pdf_page_count(path: Path) -> int:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF page inspection requires pypdf") from exc
    try:
        return len(PdfReader(str(path)).pages)
    except Exception:
        return 0


def _positive_page(value: object) -> int:
    try:
        page = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid PDF page number: {value}") from exc
    if page < 1:
        raise ValueError(f"PDF page numbers are one-based: {page}")
    return page
