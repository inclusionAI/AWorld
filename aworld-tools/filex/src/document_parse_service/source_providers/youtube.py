"""YouTube source discovery and transcript-first acquisition."""

from __future__ import annotations

import html
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import SourceResolution

_YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
    }
)
_SUBTITLE_HOST_SUFFIXES = (".youtube.com", ".googlevideo.com")
_RIGHTS_BASES = frozenset(
    {"user-owned", "licensed", "service-permitted", "applicable-law"}
)
_TRANSCRIPT_URL_PATTERN = re.compile(
    r"(?im)^\s*[*-]?\s*(?:full\s+)?transcript\s*:\s*(https?://\S+)"
)
_TIMING_PATTERN = re.compile(
    r"(?P<start>(?:\d{2}:)?\d{2}:\d{2}\.\d{3})\s+-->\s+"
    r"(?P<end>(?:\d{2}:)?\d{2}:\d{2}\.\d{3})"
)
_TAG_PATTERN = re.compile(r"<[^>]+>")


class YouTubeSourceProvider:
    """Inspect YouTube URLs and resolve the cheapest usable transcript source."""

    name = "youtube"
    version = "1"

    @staticmethod
    def supports(raw_url: str) -> bool:
        parsed = urlparse(str(raw_url or "").strip())
        return (
            parsed.scheme in {"http", "https"}
            and (parsed.hostname or "").lower() in _YOUTUBE_HOSTS
        )

    def inspect(self, raw_url: str) -> dict[str, Any]:
        info, yt_dlp_version = self._extract_info(raw_url)
        return self._inspection_from_info(
            info,
            yt_dlp_version=yt_dlp_version,
            raw_url=raw_url,
        )

    def _inspection_from_info(
        self,
        info: dict[str, Any],
        *,
        yt_dlp_version: str,
        raw_url: str,
    ) -> dict[str, Any]:
        human_subtitles = self._language_keys(info.get("subtitles"))
        automatic_captions = self._language_keys(info.get("automatic_captions"))
        transcript_urls = self._publisher_transcript_urls(
            str(info.get("description") or "")
        )
        recommended_route: list[str] = []
        if human_subtitles:
            recommended_route.append("youtube_subtitle")
        if automatic_captions:
            recommended_route.append("youtube_automatic_caption")
        recommended_route.append("local_whisper")
        return {
            "schema_version": "1.0",
            "source_provider": self.name,
            "source_provider_version": self.version,
            "extractor": "yt-dlp",
            "extractor_version": yt_dlp_version,
            "source_url": str(info.get("webpage_url") or raw_url),
            "video_id": str(info.get("id") or ""),
            "title": str(info.get("title") or ""),
            "channel": str(info.get("channel") or info.get("uploader") or ""),
            "channel_id": str(info.get("channel_id") or ""),
            "published_at": self._format_upload_date(info.get("upload_date")),
            "duration_seconds": self._optional_float(info.get("duration")),
            "language": str(info.get("language") or ""),
            "availability": str(info.get("availability") or ""),
            "live_status": str(info.get("live_status") or ""),
            "chapters": self._chapters(info.get("chapters")),
            "human_subtitles": human_subtitles,
            "automatic_captions": automatic_captions,
            "publisher_transcripts": transcript_urls,
            "discovered_routes": (["publisher_transcript"] if transcript_urls else []),
            "unavailable_routes": (
                [
                    {
                        "provider": "publisher_transcript",
                        "reason": "external_html_ingestion_disabled",
                    }
                ]
                if transcript_urls
                else []
            ),
            "recommended_mode": "transcript",
            "recommended_route": recommended_route,
            "media_download_required": not bool(human_subtitles or automatic_captions),
        }

    def resolve(
        self,
        raw_url: str,
        *,
        output_root: Path,
        language: str = "",
        allow_media_download: bool = False,
        rights_basis: str = "",
    ) -> SourceResolution:
        info, yt_dlp_version = self._extract_info(raw_url)
        inspection = self._inspection_from_info(
            info,
            yt_dlp_version=yt_dlp_version,
            raw_url=raw_url,
        )
        video_id = self._safe_component(inspection["video_id"])
        target_dir = output_root / f"youtube_{video_id}"
        target_dir.mkdir(parents=True, exist_ok=True)

        selected = self._select_subtitle(info, language=language)
        warnings: list[str] = []

        if selected is not None:
            track_kind, track_language, track = selected
            if inspection["publisher_transcripts"]:
                warnings.append(
                    "publisher transcript discovered but external HTML ingestion "
                    "is not enabled; "
                    "using a YouTube subtitle track"
                )
            vtt_text = self._download_subtitle(track)
            markdown = self._subtitle_markdown(
                inspection=inspection,
                vtt_text=vtt_text,
                track_kind=track_kind,
                track_language=track_language,
            )
            local_path = target_dir / f"youtube_{video_id}.md"
            local_path.write_text(markdown, encoding="utf-8")
            manifest = {
                **inspection,
                "selected_route": track_kind,
                "selected_language": track_language,
                "media_downloaded": False,
                "rights_basis": "",
                "warnings": warnings,
            }
            return SourceResolution(
                local_path=local_path, file_type="md", manifest=manifest
            )

        if not allow_media_download:
            raise ValueError(
                "youtube source has no usable subtitle track; rerun with "
                "--allow-media-download and --rights-basis, or provide a local "
                "media file"
            )
        normalized_rights_basis = str(rights_basis or "").strip().lower()
        if normalized_rights_basis not in _RIGHTS_BASES:
            raise ValueError(
                "rights_basis must be one of: " + ", ".join(sorted(_RIGHTS_BASES))
            )
        if inspection["publisher_transcripts"]:
            warnings.append(
                "publisher transcript discovered but external HTML ingestion "
                "is not enabled; "
                "falling back to authorized audio acquisition"
            )
        local_path = self._download_audio(
            raw_url, target_dir=target_dir, video_id=video_id
        )
        manifest = {
            **inspection,
            "selected_route": "local_whisper",
            "selected_language": str(language or inspection.get("language") or ""),
            "media_downloaded": True,
            "rights_basis": normalized_rights_basis,
            "warnings": warnings,
        }
        return SourceResolution(
            local_path=local_path, file_type="mp3", manifest=manifest
        )

    @staticmethod
    def _extract_info(raw_url: str) -> tuple[dict[str, Any], str]:
        try:
            import yt_dlp
            from yt_dlp.version import __version__
        except ImportError as exc:
            raise RuntimeError(
                "youtube source provider requires yt-dlp. "
                "Install FileX dependencies again."
            ) from exc

        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            **YouTubeSourceProvider._network_options(),
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(raw_url, download=False)
        if not isinstance(info, dict):
            raise RuntimeError("yt-dlp returned an unsupported metadata payload")
        return info, __version__

    @staticmethod
    def _language_keys(raw_tracks: Any) -> list[str]:
        if not isinstance(raw_tracks, dict):
            return []
        return sorted(str(key) for key, tracks in raw_tracks.items() if tracks)

    @staticmethod
    def _publisher_transcript_urls(description: str) -> list[str]:
        urls: list[str] = []
        for match in _TRANSCRIPT_URL_PATTERN.finditer(description):
            url = match.group(1).rstrip(".,;:)]}")
            if url not in urls:
                urls.append(url)
        return urls

    @staticmethod
    def _format_upload_date(raw_value: Any) -> str:
        value = str(raw_value or "")
        if len(value) == 8 and value.isdigit():
            return f"{value[:4]}-{value[4:6]}-{value[6:]}"
        return value

    @staticmethod
    def _optional_float(raw_value: Any) -> float | None:
        try:
            return float(raw_value) if raw_value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _chapters(raw_chapters: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_chapters, list):
            return []
        chapters: list[dict[str, Any]] = []
        for raw in raw_chapters:
            if not isinstance(raw, dict):
                continue
            chapters.append(
                {
                    "title": str(raw.get("title") or ""),
                    "start_seconds": YouTubeSourceProvider._optional_float(
                        raw.get("start_time")
                    ),
                    "end_seconds": YouTubeSourceProvider._optional_float(
                        raw.get("end_time")
                    ),
                }
            )
        return chapters

    @staticmethod
    def _select_subtitle(
        info: dict[str, Any],
        *,
        language: str,
    ) -> tuple[str, str, dict[str, Any]] | None:
        preferred_languages = [
            str(language or "").strip(),
            str(info.get("language") or "").strip(),
            "en",
        ]
        for kind, field in (
            ("youtube_subtitle", "subtitles"),
            ("youtube_automatic_caption", "automatic_captions"),
        ):
            raw_tracks = info.get(field)
            if not isinstance(raw_tracks, dict) or not raw_tracks:
                continue
            available_languages = [
                str(key) for key, tracks in raw_tracks.items() if tracks
            ]
            selected_language = next(
                (
                    candidate
                    for candidate in preferred_languages
                    if candidate in available_languages
                ),
                available_languages[0] if available_languages else "",
            )
            tracks = raw_tracks.get(selected_language) or []
            if not isinstance(tracks, list):
                continue
            track = next(
                (
                    item
                    for item in tracks
                    if isinstance(item, dict)
                    and item.get("ext") == "vtt"
                    and item.get("url")
                ),
                None,
            )
            if track is not None:
                return kind, selected_language, track
        return None

    @staticmethod
    def _download_subtitle(track: dict[str, Any]) -> str:
        url = str(track.get("url") or "").strip()
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "youtube.com"
            or hostname == "googlevideo.com"
            or hostname.endswith(_SUBTITLE_HOST_SUFFIXES)
        ):
            raise ValueError("youtube subtitle URL resolved to an unsupported host")
        max_bytes = max(
            1, int(os.getenv("FILEX_MAX_SUBTITLE_BYTES", str(32 * 1024 * 1024)))
        )
        timeout = max(1, int(os.getenv("FILEX_SOURCE_TIMEOUT_SECONDS", "20")))
        request = Request(url, headers={"User-Agent": "AWorld-FileX/1.0"})
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError(f"subtitle exceeds FILEX_MAX_SUBTITLE_BYTES ({max_bytes})")
        return body.decode("utf-8", errors="replace")

    @staticmethod
    def _subtitle_markdown(
        *,
        inspection: dict[str, Any],
        vtt_text: str,
        track_kind: str,
        track_language: str,
    ) -> str:
        lines = [
            f"# {inspection.get('title') or inspection.get('video_id')}",
            "",
            "## Source",
            "",
            f"- URL: {inspection.get('source_url')}",
            f"- Channel: {inspection.get('channel')}",
            f"- Published: {inspection.get('published_at')}",
            "- Duration: "
            f"{YouTubeSourceProvider._timestamp(inspection.get('duration_seconds'))}",
            f"- Transcript source: {track_kind}",
            f"- Language: {track_language}",
        ]
        chapters = inspection.get("chapters") or []
        if chapters:
            lines.extend(["", "## Chapters", ""])
            for chapter in chapters:
                start = YouTubeSourceProvider._timestamp(chapter.get("start_seconds"))
                lines.append(f"- [{start}] {chapter.get('title') or 'Untitled'}")
        lines.extend(["", "## Transcript", ""])
        lines.extend(YouTubeSourceProvider._vtt_timeline(vtt_text))
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _vtt_timeline(vtt_text: str) -> list[str]:
        timeline: list[str] = []
        previous_text = ""
        for block in re.split(r"\r?\n\s*\r?\n", vtt_text):
            block_lines = [line.strip() for line in block.splitlines() if line.strip()]
            timing_index = next(
                (
                    index
                    for index, line in enumerate(block_lines)
                    if _TIMING_PATTERN.search(line)
                ),
                None,
            )
            if timing_index is None:
                continue
            timing = _TIMING_PATTERN.search(block_lines[timing_index])
            if timing is None:
                continue
            text = " ".join(block_lines[timing_index + 1 :])
            text = html.unescape(_TAG_PATTERN.sub("", text)).strip()
            if not text or text == previous_text:
                continue
            start = YouTubeSourceProvider._normalize_vtt_time(timing.group("start"))
            end = YouTubeSourceProvider._normalize_vtt_time(timing.group("end"))
            timeline.append(f"- [{start}-{end}] {text}")
            previous_text = text
        if not timeline:
            raise RuntimeError("youtube subtitle track did not contain any timed cues")
        return timeline

    @staticmethod
    def _normalize_vtt_time(value: str) -> str:
        parts = value.split(":")
        if len(parts) == 2:
            return f"00:{parts[0]}:{parts[1].split('.')[0]}"
        return f"{parts[0]}:{parts[1]}:{parts[2].split('.')[0]}"

    @staticmethod
    def _timestamp(raw_seconds: Any) -> str:
        seconds = max(0, int(float(raw_seconds or 0)))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _safe_component(raw_value: Any) -> str:
        value = re.sub(r"[^A-Za-z0-9_-]+", "_", str(raw_value or "")).strip("_")
        return value[:128] or "unknown"

    @staticmethod
    def _download_audio(raw_url: str, *, target_dir: Path, video_id: str) -> Path:
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("youtube audio acquisition requires yt-dlp") from exc
        output_template = str(target_dir / f"youtube_{video_id}.%(ext)s")
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": "bestaudio/best",
            "outtmpl": output_template,
            **YouTubeSourceProvider._network_options(),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([raw_url])
        output_path = target_dir / f"youtube_{video_id}.mp3"
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("yt-dlp completed without producing a non-empty MP3")
        return output_path

    @staticmethod
    def _network_options() -> dict[str, int]:
        timeout = max(1, int(os.getenv("FILEX_SOURCE_TIMEOUT_SECONDS", "20")))
        retries = max(0, int(os.getenv("FILEX_SOURCE_RETRIES", "1")))
        return {
            "socket_timeout": timeout,
            "retries": retries,
            "extractor_retries": retries,
            "fragment_retries": retries,
        }
