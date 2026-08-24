import asyncio
import json
import sys
from pathlib import Path

from PIL import Image


def _add_src_path() -> None:
    src_path = Path(__file__).resolve().parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


class _StubImageBackend:
    name = "openai_compatible"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def transcribe(
        self,
        file_path,
        *,
        media_type,
        file_type,
        source_file_name,
        options,
    ):
        from document_parse_service.media_transcription.models import TranscriptResult

        self.calls.append(
            {
                "file_path": Path(file_path),
                "media_type": media_type,
                "file_type": file_type,
                "source_file_name": source_file_name,
                "options": dict(options),
            }
        )
        response = self.responses.pop(0)
        return TranscriptResult(
            text=response,
            backend=self.name,
            model="stub-vlm",
            metadata={"model_call_count": 1},
        )


def test_auto_router_crops_multi_object_image_into_batches(tmp_path: Path) -> None:
    _add_src_path()
    from document_parse_service.media_transcription.image_auto_router import (
        ImageAutoRouterBackend,
    )

    image_path = tmp_path / "objects.png"
    Image.new("RGB", (1000, 600), "white").save(image_path)
    backend = _StubImageBackend(
        [
            """
            {
              "scene_type": "multi_object",
              "confidence": 0.97,
              "object_count_estimate": 2,
              "objects": [
                {"id": "pill-1", "bbox": [50, 100, 450, 900]},
                {"id": "pill-2", "bbox": [550, 100, 950, 900]}
              ]
            }
            """,
            """
            {
              "objects": [
                {
                  "object_id": "pill-001",
                  "imprint": {
                    "observed": "A1",
                    "candidates": [],
                    "confidence": 0.95
                  },
                  "color": {"primary": ["white"], "confidence": 0.9},
                  "shape": {"value": "Round", "confidence": 0.9},
                  "dose_form_visual": "tablet",
                  "score_or_logo": false,
                  "evidence_notes": "clear"
                },
                {
                  "object_id": "pill-002",
                  "imprint": {
                    "observed": "NA",
                    "candidates": ["A7"],
                    "confidence": 0.4
                  },
                  "color": {"primary": ["yellow"], "confidence": 0.9},
                  "shape": {"value": "Oval", "confidence": 0.9},
                  "dose_form_visual": "tablet",
                  "score_or_logo": false,
                  "evidence_notes": "blurred"
                }
              ]
            }
            """,
        ]
    )
    router = ImageAutoRouterBackend(backend)
    asset_output_dir = tmp_path / "image_objects"

    result = asyncio.run(
        router.transcribe(
            image_path,
            media_type="image",
            file_type="png",
            source_file_name="objects",
            options={
                "mode": "auto",
                "intent": "identify_objects",
                "target_fields": ["imprint", "color", "shape"],
                "multi_object_batch_size": 6,
                "image_asset_output_dir": str(asset_output_dir),
            },
        )
    )

    assert len(backend.calls) == 2
    assert backend.calls[0]["file_path"] == image_path
    assert backend.calls[1]["file_type"] == "png"
    assert backend.calls[1]["file_path"].name == "objects-001.png"
    assert "逐字符置信度不低于 0.85" in backend.calls[1]["options"]["prompt"]
    assert '"candidates"' in backend.calls[1]["options"]["prompt"]
    assert "detect_crop_batch_vlm" in result.text
    assert '"observed": "A1"' in result.text
    assert '"review_reason": "imprint_not_observed"' in result.text
    assert result.metadata["image_scene_type"] == "multi_object"
    assert result.metadata["image_selected_pipeline"] == "detect_crop_batch_vlm"
    assert result.metadata["image_object_count"] == 2
    assert result.metadata["image_batch_count"] == 1
    assert result.metadata["object_count"] == 2
    assert result.metadata["ocr_region_count"] == 2
    assert result.metadata["model_call_count"] == 2
    assert result.metadata["image_query_ready_count"] == 1
    assert result.metadata["image_review_required_count"] == 1
    assert (asset_output_dir / "pill-001.png").exists()
    assert (asset_output_dir / "pill-002.png").exists()
    assert result.metadata["image_evidence"]["objects"][0]["crop_ref"] == (
        "image_objects/pill-001.png"
    )
    assert result.metadata["image_evidence"]["objects"][0]["search_input"] == {
        "imprint": "A1",
        "imprint_candidates": [],
        "color": "White",
        "shape": "Round",
    }


def test_auto_router_falls_back_to_whole_image_for_document_scene(
    tmp_path: Path,
) -> None:
    _add_src_path()
    from document_parse_service.media_transcription.image_auto_router import (
        ImageAutoRouterBackend,
    )

    image_path = tmp_path / "document.png"
    Image.new("RGB", (200, 300), "white").save(image_path)
    backend = _StubImageBackend(
        [
            """
            {
              "scene_type": "document",
              "confidence": 0.99,
              "object_count_estimate": 1,
              "objects": []
            }
            """,
            "# OCR result",
        ]
    )
    router = ImageAutoRouterBackend(backend)

    result = asyncio.run(
        router.transcribe(
            image_path,
            media_type="image",
            file_type="png",
            source_file_name="document",
            options={"mode": "auto", "prompt": "extract text"},
        )
    )

    assert len(backend.calls) == 2
    assert backend.calls[1]["options"]["prompt"] == "extract text"
    assert result.text == "# OCR result"
    assert result.metadata["image_scene_type"] == "document"
    assert result.metadata["image_selected_pipeline"] == "whole_image_vlm"
    assert result.metadata["image_routing_fallback_reason"] == (
        "scene_uses_whole_image_pipeline"
    )
    assert result.metadata["image_parse_mode"] == "auto"
    assert result.metadata["model_call_count"] == 2


def test_auto_router_retries_incomplete_structured_batch(tmp_path: Path) -> None:
    _add_src_path()
    from document_parse_service.media_transcription.image_auto_router import (
        ImageAutoRouterBackend,
    )

    image_path = tmp_path / "objects.png"
    Image.new("RGB", (1000, 600), "white").save(image_path)
    backend = _StubImageBackend(
        [
            """
            {
              "scene_type": "multi_object",
              "confidence": 0.97,
              "object_count_estimate": 2,
              "objects": [
                {"bbox": [50, 100, 450, 900]},
                {"bbox": [550, 100, 950, 900]}
              ]
            }
            """,
            "not-json",
            """
            {
              "objects": [
                {
                  "object_id": "pill-001",
                  "imprint": {"observed": "A1", "confidence": 0.95},
                  "color": {"primary": ["white"], "confidence": 0.9},
                  "shape": {"value": "Round", "confidence": 0.9}
                },
                {
                  "object_id": "pill-002",
                  "imprint": {"observed": "B2", "confidence": 0.95},
                  "color": {"primary": ["blue"], "confidence": 0.9},
                  "shape": {"value": "Oval", "confidence": 0.9}
                }
              ]
            }
            """,
        ]
    )

    result = asyncio.run(
        ImageAutoRouterBackend(backend).transcribe(
            image_path,
            media_type="image",
            file_type="png",
            source_file_name="objects",
            options={
                "mode": "auto",
                "image_extraction_profile": "pill_search",
            },
        )
    )

    assert len(backend.calls) == 3
    assert backend.calls[-1]["source_file_name"].endswith("-retry")
    assert result.metadata["model_call_count"] == 3
    assert result.metadata["model_retry_count"] == 1
    assert result.metadata["image_query_ready_count"] == 2


def test_whole_image_mode_skips_scene_detection(tmp_path: Path) -> None:
    _add_src_path()
    from document_parse_service.media_transcription.image_auto_router import (
        ImageAutoRouterBackend,
    )

    image_path = tmp_path / "single.png"
    Image.new("RGB", (100, 100), "white").save(image_path)
    backend = _StubImageBackend(["single image result"])
    router = ImageAutoRouterBackend(backend)

    result = asyncio.run(
        router.transcribe(
            image_path,
            media_type="image",
            file_type="png",
            source_file_name="single",
            options={"mode": "whole_image"},
        )
    )

    assert len(backend.calls) == 1
    assert result.metadata["image_selected_pipeline"] == "whole_image_vlm"
    assert result.metadata["image_scene_detection_call_count"] == 0


def test_image_metrics_include_scene_routing_details() -> None:
    _add_src_path()
    from document_parse_service.document_parse_metrics import build_parse_metrics

    metrics = build_parse_metrics(
        file_type="jpeg",
        input_bytes=100,
        output_char_count=20,
        asset_count=0,
        stage_durations_ms={"content_extract": 50},
        total_duration_ms=60,
        diagnostics={
            "metadata": {
                "object_count": 23,
                "ocr_region_count": 23,
                "batch_count": 4,
                "image_scene_type": "multi_object",
                "image_scene_confidence": 0.97,
                "image_selected_pipeline": "detect_crop_batch_vlm",
                "image_extraction_profile": "pill_search",
                "image_query_ready_count": 15,
                "image_review_required_count": 8,
                "image_evidence_schema_version": "image_visual_evidence/1.0",
                "image_evidence_file_path": "/workspace/evidence.json",
            }
        },
    )

    image_metrics = metrics["type_metrics"]["jpeg"]
    assert image_metrics["object_count"] == 23
    assert image_metrics["ocr_region_count"] == 23
    assert image_metrics["batch_count"] == 4
    assert image_metrics["scene_type"] == "multi_object"
    assert image_metrics["scene_confidence"] == 0.97
    assert image_metrics["selected_pipeline"] == "detect_crop_batch_vlm"
    assert image_metrics["extraction_profile"] == "pill_search"
    assert image_metrics["query_ready_count"] == 15
    assert image_metrics["review_required_count"] == 8
    assert image_metrics["evidence_schema_version"] == (
        "image_visual_evidence/1.0"
    )
    assert image_metrics["evidence_file_path"] == "/workspace/evidence.json"


def test_scene_parser_rejects_invalid_regions() -> None:
    _add_src_path()
    from document_parse_service.media_transcription.image_auto_router import (
        ImageAutoRouterBackend,
    )

    decision = ImageAutoRouterBackend._parse_scene_decision(
        """
        ```json
        {
          "scene_type": "multi_object",
          "confidence": 1.4,
          "object_count_estimate": 3,
          "objects": [
            {"id": "valid", "bbox": [-20, 100, 1100, 900]},
            {"id": "invalid", "bbox": [500, 500, 400, 600]},
            {"id": "missing"}
          ]
        }
        ```
        """,
        forced_multi_object=False,
        max_objects=32,
    )

    assert decision.scene_type == "multi_object"
    assert decision.confidence == 1.0
    assert decision.object_count_estimate == 3
    assert decision.objects[0].object_id == "pill-001"
    assert decision.objects[0].bbox == (0, 100, 1000, 900)
    assert len(decision.objects) == 1


def test_image_document_service_writes_evidence_sidecar(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _add_src_path()
    from document_parse_service import base_document_service
    from document_parse_service import media_document_service
    from document_parse_service.media_document_service import ImageDocumentService
    from document_parse_service.media_transcription.models import TranscriptResult

    monkeypatch.setattr(
        base_document_service,
        "DOCUMENT_PARSE_WORKSPACE",
        tmp_path,
    )
    monkeypatch.setattr(
        media_document_service,
        "DOCUMENT_PARSE_WORKSPACE",
        tmp_path,
    )

    class _EvidenceBackend:
        name = "openai_compatible"

        def __init__(self) -> None:
            self.options = {}

        async def transcribe(
            self,
            file_path,
            *,
            media_type,
            file_type,
            source_file_name,
            options,
        ):
            self.options = dict(options)
            asset_dir = Path(options["image_asset_output_dir"])
            asset_dir.mkdir(parents=True, exist_ok=True)
            asset_path = asset_dir / "pill-001.png"
            Image.new("RGB", (20, 20), "white").save(asset_path)
            return TranscriptResult(
                text="structured evidence",
                backend=self.name,
                model="stub-vlm",
                metadata={
                    "image_evidence": {
                        "schema_version": "image_visual_evidence/1.0",
                        "summary": {
                            "object_count": 1,
                            "query_ready_count": 1,
                            "review_required_count": 0,
                        },
                        "objects": [{"object_id": "pill-001"}],
                    },
                    "image_assets": [
                        {
                            "asset_id": "pill-001",
                            "object_id": "pill-001",
                            "bbox": [0, 0, 1000, 1000],
                            "local_path": str(asset_path),
                            "crop_ref": "image_objects/pill-001.png",
                        }
                    ],
                },
            )

    image_path = tmp_path / "input.png"
    Image.new("RGB", (100, 100), "white").save(image_path)
    backend = _EvidenceBackend()
    service = ImageDocumentService(
        file_type="png",
        backend=backend,
        backend_options={},
    )

    output_path = asyncio.run(
        service.parse_to_markdown(
            image_path,
            task_id="image-evidence",
            source_file_name="input",
        )
    )

    evidence_path = output_path.with_suffix(".evidence.json")
    assert evidence_path.exists()
    assert json.loads(evidence_path.read_text(encoding="utf-8"))["objects"] == [
        {"object_id": "pill-001"}
    ]
    assert backend.options["image_asset_output_dir"] == str(
        tmp_path / "image-evidence" / "image_objects"
    )
    metrics = json.loads(
        output_path.with_suffix(".metrics.json").read_text(encoding="utf-8")
    )
    assert metrics["io"]["asset_count"] == 1
