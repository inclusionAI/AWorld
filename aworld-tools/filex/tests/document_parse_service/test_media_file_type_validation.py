import asyncio
import sys
import tempfile
from pathlib import Path


def _add_src_path() -> None:
    src_path = Path(__file__).resolve().parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def test_verify_file_type_accepts_common_media_headers() -> None:
    _add_src_path()
    from utils.file_utils import verify_file_type

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        mp3_path = tmp_path / "demo.mp3"
        mp4_path = tmp_path / "demo.mp4"
        wav_path = tmp_path / "demo.wav"
        png_path = tmp_path / "demo.png"
        mp3_path.write_bytes(b"ID3demo")
        mp4_path.write_bytes(b"\x00\x00\x00\x18ftypmp42demo")
        wav_path.write_bytes(b"RIFF\x24\x00\x00\x00WAVEdemo")
        png_path.write_bytes(b"\x89PNG\r\n\x1a\npng")

        assert asyncio.run(verify_file_type(mp3_path, "mp3"))
        assert asyncio.run(verify_file_type(mp4_path, "mp4"))
        assert asyncio.run(verify_file_type(wav_path, "wav"))
        assert asyncio.run(verify_file_type(png_path, "png"))
