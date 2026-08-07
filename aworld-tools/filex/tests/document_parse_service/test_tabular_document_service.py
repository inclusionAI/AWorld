import asyncio

from document_parse_service.tabular_document_service import CsvContentExtractor


def test_csv_extractor_uses_structural_delimiter_detection(tmp_path):
    source = tmp_path / "business_hours.csv"
    source.write_text(
        "placeID,hours,days\n"
        "135111,00:00-23:30;,Mon;Tue;Wed;Thu;Fri;\n"
        "132557,16:30-22:00;11:00-22:00;11:00-23:00;04:30-23:00;,Sun;\n"
        "132560,08:00-12:00;,Mon;Tue;Wed;Thu;Fri;\n",
        encoding="utf-8",
    )

    markdown, metrics = asyncio.run(CsvContentExtractor().extract_content(source))

    assert metrics["delimiter"] == ","
    assert metrics["delimiter_detection"] == "csv_sniffer_validated"
    assert metrics["parser_engine"] == "c"
    assert metrics["fallback_reason"] == ""
    assert metrics["row_count"] == 3
    assert metrics["column_count"] == 3
    assert "00:00-23:30;" in markdown
    assert "Mon;Tue;Wed;Thu;Fri;" in markdown


def test_csv_extractor_falls_back_to_python_engine_without_skipping_rows(tmp_path, monkeypatch):
    source = tmp_path / "orders.csv"
    source.write_text("order_id,amount\nO-1,12.50\nO-2,19.00\n", encoding="utf-8")
    extractor = CsvContentExtractor()
    original_read_csv = __import__("pandas").read_csv

    def read_csv_with_c_failure(*args, engine=None, **kwargs):
        if engine == "c":
            raise __import__("pandas").errors.ParserError("forced C parser failure")
        return original_read_csv(*args, engine=engine, **kwargs)

    monkeypatch.setattr("document_parse_service.tabular_document_service.pd.read_csv", read_csv_with_c_failure)

    _, metrics = asyncio.run(extractor.extract_content(source))

    assert metrics["parser_engine"] == "python"
    assert metrics["fallback_reason"] == "c_parser_error"
    assert metrics["row_count"] == 2
    assert metrics["column_count"] == 2
