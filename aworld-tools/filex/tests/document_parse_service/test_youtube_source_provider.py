from __future__ import annotations

from pathlib import Path

import pytest
from document_parse_service.source_providers.youtube import YouTubeSourceProvider


def _info(*, subtitles=True, automatic_captions=True):
    return {
        "id": "abc123",
        "title": "A useful interview",
        "channel": "Example Channel",
        "channel_id": "channel-1",
        "upload_date": "20260815",
        "duration": 125.8,
        "language": "en",
        "availability": "public",
        "live_status": "not_live",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "description": "Episode links\n* Transcript: https://publisher.example/transcript\n",
        "chapters": [{"title": "Opening", "start_time": 0, "end_time": 30}],
        "subtitles": {
            "en": [
                {"ext": "vtt", "url": "https://www.youtube.com/api/timedtext?id=abc123"}
            ]
        }
        if subtitles
        else {},
        "automatic_captions": {
            "zh-Hans": [
                {
                    "ext": "vtt",
                    "url": "https://www.youtube.com/api/timedtext?id=abc123&lang=zh",
                }
            ]
        }
        if automatic_captions
        else {},
    }


def test_supports_standard_youtube_urls() -> None:
    assert YouTubeSourceProvider.supports("https://www.youtube.com/watch?v=abc123")
    assert YouTubeSourceProvider.supports("https://youtu.be/abc123")
    assert not YouTubeSourceProvider.supports("https://example.com/watch?v=abc123")


def test_safe_component_rejects_path_syntax() -> None:
    assert YouTubeSourceProvider._safe_component("../../video/id") == "video_id"
    assert YouTubeSourceProvider._safe_component("") == "unknown"


def test_inspect_reports_transcript_first_routes(monkeypatch) -> None:
    monkeypatch.setattr(
        YouTubeSourceProvider,
        "_extract_info",
        staticmethod(lambda _url: (_info(), "2026.08.15")),
    )

    result = YouTubeSourceProvider().inspect("https://youtu.be/abc123")

    assert result["video_id"] == "abc123"
    assert result["duration_seconds"] == 125.8
    assert result["published_at"] == "2026-08-15"
    assert result["publisher_transcripts"] == ["https://publisher.example/transcript"]
    assert result["discovered_routes"] == ["publisher_transcript"]
    assert result["unavailable_routes"] == [
        {
            "provider": "publisher_transcript",
            "reason": "external_html_ingestion_disabled",
        }
    ]
    assert result["recommended_route"][:2] == [
        "youtube_subtitle",
        "youtube_automatic_caption",
    ]
    assert result["media_download_required"] is False


def test_resolve_writes_timed_markdown_without_media_download(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        YouTubeSourceProvider,
        "_extract_info",
        staticmethod(lambda _url: (_info(), "2026.08.15")),
    )
    monkeypatch.setattr(
        YouTubeSourceProvider,
        "_download_subtitle",
        staticmethod(
            lambda _track: (
                """WEBVTT

00:00:00.000 --> 00:00:03.000
Hello &amp; welcome

00:00:03.000 --> 00:00:06.500
This is <c>FileX</c>.
"""
            )
        ),
    )

    result = YouTubeSourceProvider().resolve(
        "https://www.youtube.com/watch?v=abc123",
        output_root=tmp_path,
    )

    assert result.file_type == "md"
    assert result.manifest["selected_route"] == "youtube_subtitle"
    assert result.manifest["media_downloaded"] is False
    markdown = result.local_path.read_text(encoding="utf-8")
    assert "## Chapters" in markdown
    assert "[00:00:00] Opening" in markdown
    assert "[00:00:00-00:00:03] Hello & welcome" in markdown
    assert "[00:00:03-00:00:06] This is FileX." in markdown


def test_resolve_requires_explicit_rights_before_audio_download(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        YouTubeSourceProvider,
        "_extract_info",
        staticmethod(
            lambda _url: (
                _info(subtitles=False, automatic_captions=False),
                "2026.08.15",
            )
        ),
    )

    with pytest.raises(ValueError, match="rights_basis"):
        YouTubeSourceProvider().resolve(
            "https://www.youtube.com/watch?v=abc123",
            output_root=tmp_path,
            allow_media_download=True,
        )


def test_vtt_parser_rejects_empty_tracks() -> None:
    with pytest.raises(RuntimeError, match="timed cues"):
        YouTubeSourceProvider._vtt_timeline("WEBVTT\n")


def test_network_options_are_bounded_and_configurable(monkeypatch) -> None:
    monkeypatch.setenv("FILEX_SOURCE_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("FILEX_SOURCE_RETRIES", "0")

    assert YouTubeSourceProvider._network_options() == {
        "socket_timeout": 7,
        "retries": 0,
        "extractor_retries": 0,
        "fragment_retries": 0,
    }
