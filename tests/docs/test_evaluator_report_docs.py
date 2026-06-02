from __future__ import annotations

from pathlib import Path


def test_evaluator_report_command_doc_covers_schema_and_validation() -> None:
    doc_path = Path("docs/AWorld CLI/Commands/Evaluator.md")
    overview_path = Path("docs/AWorld CLI/Commands/Overview.md")

    content = doc_path.read_text(encoding="utf-8")
    overview = overview_path.read_text(encoding="utf-8")

    assert "aworld-cli evaluator" in content
    assert "--print-report-schema" in content
    assert "--validate-report" in content
    assert "report_format" in content
    assert "automation" in content
    assert "Evaluator" in overview


def test_evaluator_report_example_includes_stable_contract_fields() -> None:
    example_path = Path("examples/aworld_quick_start/cli/evaluator_report.example.json")
    recipe_path = Path("docs/AWorld CLI/Recipes/Mini App Build.md")

    content = example_path.read_text(encoding="utf-8")
    recipe = recipe_path.read_text(encoding="utf-8")

    assert '"report_format"' in content
    assert '"generated_at"' in content
    assert '"metrics"' in content
    assert '"automation"' in content
    assert "--validate-report" in recipe
    assert "--print-report-schema" in recipe
