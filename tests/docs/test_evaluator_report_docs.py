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
    assert "aworld-cli evaluator --input" in content
    assert "--kind task" in content
    assert "--kind answer" in content
    assert "--kind trajectory" in content
    assert "--judge-agent-name" in content
    assert "--judge-backend-ref" in content
    assert "exactly one judge selector" in content
    assert "report_format" in content
    assert "automation" in content
    assert ".aworld/evaluators/*.json" in content
    assert "declared_evaluator_suite.example.json" in content
    assert "get_declared_evaluator_suite_schema()" in content
    assert "Evaluator" in overview


def test_evaluator_report_example_includes_stable_contract_fields() -> None:
    example_path = Path("examples/aworld_quick_start/cli/evaluator_report.example.json")
    manifest_example_path = Path("examples/aworld_quick_start/cli/declared_evaluator_suite.example.json")
    recipe_path = Path("docs/AWorld CLI/Recipes/Mini App Build.md")
    readme_path = Path("examples/aworld_quick_start/cli/README.md")

    content = example_path.read_text(encoding="utf-8")
    manifest_example = manifest_example_path.read_text(encoding="utf-8")
    recipe = recipe_path.read_text(encoding="utf-8")
    readme = readme_path.read_text(encoding="utf-8")

    assert '"report_format"' in content
    assert '"generated_at"' in content
    assert '"metrics"' in content
    assert '"automation"' in content
    assert '"suite_id"' in manifest_example
    assert '"base_suite": "app-evaluator"' in manifest_example
    assert '"target_kinds"' in manifest_example
    assert '"gate_policy"' in manifest_example
    assert ".aworld/evaluators/*.json" in readme
    assert "declared_evaluator_suite.example.json" in readme
    assert "--validate-report" in recipe
    assert "--print-report-schema" in recipe
