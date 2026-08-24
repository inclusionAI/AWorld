import json

from document_parse_service import cli
from document_parse_service.document_artifact_models import MarkdownArtifact
from document_parse_service.pdf.pdf_batch_checkpoint import PdfBatchCheckpointStore


def test_pdf_batch_progress_snapshot_is_merged_and_read_atomically(tmp_path) -> None:
    store = PdfBatchCheckpointStore(tmp_path, "resume-1")

    store.write_progress(status="parsing", completed_pages=10, total_pages=40)
    snapshot = store.write_progress(completed_pages=20, completed_batches=2)

    assert snapshot == {
        "status": "parsing",
        "completed_pages": 20,
        "total_pages": 40,
        "completed_batches": 2,
    }
    assert store.read_progress() == snapshot
    assert not (tmp_path / "resume-1" / "progress.json.tmp").exists()


def test_pdf_batch_checkpoint_returns_incremental_markdown_after_cursor(tmp_path) -> None:
    store = PdfBatchCheckpointStore(tmp_path, "resume-incremental")
    store.write_progress(
        status="parsing",
        completed_pages=2,
        total_pages=4,
        completed_batches=1,
        total_batches=2,
    )
    store.save(
        batch_index=1,
        pages=[1, 2],
        artifact=MarkdownArtifact(markdown_text="第一页\n\n第二页"),
    )

    first = store.read_incremental_results(after_batch_index=0)

    assert first == [
        {
            "batch_index": 1,
            "pages": [1, 2],
            "status": "succeeded",
            "is_last_batch": False,
            "is_final": False,
            "markdown": "第一页\n\n第二页",
            "output_char_count": 8,
            "asset_count": 0,
            "assets_pending": False,
        }
    ]
    assert store.read_incremental_results(after_batch_index=1) == []

    store.save(
        batch_index=2,
        pages=[3, 4],
        artifact=MarkdownArtifact(markdown_text="第三页\n\n第四页"),
    )
    store.write_progress(
        status="succeeded",
        completed_pages=4,
        completed_batches=2,
    )

    final = store.read_incremental_results(after_batch_index=1)

    assert final[0]["batch_index"] == 2
    assert final[0]["pages"] == [3, 4]
    assert final[0]["is_last_batch"] is True
    assert final[0]["is_final"] is True
    assert final[0]["markdown"] == "第三页\n\n第四页"


def test_filex_status_exposes_incremental_cursor_and_failure_details(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(cli, "DOCUMENT_PARSE_WORKSPACE", tmp_path)
    store = PdfBatchCheckpointStore(tmp_path / "pdf_batch_checkpoints", "resume-cli")
    store.write_progress(
        status="failed",
        completed_pages=2,
        total_pages=4,
        completed_batches=1,
        total_batches=2,
        failed_batch=2,
        error="provider timeout",
    )
    store.save(
        batch_index=1,
        pages=[1, 2],
        artifact=MarkdownArtifact(markdown_text="已经完成的正文"),
    )

    exit_code = cli.main(
        [
            "status",
            "--batch-resume-id=resume-cli",
            "--include-results",
            "--after-batch=0",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["incremental_result"]["cursor"] == 1
    assert payload["incremental_result"]["is_final"] is True
    assert payload["incremental_result"]["failed_batch"] == 2
    assert payload["incremental_result"]["error"] == "provider timeout"
    assert payload["incremental_result"]["batches"][0]["markdown"] == "已经完成的正文"
