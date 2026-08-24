from pathlib import Path

import pytest

from document_parse_service.pdf_page_selection import create_pdf_subset, parse_page_selection


def test_parse_page_selection_supports_ranges_order_and_deduplication() -> None:
    assert parse_page_selection("3,1-2,2,5") == [3, 1, 2, 5]


@pytest.mark.parametrize("value", ["0", "3-1", "a"])
def test_parse_page_selection_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_page_selection(value)


def test_create_pdf_subset_keeps_requested_order(tmp_path: Path) -> None:
    from pypdf import PdfReader, PdfWriter

    source_path = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=300, height=300)
    with source_path.open("wb") as source_file:
        writer.write(source_file)

    subset_path, source_page_count = create_pdf_subset(
        source_path,
        tmp_path / "subset.pdf",
        [3, 1],
    )

    subset = PdfReader(str(subset_path))
    assert source_page_count == 3
    assert len(subset.pages) == 2
    assert float(subset.pages[0].mediabox.width) == 300
    assert float(subset.pages[1].mediabox.width) == 100


def test_create_pdf_subset_rejects_out_of_range_page(tmp_path: Path) -> None:
    from pypdf import PdfWriter

    source_path = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with source_path.open("wb") as source_file:
        writer.write(source_file)

    with pytest.raises(ValueError, match="out of range"):
        create_pdf_subset(source_path, tmp_path / "subset.pdf", [2])
