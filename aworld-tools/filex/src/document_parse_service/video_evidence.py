"""Bounded FFmpeg video probing, scene sampling, and storyboard evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

_PTS_TIME_RE = re.compile(r"\bpts_time:([0-9]+(?:\.[0-9]+)?)")


@dataclass(slots=True)
class VideoEvidenceResult:
    evidence: dict[str, Any]
    storyboard_path: Path
    frame_paths: list[Path]


class FfmpegVideoEvidenceExtractor:
    """Create a small, timestamped visual index without loading a video model."""

    async def extract(
        self,
        file_path: Path,
        *,
        output_dir: Path,
        source_file_name: str,
        options: dict[str, Any] | None = None,
    ) -> VideoEvidenceResult:
        return await asyncio.to_thread(
            self._extract_sync,
            file_path,
            output_dir,
            source_file_name,
            options or {},
        )

    def _extract_sync(
        self,
        file_path: Path,
        output_dir: Path,
        source_file_name: str,
        options: dict[str, Any],
    ) -> VideoEvidenceResult:
        ffmpeg = str(
            options.get("ffmpeg_path") or os.getenv("FILEX_FFMPEG_PATH") or "ffmpeg"
        )
        ffprobe = str(
            options.get("ffprobe_path") or os.getenv("FILEX_FFPROBE_PATH") or "ffprobe"
        )
        max_frames = self._bounded_int(options.get("video_max_frames"), 12, 1, 48)
        frame_width = self._bounded_int(
            options.get("video_frame_width"), 640, 160, 1280
        )
        uniform_interval = self._bounded_float(
            options.get("video_uniform_interval_seconds"), 30.0, 5.0, 600.0
        )
        scene_threshold = self._bounded_float(
            options.get("video_scene_threshold"), 0.25, 0.01, 0.99
        )
        timeout_seconds = self._bounded_int(
            options.get("video_analysis_timeout_seconds"), 180, 10, 3600
        )

        probe = self._probe(ffprobe, file_path, timeout_seconds)
        duration = float(probe.get("duration") or 0.0)
        video_duration = float(
            (probe.get("video") or {}).get("duration_seconds") or 0.0
        )
        sampling_duration = (
            min(duration, video_duration)
            if duration > 0 and video_duration > 0
            else duration
        )
        scene_times = self._detect_scene_times(
            ffmpeg,
            file_path,
            threshold=scene_threshold,
            timeout_seconds=timeout_seconds,
        )
        timestamps = self._select_timestamps(
            scene_times,
            duration=sampling_duration,
            max_frames=max_frames,
            uniform_interval=uniform_interval,
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        frames_dir = output_dir / "video_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        frame_records: list[dict[str, Any]] = []
        frame_paths: list[Path] = []
        for index, timestamp in enumerate(timestamps, start=1):
            frame_path = (
                frames_dir / f"frame-{index:03d}-{int(timestamp * 1000):010d}.jpg"
            )
            actual_timestamp = self._extract_frame(
                ffmpeg,
                file_path,
                frame_path,
                timestamp=timestamp,
                width=frame_width,
                timeout_seconds=min(timeout_seconds, 60),
            )
            if actual_timestamp != timestamp:
                actual_path = (
                    frames_dir
                    / f"frame-{index:03d}-{int(actual_timestamp * 1000):010d}.jpg"
                )
                frame_path.replace(actual_path)
                frame_path = actual_path
            with Image.open(frame_path) as frame:
                width, height = frame.size
            frame_records.append(
                {
                    "index": index,
                    "timestamp_seconds": round(actual_timestamp, 3),
                    "timestamp": self._format_timestamp(actual_timestamp),
                    "path": str(frame_path.relative_to(output_dir)),
                    "sha256": hashlib.sha256(frame_path.read_bytes()).hexdigest(),
                    "width": width,
                    "height": height,
                }
            )
            frame_paths.append(frame_path)

        storyboard_path = output_dir / f"{source_file_name}.storyboard.jpg"
        self._write_storyboard(frame_records, frame_paths, storyboard_path)
        evidence = {
            "schema_version": "filex.video-evidence.v1",
            "source_file_name": source_file_name,
            "duration_seconds": round(duration, 3),
            "container": probe.get("container"),
            "size_bytes": int(probe.get("size_bytes") or 0),
            "video": probe.get("video") or {},
            "audio": probe.get("audio") or {},
            "has_audio": bool(probe.get("audio")),
            "scene_detection": {
                "method": "ffmpeg_scene_score",
                "threshold": scene_threshold,
                "detected_cut_count": len(scene_times),
                "scene_count": max(1, len(scene_times) + 1),
            },
            "sampling": {
                "strategy": "scene_boundaries_plus_uniform_coverage",
                "max_frames": max_frames,
                "uniform_interval_seconds": uniform_interval,
                "sampled_frame_count": len(frame_records),
            },
            "frames": frame_records,
            "storyboard_path": storyboard_path.name,
        }
        return VideoEvidenceResult(
            evidence=evidence,
            storyboard_path=storyboard_path,
            frame_paths=frame_paths,
        )

    @staticmethod
    def _probe(ffprobe: str, file_path: Path, timeout_seconds: int) -> dict[str, Any]:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration,size,format_name:stream=index,codec_type,codec_name,width,height,avg_frame_rate,sample_rate,channels,duration",
                "-of",
                "json",
                str(file_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        payload = json.loads(completed.stdout)
        streams = list(payload.get("streams") or [])
        video_stream = next(
            (stream for stream in streams if stream.get("codec_type") == "video"), {}
        )
        audio_stream = next(
            (stream for stream in streams if stream.get("codec_type") == "audio"), {}
        )
        format_info = payload.get("format") or {}
        return {
            "duration": float(format_info.get("duration") or 0.0),
            "size_bytes": int(format_info.get("size") or file_path.stat().st_size),
            "container": str(format_info.get("format_name") or ""),
            "video": {
                key: video_stream.get(key)
                for key in ("codec_name", "width", "height", "avg_frame_rate")
                if video_stream.get(key) is not None
            }
            | (
                {"duration_seconds": float(video_stream["duration"])}
                if video_stream.get("duration") is not None
                else {}
            ),
            "audio": {
                key: audio_stream.get(key)
                for key in ("codec_name", "sample_rate", "channels")
                if audio_stream.get(key) is not None
            },
        }

    @staticmethod
    def _detect_scene_times(
        ffmpeg: str,
        file_path: Path,
        *,
        threshold: float,
        timeout_seconds: int,
    ) -> list[float]:
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "info",
                "-i",
                str(file_path),
                "-vf",
                f"select='gt(scene,{threshold})',showinfo",
                "-an",
                "-f",
                "null",
                "-",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return [float(value) for value in _PTS_TIME_RE.findall(completed.stderr)]

    @staticmethod
    def _select_timestamps(
        scene_times: list[float],
        *,
        duration: float,
        max_frames: int,
        uniform_interval: float,
    ) -> list[float]:
        last = max(0.0, duration - 0.1)
        candidates = {0.0, last}
        candidates.update(value for value in scene_times if 0.0 < value < last)
        if duration > uniform_interval:
            candidates.update(
                min(last, index * uniform_interval)
                for index in range(1, math.ceil(duration / uniform_interval))
            )
        ordered: list[float] = []
        for timestamp in sorted(candidates):
            if not ordered or timestamp - ordered[-1] >= 1.0:
                ordered.append(timestamp)
            elif timestamp == last:
                ordered[-1] = timestamp
        if len(ordered) <= max_frames:
            return ordered
        if max_frames == 1:
            return [ordered[len(ordered) // 2]]
        selected_indexes = {
            round(index * (len(ordered) - 1) / (max_frames - 1))
            for index in range(max_frames)
        }
        return [ordered[index] for index in sorted(selected_indexes)]

    @staticmethod
    def _extract_frame(
        ffmpeg: str,
        file_path: Path,
        target: Path,
        *,
        timestamp: float,
        width: int,
        timeout_seconds: int,
    ) -> float:
        attempted: list[float] = []
        for offset in (0.0, 0.5, 1.0, 2.0):
            candidate = max(0.0, timestamp - offset)
            if candidate in attempted:
                continue
            attempted.append(candidate)
            target.unlink(missing_ok=True)
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{candidate:.3f}",
                    "-i",
                    str(file_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={width}:-2",
                    "-q:v",
                    "3",
                    "-y",
                    str(target),
                ],
                check=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
            if target.is_file() and target.stat().st_size > 0:
                return candidate
        raise RuntimeError(
            f"FFmpeg produced no frame near {timestamp:.3f}s; attempted {attempted}"
        )

    @classmethod
    def _write_storyboard(
        cls,
        records: list[dict[str, Any]],
        paths: list[Path],
        target: Path,
    ) -> None:
        columns = min(3, max(1, len(paths)))
        cell_width, cell_height, label_height = 320, 180, 28
        rows = max(1, math.ceil(len(paths) / columns))
        canvas = Image.new(
            "RGB", (columns * cell_width, rows * (cell_height + label_height)), "white"
        )
        draw = ImageDraw.Draw(canvas)
        for index, (record, path) in enumerate(zip(records, paths, strict=True)):
            column, row = index % columns, index // columns
            x, y = column * cell_width, row * (cell_height + label_height)
            with Image.open(path) as frame:
                thumbnail = ImageOps.fit(
                    frame.convert("RGB"), (cell_width, cell_height)
                )
            canvas.paste(thumbnail, (x, y))
            draw.rectangle(
                (x, y + cell_height, x + cell_width, y + cell_height + label_height),
                fill="#111827",
            )
            draw.text(
                (x + 10, y + cell_height + 7),
                f"#{record['index']:02d}  {record['timestamp']}",
                fill="white",
            )
        canvas.save(target, format="JPEG", quality=88, optimize=True)

    @staticmethod
    def _format_timestamp(value: float) -> str:
        total_seconds = max(0, int(round(value)))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        resolved = int(value) if value not in (None, "") else default
        return min(max(resolved, minimum), maximum)

    @staticmethod
    def _bounded_float(
        value: Any, default: float, minimum: float, maximum: float
    ) -> float:
        resolved = float(value) if value not in (None, "") else default
        return min(max(resolved, minimum), maximum)
