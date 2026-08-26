import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from document_parse_service.media_transcription.local_backend import (
    LocalMediaTranscriptionBackend,
)
from document_parse_service.video_evidence import (
    FfmpegVideoEvidenceExtractor,
    VideoEvidenceResult,
)


def test_select_timestamps_combines_scenes_and_uniform_coverage() -> None:
    timestamps = FfmpegVideoEvidenceExtractor._select_timestamps(
        [7.5, 12.1, 45.6, 51.2, 64.7],
        duration=178.0,
        max_frames=8,
        uniform_interval=30.0,
    )
    assert timestamps[0] == 0.0
    assert timestamps[-1] == 177.9
    assert len(timestamps) == 8
    assert timestamps == sorted(timestamps)


def test_local_asr_deployment_env_overrides_bundled_yaml_options(
    tmp_path: Path, monkeypatch
) -> None:
    captured = {}

    class FakeWhisperModel:
        def __init__(self, model_name, *, device, compute_type):
            captured.update(
                model_name=model_name,
                device=device,
                compute_type=compute_type,
            )

        def transcribe(self, _path, **_kwargs):
            return iter(
                [SimpleNamespace(text="offline", start=0.0, end=1.0)]
            ), SimpleNamespace(language="en", language_probability=1.0, duration=1.0)

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel),
    )
    monkeypatch.setenv("FILEX_LOCAL_MEDIA_MODEL", "/opt/filex/models/offline")
    monkeypatch.setenv("FILEX_LOCAL_MEDIA_DEVICE", "cpu")
    monkeypatch.setenv("FILEX_LOCAL_MEDIA_COMPUTE_TYPE", "int8")
    source = tmp_path / "speaking.mp4"
    source.write_bytes(b"video")

    result = LocalMediaTranscriptionBackend()._transcribe_sync(
        source,
        media_type="video",
        file_type="mp4",
        source_file_name="speaking.mp4",
        options={"model": "base", "device": "auto", "compute_type": "default"},
    )

    assert captured == {
        "model_name": "/opt/filex/models/offline",
        "device": "cpu",
        "compute_type": "int8",
    }
    assert result.text == "offline"


def test_extractor_writes_timestamped_frames_storyboard_and_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    def fake_run(command, **_kwargs):
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "codec_name": "h264",
                                "width": 1280,
                                "height": 720,
                                "avg_frame_rate": "30/1",
                                "duration": "64.6",
                            }
                        ],
                        "format": {
                            "duration": "65.0",
                            "size": str(source.stat().st_size),
                            "format_name": "mov,mp4",
                        },
                    }
                ),
                stderr="",
            )
        if "showinfo" in " ".join(command):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="",
                stderr="pts_time:10.0\npts_time:40.0\n",
            )
        target = Path(command[-1])
        Image.new("RGB", (640, 360), "navy").save(target, "JPEG")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    output_dir = tmp_path / "output"
    result = asyncio.run(
        FfmpegVideoEvidenceExtractor().extract(
            source,
            output_dir=output_dir,
            source_file_name="sample",
            options={"video_max_frames": 8, "video_uniform_interval_seconds": 30},
        )
    )

    assert result.evidence["schema_version"] == "filex.video-evidence.v1"
    assert result.evidence["scene_detection"]["scene_count"] == 3
    assert [frame["timestamp_seconds"] for frame in result.evidence["frames"]] == [
        0.0,
        10.0,
        30.0,
        40.0,
        60.0,
        64.5,
    ]
    assert all(path.is_file() for path in result.frame_paths)
    assert result.storyboard_path.is_file()
    with Image.open(result.storyboard_path) as storyboard:
        assert storyboard.width == 960
        assert storyboard.height == 416


def test_silent_video_skips_transcription_model(tmp_path: Path, monkeypatch) -> None:
    import document_parse_service.base_document_service as base_module
    import document_parse_service.media_document_service as media_module

    output_root = tmp_path / "document_parse"
    monkeypatch.setattr(base_module, "DOCUMENT_PARSE_WORKSPACE", output_root)
    monkeypatch.setattr(media_module, "DOCUMENT_PARSE_WORKSPACE", output_root)
    source = tmp_path / "silent.mp4"
    source.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    class SilentEvidenceExtractor:
        async def extract(
            self, file_path, *, output_dir, source_file_name, options=None
        ):
            output_dir.mkdir(parents=True, exist_ok=True)
            frame_dir = output_dir / "video_frames"
            frame_dir.mkdir()
            frame = frame_dir / "frame-001.jpg"
            storyboard = output_dir / f"{source_file_name}.storyboard.jpg"
            Image.new("RGB", (32, 18), "black").save(frame, "JPEG")
            Image.new("RGB", (32, 18), "black").save(storyboard, "JPEG")
            return VideoEvidenceResult(
                evidence={
                    "schema_version": "filex.video-evidence.v1",
                    "duration_seconds": 5.0,
                    "has_audio": False,
                    "video": {"width": 32, "height": 18, "codec_name": "h264"},
                    "audio": {},
                    "scene_detection": {"scene_count": 1},
                    "frames": [
                        {
                            "index": 1,
                            "timestamp": "00:00",
                            "timestamp_seconds": 0.0,
                            "path": "video_frames/frame-001.jpg",
                        }
                    ],
                },
                storyboard_path=storyboard,
                frame_paths=[frame],
            )

    class BackendThatMustNotLoad:
        name = "local"

        async def transcribe(self, *args, **kwargs):
            raise AssertionError("silent video must not load the ASR model")

    service = media_module.VideoDocumentService(
        file_type="mp4",
        backend=BackendThatMustNotLoad(),
        backend_options={},
        video_evidence_extractor=SilentEvidenceExtractor(),
    )
    output = asyncio.run(
        service.parse_to_markdown(
            source,
            task_id="silent-video",
            source_file_name="silent",
        )
    )

    assert "视频没有可转写的音轨" in output.read_text(encoding="utf-8")
    assert output.with_suffix(".storyboard.jpg").is_file()
    assert output.with_suffix(".evidence.json").is_file()


def test_extract_frame_retries_earlier_when_ffmpeg_tail_is_empty(
    tmp_path: Path, monkeypatch
) -> None:
    attempted = []
    target = tmp_path / "tail.jpg"

    def fake_run(command, **_kwargs):
        timestamp = float(command[command.index("-ss") + 1])
        attempted.append(timestamp)
        if timestamp <= 9.5:
            Image.new("RGB", (32, 18), "navy").save(target, "JPEG")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    actual = FfmpegVideoEvidenceExtractor._extract_frame(
        "ffmpeg",
        tmp_path / "sample.mp4",
        target,
        timestamp=10.0,
        width=640,
        timeout_seconds=10,
    )

    assert attempted == [10.0, 9.5]
    assert actual == 9.5
    assert target.is_file()
