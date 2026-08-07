import sys
from pathlib import Path


def _add_src_path() -> None:
    src_path = Path(__file__).resolve().parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def test_media_only_env_content_does_not_create_afts_service() -> None:
    _add_src_path()
    from document_parse_service.service import DocumentParseService

    service = DocumentParseService()

    assert service._create_afts_service(
        {
            "media_parse_backend": "local",
            "media_parse_options": {"vad_filter": False},
        },
        required=False,
    ) is None


def test_required_afts_service_rejects_media_only_env_content() -> None:
    _add_src_path()
    from document_parse_service.service import DocumentParseService

    service = DocumentParseService()

    try:
        service._create_afts_service(
            {"media_parse_backend": "local"},
            required=True,
        )
    except ValueError as exc:
        assert "env_content is required" in str(exc)
    else:
        raise AssertionError("expected ValueError")
